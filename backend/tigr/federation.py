"""Bounded, process-local execution for reviewed relational federation plans.

This module deliberately stages rows only in worker memory. It accepts the
server-validated source reads produced by ``source_to_data`` and never accepts
SQL or object identifiers directly from an API request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from tigr.scenario_generation import _json_safe, _read_mysql_rows, _read_postgres_rows

MAX_SOURCE_ROWS = 10_000
MAX_JOIN_ROWS = 10_000
MAX_STAGED_BYTES = 32 * 1024 * 1024


class FederationLimitError(ValueError):
    """A federated plan exceeded a configured hard execution bound."""


def _encoded_size(rows: list[dict[str, Any]]) -> int:
    return len(json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def execute_federated_reads(
    connections: dict[str, Any],
    validated_plan: dict[str, Any],
    joins: list[dict[str, Any]],
    *,
    row_limit: int,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read projected rows and hash-join them under hard row/byte limits.

    Output names are deterministic ``<alias>__<field>`` labels, so duplicate
    field names from separate bindings remain unambiguous.
    """
    if not 1 <= row_limit <= MAX_SOURCE_ROWS:
        raise FederationLimitError("row_limit_out_of_range")
    join_row_limit = min(MAX_JOIN_ROWS, row_limit)
    by_alias: dict[str, list[dict[str, Any]]] = {}
    source_columns: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    source_order: list[str] = []
    for read in validated_plan.get("sources", []):
        if cancelled and cancelled():
            raise InterruptedError("job_cancelled")
        alias = str(read["alias"])
        connection = connections.get(alias)
        if connection is None:
            raise ValueError("federated_source_unavailable")
        query = re.sub(r"\s+LIMIT\s+\d+\s*$", f" LIMIT {row_limit}", str(read["read_template"]), flags=re.IGNORECASE)
        if query == read["read_template"]:
            raise ValueError("federated_read_template_invalid")
        compiled = {"query": query, "parameters": {}, "database": read["database"]}
        if connection.connector_type == "mysql":
            source_rows = _read_mysql_rows(connection, compiled)
        elif connection.connector_type == "postgresql":
            source_rows = _read_postgres_rows(connection, None, compiled)
        else:
            raise ValueError("unsupported_federated_connector")
        if len(source_rows) > row_limit:
            raise FederationLimitError("source_row_limit_exceeded")
        counts[alias] = len(source_rows)
        source_order.append(alias)
        source_columns[alias] = [str(field) for field in read["fields"]]
        by_alias[alias] = [
            {f"{alias}__{field}": _json_safe(row.get(field)) for field in read["fields"]}
            for row in source_rows
        ]
        if sum(_encoded_size(rows) for rows in by_alias.values()) > MAX_STAGED_BYTES:
            raise FederationLimitError("staging_byte_limit_exceeded")

    if not source_order:
        raise ValueError("federated_plan_has_no_sources")
    joined = by_alias[source_order[0]]
    joined_aliases = {source_order[0]}
    pending_edges = list(joins)
    while pending_edges:
        if cancelled and cancelled():
            raise InterruptedError("job_cancelled")
        edge_index = next((index for index, item in enumerate(pending_edges)
                           if (item.get("left_alias") in joined_aliases) != (item.get("right_alias") in joined_aliases)), None)
        if edge_index is None:
            raise ValueError("federated_join_order_invalid")
        edge = pending_edges.pop(edge_index)
        left_alias, right_alias = str(edge["left_alias"]), str(edge["right_alias"])
        # Keep the current joined relation on the left. A reviewed edge may
        # name either endpoint first, so normalize its orientation here.
        if right_alias in joined_aliases:
            left_alias, right_alias = right_alias, left_alias
            left_field, right_field = str(edge["right_field"]), str(edge["left_field"])
        else:
            left_field, right_field = str(edge["left_field"]), str(edge["right_field"])
        if right_alias not in by_alias:
            raise ValueError("federated_join_source_missing")
        right_rows = by_alias[right_alias]
        left_key, right_key = f"{left_alias}__{left_field}", f"{right_alias}__{right_field}"
        index: dict[Any, list[dict[str, Any]]] = {}
        for row in right_rows:
            value = row.get(right_key)
            if value is not None:
                index.setdefault(value, []).append(row)
        output: list[dict[str, Any]] = []
        is_left = edge.get("join_type") == "left"
        for left_row in joined:
            value = left_row.get(left_key)
            matches = index.get(value, []) if value is not None else []
            if matches:
                for right_row in matches:
                    output.append({**left_row, **right_row})
                    if len(output) > join_row_limit:
                        raise FederationLimitError("join_output_row_limit_exceeded")
            elif is_left:
                output.append({**left_row, **{f"{right_alias}__{field}": None for field in source_columns[right_alias]}})
        joined = output
        joined_aliases.add(right_alias)
        if _encoded_size(joined) > MAX_STAGED_BYTES:
            raise FederationLimitError("staging_byte_limit_exceeded")
    if joined_aliases != set(source_order):
        raise ValueError("federated_plan_is_disconnected")
    return joined, counts
