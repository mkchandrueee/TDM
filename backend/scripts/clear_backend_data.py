"""Clear TIGR operational data while preserving logins and source connections.

By default this prints the tables and row counts that would be cleared.
Pass --execute to perform the cleanup. This only connects to the configured
TIGR application database; it never follows connection secrets to source DBs.
"""

from __future__ import annotations

import argparse

from sqlalchemy import text

from tigr.db import engine


PRESERVED_TABLES = {
    ("dataops", "workspace_connections"),
    # Keep identity and workspace scaffolding so preserved connections remain usable.
    ("iam", "users"),
    ("iam", "workspaces"),
    ("iam", "workspace_memberships"),
    ("iam", "access_grants"),
    ("iam", "platform_admins"),
    ("iam", "service_identities"),
}
CLEAR_SCHEMAS = ("dataops", "audit")
DATASET_TABLES = {
    ("dataops", name)
    for name in (
        "artifact_records",
        "consumption_events",
        "data_use_requests",
        "dataset_fields",
        "dataset_lineage_edges",
        "dataset_lineage_events",
        "dataset_locks",
        "dataset_publications",
        "dataset_records",
        "dataset_reservations",
        "dataset_status_history",
        "dataset_versions",
        "export_artifacts",
        "masking_runs",
        "pii_findings",
        "provisioning_runs",
        "validation_results",
        "validation_runs",
    )
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the cleanup; without this flag the script only reports counts",
    )
    parser.add_argument(
        "--datasets-only",
        action="store_true",
        help="clear data sets and dependent records while preserving jobs, plans, mappings, and connections",
    )
    args = parser.parse_args()

    with engine.begin() as connection:
        tables = connection.execute(
            text(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema = ANY(:schemas)
                ORDER BY table_schema, table_name
                """
            ),
            {"schemas": list(CLEAR_SCHEMAS)},
        ).all()
        clear_tables = [
            (schema, table)
            for schema, table in tables
            if (schema, table) not in PRESERVED_TABLES
            and (not args.datasets_only or (schema, table) in DATASET_TABLES or (schema, table) == ("dataops", "datasets"))
        ]

        counts = []
        for schema, table in clear_tables:
            count = connection.execute(
                text(f'SELECT count(*) FROM "{schema}"."{table}"')
            ).scalar_one()
            counts.append((schema, table, count))

        print("Preserved: dataops.workspace_connections and IAM users/workspaces/access")
        if args.datasets_only:
            print("Scope: data sets and dependent records only; jobs, saved plans, mappings, connections, and audit records stay intact")
        print("Database scope: configured TIGR application database only")
        print("Records selected for cleanup:")
        for schema, table, count in counts:
            print(f"  {schema}.{table}: {count}")
        print(f"Total: {sum(count for _, _, count in counts)}")

        if not args.execute:
            print("Dry run only. Re-run with --execute to clear these tables.")
            return

        truncate_tables = [
            (schema, table) for schema, table in clear_tables
            if not (args.datasets_only and (schema, table) == ("dataops", "datasets"))
        ]
        if truncate_tables:
            qualified = ", ".join(f'"{schema}"."{table}"' for schema, table in truncate_tables)
            connection.exec_driver_sql(f"TRUNCATE TABLE {qualified}")
        if args.datasets_only:
            connection.exec_driver_sql('DELETE FROM "dataops"."datasets"')
        print("Cleanup completed.")


if __name__ == "__main__":
    main()
