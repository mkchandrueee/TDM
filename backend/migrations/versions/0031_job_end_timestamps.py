"""Track terminal job end times for the Jobs inventory."""

import sqlalchemy as sa
from alembic import op

revision = "0031_job_end_timestamps"
down_revision = "0030_same_actor_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True), schema="dataops")
    op.execute(
        "UPDATE dataops.jobs SET ended_at = heartbeat_at "
        "WHERE status IN ('succeeded', 'succeeded_with_warnings', 'failed', 'cancelled', 'expired', 'archived')"
    )
    op.execute(
        """
        CREATE FUNCTION dataops.set_job_ended_at() RETURNS trigger AS $$
        BEGIN
            IF NEW.status IN ('succeeded', 'succeeded_with_warnings', 'failed', 'cancelled', 'expired', 'archived') THEN
                IF TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status OR NEW.ended_at IS NULL THEN
                    NEW.ended_at := now();
                END IF;
            ELSE
                NEW.ended_at := NULL;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_jobs_ended_at
        BEFORE INSERT OR UPDATE OF status ON dataops.jobs
        FOR EACH ROW EXECUTE FUNCTION dataops.set_job_ended_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_jobs_ended_at ON dataops.jobs")
    op.execute("DROP FUNCTION IF EXISTS dataops.set_job_ended_at()")
    op.drop_column("jobs", "ended_at", schema="dataops")
