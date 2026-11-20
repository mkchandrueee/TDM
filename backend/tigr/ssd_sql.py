"""Fail-closed SQL grammar for the transactional database connectors supported by SSD."""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

SAFE_FUNCTIONS = {
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "COALESCE",
    "NULLIF",
    "LOWER",
    "UPPER",
    "LENGTH",
    "ABS",
    "ROUND",
    "CAST",
    "TRIM",
    "SUBSTRING",
    "SUBSTR",
    "IFNULL",
    "ISNULL",
    "NVL",
    "LEN",
    "CONVERT",
    "DATEADD",
    "DATEDIFF",
    "TO_CHAR",
    "TO_DATE",
    "TO_NUMBER",
    "IIF",
    "EXISTS",
}


CONNECTOR_DIALECTS = {
    "postgresql": "postgres",
    "mysql": "mysql",
    "oracle": "oracle",
    "mssql": "tsql",
}


def inspect_sql(
    sql: str,
    parameters: dict,
    *,
    connector_type: str = "postgresql",
    allow_database_select_functions: bool = False,
) -> dict:
    # Parser fallback warnings must never log SQL literals or parameter text.
    dialect = CONNECTOR_DIALECTS.get(connector_type)
    if dialect is None:
        raise ValueError("SSD_UNSUPPORTED_CONNECTION: SSD SQL requires a supported relational database")
    statements = sqlglot.parse(sql, read=dialect, error_message_context=0)
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("SSD_UNSUPPORTED_STATEMENT: Supply exactly one statement")
    root = statements[0]
    if type(root) not in (exp.Select, exp.Union, exp.Insert, exp.Update, exp.Delete):
        raise ValueError("SSD_UNSUPPORTED_STATEMENT: SELECT, INSERT, UPDATE and DELETE only")
    operation = "select" if isinstance(root, (exp.Select, exp.Union)) else root.key
    forbidden = {
        "command",
        "create",
        "drop",
        "alter",
        "merge",
        "transaction",
        "commit",
        "rollback",
        "into",
        "lock",
        "copy",
        "execute",
        "grant",
        "revoke",
        "onconflict",
        "conflict",
        "duplicatekeyupdate",
        "output",
        "hint",
        "operator",
    }
    for node in root.walk():
        if node.key in forbidden or (
            isinstance(node, (exp.Insert, exp.Update, exp.Delete)) and node is not root
        ):
            raise ValueError(
                "SSD_UNSUPPORTED_STATEMENT: Scripts, locking reads and nested mutations are forbidden"
            )
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name()
            database_validated_select_function = (
                operation == "select" and allow_database_select_functions
            )
            if not database_validated_select_function and (
                name not in SAFE_FUNCTIONS or isinstance(node.parent, exp.Dot)
            ):
                raise ValueError(
                    "SSD_UNSUPPORTED_STATEMENT: Function is not in the safe SQL subset"
                )
        if isinstance(node, exp.DataType) and node.this == exp.DataType.Type.USERDEFINED:
            raise ValueError(
                "SSD_UNSUPPORTED_STATEMENT: User-defined type conversion is not supported"
            )
    tables = [
        source
        for scope in traverse_scope(root)
        for source in scope.sources.values()
        if isinstance(source, exp.Table)
    ]
    if operation != "select":
        tables.append(root.this.this if isinstance(root.this, exp.Schema) else root.this)
    objects = set()
    for table in tables:
        if not table.db or table.catalog or not isinstance(table.this, exp.Identifier):
            raise ValueError(
                "SSD_UNSUPPORTED_STATEMENT: Use schema-qualified tables in the selected database"
            )
        schema_name = table.db.casefold()
        protected_schemas = {
            "postgresql": {"information_schema", "iam", "dataops", "audit"},
            "mysql": {"information_schema", "mysql", "performance_schema", "sys"},
            "oracle": {"sys", "system", "xdb", "ctxsys", "outln", "dbsnmp"},
            "mssql": {"information_schema", "sys"},
        }[connector_type]
        if (connector_type == "postgresql" and schema_name.startswith("pg_")) or schema_name in protected_schemas:
            raise ValueError("SSD_OPERATION_NOT_ALLOWED: System schemas are not source objects")
        objects.add((table.db, table.name))
    if not objects:
        raise ValueError("SSD_UNSUPPORTED_STATEMENT: At least one source table is required")
    names = {node.name for node in root.find_all(exp.Placeholder)}
    if names != set(parameters) or any(
        not isinstance(v, (str, int, float, bool, type(None))) for v in parameters.values()
    ):
        raise ValueError(
            "SSD_UNSUPPORTED_STATEMENT: Bind scalar values to exactly the named parameters"
        )
    required = {operation}
    if operation != "select":
        target = root.this.this if isinstance(root.this, exp.Schema) else root.this
        if not isinstance(target, exp.Table) or not target.db:
            raise ValueError(
                "SSD_UNSUPPORTED_STATEMENT: A mutation must target one qualified table"
            )
        if (
            root.args.get("returning")
            or root.args.get("where")
            or list(root.find_all(exp.Select))
            or any(
                not (
                    isinstance(col.parent, exp.EQ)
                    and col.parent.this is col
                    and col.parent in (root.args.get("expressions") or [])
                )
                for col in root.find_all(exp.Column)
            )
        ):
            required.add("select")
        if root.args.get("using") or root.args.get("from_") or root.args.get("joins"):
            raise ValueError("SSD_UNSUPPORTED_STATEMENT: Multi-table mutations are not supported")
    return {
        "operation": operation,
        "operations": sorted(required),
        "objects": [list(o) for o in sorted(objects)],
        "columns": sorted({column.name for column in root.find_all(exp.Column)}),
    }
