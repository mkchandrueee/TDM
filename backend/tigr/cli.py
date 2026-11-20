import argparse
import json

from sqlalchemy import select

from tigr.access import record_audit
from tigr.db import SessionLocal
from tigr.entity_lock_operations import reconcile_entity_locks
from tigr.iam import PlatformAdmin, UserIdentity
from tigr.identity import Principal


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="tigr-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin", help="Grant initial platform Admin to an OIDC subject")
    bootstrap.add_argument("subject", help="The exact OIDC subject (sub claim) from your identity provider")
    commands.add_parser("entity-lock-reconcile", help="Close stale claims and print aggregate rollout health counts")
    args = parser.parse_args()
    if args.command == "bootstrap-admin":
        bootstrap_admin(args.subject)
    elif args.command == "entity-lock-reconcile":
        with SessionLocal.begin() as db:
            report = reconcile_entity_locks(db)
            record_audit(db, Principal(subject="tigr-entity-lock-reconciler", actor_type="service"),
                         "entity-lock-reconcile", "entity_lock.reconciled", "entity_claim_registry",
                         "aggregate", None, None, report)
        print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
