# TIGR entity locking rollout and recovery

## Scope and defaults

Entity locking is opt-in per PostgreSQL source connection. New connections and connections upgraded by migration `0027_entity_lock_rollout` have `entity_locking_enabled = false`. The deployment setting `TIGR_ENTITY_LOCKING_ENABLED=false` is the global emergency switch; it takes precedence over connection flags and disables claim creation and entity-aware filtering. It defaults to `true` so enabled connection flags can operate after explicit workspace rollout.

Enable a connection only after all of these are true:

1. The source is PostgreSQL and a successful current structure snapshot exists.
2. A Test Data Engineer has reviewed and approved the anchor keys and relationship paths against that snapshot.
3. Source-backed data sets generated after approval persist protected lineage. Legacy versions without trustworthy lineage are not backfilled from output values; regenerate them through the approved source workflow before acquiring an entity lock.
4. The identity secret is configured and available to workers. Secret values must never be copied into logs, reports, or deployment notes.

The connection panel shows aggregate active claims and the names, reference codes, and affected row counts of other data sets to Admins and Test Data Engineers. QA Engineers receive safe conflict text and their own blocked row counts. Neither the panel nor the API exposes source keys, key values, or entity tokens.

## Deployment and rollout

1. Back up the database and record safe pre-deployment counts for data-set versions, records, artifacts, reservations, and version locks.
2. Deploy the additive migration with `.venv/bin/alembic -c alembic.ini upgrade head`. Confirm the current revision is `0027_entity_lock_rollout` and that every connection flag is false unless explicitly rolled out.
3. Deploy the application and UI. Keep the global emergency switch enabled; connection flags still default off.
4. In a non-production workspace, test with two QA users and at least two overlapping data sets. Cover disjoint data, overlapping acquisition, filtered preview/download, denied derivation/delivery, release, expiry, and stale-worker rejection. Check API responses, audit events, and exports for raw identity material.
5. Have a Test Data Engineer approve the current snapshot configuration. An Admin or Test Data Engineer may then enable that connection. Reconcile after enabling and after worker restarts with `PYTHONPATH=backend .venv/bin/python -m tigr.cli entity-lock-reconcile`.
6. Monitor the aggregate reconciliation report and audit events. Review missing-lineage counts before expanding rollout. Resolve those versions by safe regeneration, not inferred identity backfill.

## Reconciliation report

`entity-lock-reconcile` expires claims past their lease, closes claims whose version lock is no longer current, releases claims associated with disabled connections, and prints only aggregate counts. It writes the same safe counts to the audit log. It does not read or print source keys, entity tokens, credentials, or record samples.

Run it on a schedule appropriate to the worker and lease interval, and after a rollout change, deployment, or recovery. A nonzero `source_versions_missing_trustworthy_lineage` count is a remediation queue, not permission to infer source identity from masked output.

## Operational rollback

1. For each rolled-back connection, use the entity-locking control to disable the flag. The transaction serializes against claim acquisition, releases active entity claims, and leaves existing reservations and version locks untouched. For a global incident, also set `TIGR_ENTITY_LOCKING_ENABLED=false` and restart API/worker processes; the global switch is an immediate kill switch for reads and writes in newly started processes.
2. Stop or drain source, masking, scenario, and delivery workers. Mark in-flight entity-aware jobs cancelled or failed with safe reason codes. Verify no feature worker can commit after its job is terminal.
3. Run `entity-lock-reconcile`; confirm `active_claims_remaining` is zero for the disabled connections and review the aggregate report.
4. Restore the Phase 6 baseline trees (the captured post-Phase-5 application) to roll back Phase 6 only. Restore the Phase 5 baseline trees to roll back Phases 5 and 6. For a complete requirement rollback, use the original Phase 1 baseline and its implementation manifest after disabling claims.
5. Keep migration `0027` if only reverting application behavior. To remove only Phase 6 schema after the restored application no longer references the flag, run `.venv/bin/alembic -c alembic.ini downgrade 0026_entity_claim_registry`. This drops only the per-connection flag column.
6. For a full feature-schema rollback, first verify the restored application no longer references feature schema, then downgrade to `0023_dataset_reference_codes`. Migrations `0024`–`0027` remove only feature-specific relationship configuration, lineage, claim, and rollout metadata. Dataset records, artifacts, reservations, and version locks are preserved. If any environment cannot verify that separation, stop at disabled application behavior and retain the additive schema.
7. Remove the identity secret reference only after all workers are stopped and no remaining feature process needs it. Confirm the pre-deployment safe counts for dataset versions, records, artifacts, reservations, and version locks are unchanged; record counts only in the deployment log.

The per-phase source snapshots and exact application restore trees are in `Docs/rollback/2-TIGR_Record_Level_Locking/phaseN-baseline/`. Phase 6 migration downgrade is additive and reversible; downgrades through Phase 4 remove feature-only metadata and should be rehearsed against a non-production database before production use.
