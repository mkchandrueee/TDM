import argparse
import csv
import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tigr.access import record_audit
from tigr.connection_models import WorkspaceConnection
from tigr.connection_testing import test_file
from tigr.datasets import DataSet, DataSetVersion
from tigr.dataset_schemas import DraftDataSet, LineageEventCreate, Provenance, SchemaField
from tigr.dataset_services import (
    append_lineage_event,
    create_initial_version,
    create_version,
    record_generated_artifact,
    record_status_change,
    record_validation,
    store_dataset_records,
)
from tigr.db import SessionLocal
from tigr.iam import PlatformAdmin, UserIdentity, Workspace, WorkspaceMembership
from tigr.identity import Principal
from tigr.jobs import Job  # noqa: F401 - registers the job table for lineage foreign keys
from tigr.schema_records import (
    ArtifactRecord,
    ConnectionTestRun,
    DatasetPublication,
    DataSetRecord,
)
from tigr.source_data import SourceAccount, SourceBranch, SourceCustomer, SourceLoan

SEED_ACTOR = "tigr-banking-seed"
WORKSPACE_SLUG = "customer-accounts"
DATASET_NAME = "Customers with NRI and Loan Accounts"


def bootstrap_admin(subject: str) -> None:
    with SessionLocal.begin() as db:
        user = db.get(UserIdentity, subject)
        if user is None:
            db.add(UserIdentity(subject=subject))
        if db.scalar(select(PlatformAdmin.subject).where(PlatformAdmin.subject == subject)):
            print("The identity is already a platform Admin.")
            return
        db.add(PlatformAdmin(subject=subject, granted_by="bootstrap-cli"))
        record_audit(
            db,
            Principal(subject="bootstrap-cli", display_name="Local administrator bootstrap", actor_type="service"),
            "bootstrap-cli",
            "identity.platform_admin_bootstrapped",
            "platform_admin",
            subject,
            None,
            None,
            {"subject": subject},
        )
    print("Platform Admin access granted. The identity must sign in through the configured OIDC provider.")


def import_csv_table(db, model, file_path: str, *, date_fields: set[str] = frozenset(),
                     integer_fields: set[str] = frozenset(), numeric_fields: set[str] = frozenset()) -> int:
    """Upsert a repository CSV into its normalized TIGR PostgreSQL source table."""
    root = Path(__file__).resolve().parents[2]
    path = (root / "docs" / "source_data" / file_path).resolve()
    if root not in path.parents or not path.is_file():
        raise RuntimeError(f"Banking CSV is not available in the repository source_data folder: {file_path}")
    primary_key = model.__mapper__.primary_key[0].name
    base_insert = pg_insert(model)
    update_columns = {column.name: getattr(base_insert.excluded, column.name)
                      for column in model.__table__.columns if column.name != primary_key}
    statement = base_insert.on_conflict_do_update(index_elements=[primary_key], set_=update_columns)
    count = 0
    batch = []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for source_row in csv.DictReader(source):
            row = dict(source_row)
            for field in date_fields:
                row[field] = date.fromisoformat(row[field])
            for field in integer_fields:
                row[field] = int(row[field])
            for field in numeric_fields:
                row[field] = Decimal(row[field])
            batch.append(row)
            if len(batch) >= 1000:
                db.execute(statement, batch)
                count += len(batch)
                batch.clear()
    if batch:
        db.execute(statement, batch)
        count += len(batch)
    return count


def import_banking_sources(db) -> dict[str, int]:
    return {
        "branches": import_csv_table(db, SourceBranch, "branches.csv", date_fields={"opened_date"}, integer_fields={"branch_id"}),
        "customers": import_csv_table(db, SourceCustomer, "customers.csv", date_fields={"date_of_birth", "join_date"}, integer_fields={"customer_id", "credit_score"}, numeric_fields={"annual_income"}),
        "accounts": import_csv_table(db, SourceAccount, "accounts.csv", date_fields={"open_date"}, integer_fields={"account_id", "customer_id", "branch_id"}, numeric_fields={"balance"}),
        "loans": import_csv_table(db, SourceLoan, "loans.csv", date_fields={"start_date"}, integer_fields={"loan_id", "customer_id", "branch_id", "term_months"}, numeric_fields={"loan_amount", "interest_rate"}),
    }


