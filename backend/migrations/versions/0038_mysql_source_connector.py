"""Allow MySQL as a workspace source connector."""
from alembic import op

revision = "0038_mysql_source_connector"
down_revision = "0037_workspace_pii_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_workspace_connections_type", "workspace_connections", schema="dataops", type_="check")
    op.create_check_constraint(
        "ck_workspace_connections_type", "workspace_connections",
        "connector_type IN ('postgresql', 'mysql', 'json', 'csv', 'delimited_text')", schema="dataops",
    )


def downgrade() -> None:
    op.drop_constraint("ck_workspace_connections_type", "workspace_connections", schema="dataops", type_="check")
    op.create_check_constraint(
        "ck_workspace_connections_type", "workspace_connections",
        "connector_type IN ('postgresql', 'json', 'csv', 'delimited_text')", schema="dataops",
    )