def linked_nri_loan_rows(db) -> list[dict]:
    rows = db.execute(
        select(
            SourceCustomer.customer_id.label("customer_id"),
            SourceAccount.account_id.label("account_id"),
            SourceAccount.account_type.label("account_type"),
            SourceAccount.balance.label("account_balance"),
            SourceAccount.status.label("account_status"),
            SourceLoan.loan_id.label("loan_id"),
            SourceLoan.loan_type.label("loan_type"),
            SourceLoan.loan_amount.label("loan_amount"),
            SourceLoan.interest_rate.label("interest_rate"),
            SourceLoan.term_months.label("loan_term_months"),
            SourceLoan.status.label("loan_status"),
        )
        .join(SourceAccount, SourceAccount.customer_id == SourceCustomer.customer_id)
        .join(SourceLoan, SourceLoan.customer_id == SourceCustomer.customer_id)
        .where(func.upper(SourceAccount.account_type) == "NRI")
        .order_by(SourceCustomer.customer_id, SourceAccount.account_id, SourceLoan.loan_id)
    ).mappings().all()
    return [{key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()} for row in rows]


def seed_sample() -> None:
    """Create or refresh the linked banking sample in PostgreSQL."""
    now = datetime.now(UTC)
    fields = [
        ("customer_id", "integer", True),
        ("account_id", "integer", True), ("account_type", "varchar", True), ("account_balance", "numeric", True),
        ("account_status", "varchar", True), ("loan_id", "integer", True), ("loan_type", "varchar", True),
        ("loan_amount", "numeric", True), ("interest_rate", "numeric", True),
        ("loan_term_months", "integer", True), ("loan_status", "varchar", True),
    ]
    rows: list[dict] = []
    digest = ""
    reference = ""
    source_files = {
        "Banking branches CSV source": "branches.csv",
        "Banking customer CSV source": "customers.csv",
        "Banking accounts CSV source": "accounts.csv",
        "Banking loans CSV source": "loans.csv",
    }
    with SessionLocal.begin() as db:
        source_counts = import_banking_sources(db)
    with SessionLocal.begin() as db:
        db.execute(text("ANALYZE source_data.branches, source_data.customers, source_data.accounts, source_data.loans"))
        rows = linked_nri_loan_rows(db)
        if not rows:
            raise RuntimeError("No customers with NRI accounts and loans were found in the imported CSV tables")
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        workspace = db.scalar(select(Workspace).where(Workspace.slug == WORKSPACE_SLUG))
        if workspace is None:
            legacy_workspace = db.scalar(select(Workspace).where(Workspace.slug == "tigr-sample"))
            if legacy_workspace is not None:
                legacy_workspace.status = "inactive"
                legacy_dataset = db.scalar(select(DataSet).where(
                    DataSet.workspace_id == legacy_workspace.id,
                    DataSet.name == "Sample Account Test Data",
                ))
                if legacy_dataset is not None:
                    legacy_dataset.name = "Retired generic sample data"
                    legacy_versions = db.scalars(select(DataSetVersion).where(DataSetVersion.dataset_id == legacy_dataset.id)).all()
                    for legacy_version in legacy_versions:
                        db.execute(DataSetRecord.__table__.delete().where(DataSetRecord.version_id == legacy_version.id))
                        legacy_artifacts = db.scalars(select(ArtifactRecord).where(
                            ArtifactRecord.dataset_id == legacy_dataset.id,
                            ArtifactRecord.version_id == legacy_version.id,
                        )).all()
                        for legacy_artifact in legacy_artifacts:
                            legacy_artifact.status = "deleted"
                            legacy_artifact.deleted_at = now
            workspace = Workspace(
                id=uuid4(), slug=WORKSPACE_SLUG, name="Customer and Accounts",
                purpose="Banking test data workspace for customer, NRI account, and loan scenarios.",
                status="active", created_by=SEED_ACTOR,
            )
            db.add(workspace)
            db.flush()
        else:
            workspace.slug = WORKSPACE_SLUG
            workspace.name = "Customer and Accounts"
            workspace.purpose = "Banking test data workspace for customer, NRI account, and loan scenarios."
            workspace.status = "active"
        workspace_id = workspace.id

        personas = [
            ("fathima", "Fathima", "Admin"),
            ("abdul", "Abdul", "Test Data Engineer"),
            ("john", "John", "QA Engineer"),
        ]
        for subject, name, role in personas:
            user = db.get(UserIdentity, subject)
            if user is None:
                db.add(UserIdentity(subject=subject, display_name=name))
            else:
                user.display_name = name
            membership = db.get(WorkspaceMembership, (workspace_id, subject))
            if membership is None:
                db.add(WorkspaceMembership(workspace_id=workspace_id, subject=subject, role=role, granted_by=SEED_ACTOR))
            else:
                membership.role = role
                membership.valid_from = min(membership.valid_from, now)
                membership.valid_until = None
                membership.revoked_at = None
                membership.revoked_by = None

        source_connections = {}
        for name, file_path in source_files.items():
            connection = db.scalar(select(WorkspaceConnection).where(
                WorkspaceConnection.workspace_id == workspace_id,
                WorkspaceConnection.name == name,
            ))
            if connection is None:
                connection = WorkspaceConnection(
                    id=uuid4(), workspace_id=workspace_id, name=name,
                    connector_type="csv", direction="source", environment="Development",
                    purpose="Read-only banking source file for linked test data generation.", owner="abdul",
                    operation_scope="read_only", connector_config={},
                    created_by=SEED_ACTOR, updated_by=SEED_ACTOR,
                )
                db.add(connection)
                db.flush()
            connection.connector_type = "csv"
            connection.direction = "source"
            connection.environment = "Development"
            connection.owner = "abdul"
            connection.operation_scope = "read_only"
            connection.connector_config = {"file_path": file_path, "delimiter": ",", "has_header": True, "encoding": "utf-8-sig"}
            connection_test = test_file(connection)
            if not connection_test.succeeded or connection_test.structure is None:
                raise RuntimeError(f"Unable to validate configured banking source {file_path}")
            connection.status = "enabled"
            connection.tested_at = now
            connection.tested_by = "abdul"
            connection.test_result = {"succeeded": True, "message": connection_test.message}
            connection.structure_preview = connection_test.structure
            connection.updated_by = "abdul"
            source_connections[file_path] = connection
            test_run = db.scalar(select(ConnectionTestRun).where(
                ConnectionTestRun.connection_id == connection.id,
                ConnectionTestRun.correlation_id == "banking-sample-seed",
            ))
            if test_run is None:
                db.add(ConnectionTestRun(
                    id=uuid4(), workspace_id=workspace_id, connection_id=connection.id, requested_by="abdul",
                    started_at=now, finished_at=now, outcome="succeeded",
                    safe_message="CSV structure was tested; source rows are persisted in the TIGR PostgreSQL source tables.",
                    correlation_id="banking-sample-seed",
                ))

        dataset = db.scalar(select(DataSet).where(
            DataSet.workspace_id == workspace_id, DataSet.name == DATASET_NAME,
        ))
        created_dataset = dataset is None
        if dataset is None:
            dataset = DataSet(
                id=uuid4(), workspace_id=workspace_id, name=DATASET_NAME,
                description="Customer rows joined to NRI accounts and loans from PostgreSQL source tables.",
                owner_id="abdul", creation_pathway="source_to_data", latest_version_number=1,
            )
            db.add(dataset)
            db.flush()
        dataset.description = "Customer records joined to NRI accounts and loans using customer keys in PostgreSQL source tables."
        dataset.owner_id = "abdul"
        dataset.creation_pathway = "source_to_data"
        dataset_id = dataset.id
        current = None if created_dataset else db.scalar(select(DataSetVersion).where(
            DataSetVersion.dataset_id == dataset.id,
            DataSetVersion.version_number == dataset.latest_version_number,
        ))
        if current and current.validation_evidence.get("hash") == digest:
            print("The linked NRI banking dataset is already current in PostgreSQL.")
            return

        field_specs = [
            ("customer_id", "integer"), ("account_id", "integer"), ("account_type", "varchar"),
            ("account_balance", "numeric"), ("account_status", "varchar"), ("loan_id", "integer"),
            ("loan_type", "varchar"), ("loan_amount", "numeric"), ("interest_rate", "numeric"),
            ("loan_term_months", "integer"), ("loan_status", "varchar"),
        ]
        schema_fields = [SchemaField(field=name, data_type=kind, required=True) for name, kind in field_specs]
        selected_fields = [name for name, _ in field_specs]
        actor = Principal(subject="abdul", display_name="Abdul", actor_type="human")
        customer_connection = source_connections["customers.csv"]
        draft = DraftDataSet(
            name=DATASET_NAME,
            description=dataset.description,
            creation_pathway="source_to_data",
            schema_scope=schema_fields,
            selected_fields=selected_fields,
            provenance=Provenance(
                origin="source_backed",
                source_connection_id=customer_connection.id,
                source_object="customers, accounts, and loans in TIGR PostgreSQL source tables",
                source_schema_version="banking-source-schema-v1",
                mapping_version="customer-account-loan-join-v1",
            ),
            permitted_use=["testing"],
        )
        version = create_initial_version(db, dataset, draft, actor, "banking-sample-seed") if created_dataset else create_version(db, dataset, draft, actor, "banking-sample-seed")
        version_id = version.id
        version_number = version.version_number
        reference = f"artifact://{dataset_id}/v{version_number}/records"
        digest, stored_size = store_dataset_records(db, dataset, version, rows)
        version.privacy_state = "compliant"
        version.validation_state = "passed"
        version.lifecycle_state = "ready"
        version.record_count = len(rows)
        version.validation_evidence = {
            "summary": "Customer, NRI, and loan rows were joined from PostgreSQL source tables.",
            "artifact_ref": reference, "hash": digest, "record_count": len(rows),
            "field_count": len(schema_fields), "validation_version": "banking-source-v1",
            "format": "json", "source_table_counts": json.dumps(source_counts, sort_keys=True),
        }
        record_status_change(db, dataset, version, "draft", "ready", actor,
                             "banking-sample-seed", "Source rows validated and privacy-reviewed")
        record_validation(db, dataset, version, "banking-sample-seed", "banking_source_schema_relationships")
        record_generated_artifact(db, dataset, version, stored_size, reference, "application/json", digest)
        version.lifecycle_state = "published"
        version.publication_state = "published"
        version.published_at = now
        record_status_change(db, dataset, version, "ready", "published", actor,
                             "banking-sample-seed", "Version published after validation and privacy checks")
        db.add(DatasetPublication(
            id=uuid4(), workspace_id=workspace_id, dataset_id=dataset_id, version_id=version_id,
            published_by=actor.subject, published_at=now, state="published",
            readiness_snapshot={"validation_state": version.validation_state, "privacy_state": version.privacy_state,
                                "record_count": version.record_count, "permitted_use": version.permitted_use},
        ))
        append_lineage_event(db, dataset, version, LineageEventCreate(
            event_type="banking_source_generation_completed", stage="generation",
            source_reference=str(customer_connection.id), artifact_reference=reference,
            mapping_version="customer-account-loan-join-v1", validation_version="banking-source-v1",
            safe_evidence={"record_count": len(rows), "field_count": len(schema_fields), "hash": digest,
                           "summary": "Customer, NRI account, and loan rows joined from source tables"},
        ), actor)
        dataset.updated_at = now
        append_lineage_event(db, dataset, version, LineageEventCreate(
            event_type="published", stage="publication",
            safe_evidence={"summary": "Version published after validation and privacy checks"},
        ), actor)

        record_audit(
            db, Principal(subject=SEED_ACTOR, actor_type="service"), "banking-sample-seed",
            "sample.banking_dataset_seeded", "dataset", str(dataset_id), workspace_id, None,
            {"record_count": len(rows), "source_table_counts": source_counts, "storage": "postgresql"},
        )
    print("Created or refreshed the Customer and Accounts banking sample in PostgreSQL.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="tigr-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin", help="Grant initial platform Admin to an OIDC subject")
    bootstrap.add_argument("subject", help="The exact OIDC subject (sub claim) from your identity provider")
    commands.add_parser("seed-sample", help="Create or refresh the linked banking sample in PostgreSQL")
    args = parser.parse_args()
    if args.command == "bootstrap-admin":
        bootstrap_admin(args.subject)
    elif args.command == "seed-sample":
        seed_sample()


if __name__ == "__main__":
    main()
