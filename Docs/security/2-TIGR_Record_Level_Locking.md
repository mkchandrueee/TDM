# TIGR entity locking security and permission review

## Permission boundaries

- Test Data Engineers approve versioned source key and relationship configuration. They cannot approve unsupported metadata or relationships that fail type, key, endpoint, or ordered-path validation.
- Admins and Test Data Engineers can inspect aggregate lock impact and operate connection rollout controls. QA Engineers cannot see the cross-dataset impact list or another actor's lock owner identity.
- Existing reservation, dataset, source connection, delivery, and workspace membership checks remain in front of entity-lock operations.
- The per-connection rollout control is default off. Enabling requires a current structure snapshot and a matching approved configuration. Re-enabling is denied while an active version lock uses the source namespace.

## Data handling

- Identity values are HMACed in worker memory. Stored lineage and claims contain opaque tokens and key definitions, never raw key values.
- User-facing conflict messages and row counts do not identify protected source objects or values. Operator reports contain dataset names and safe row counts only for Admin/TDE.
- Audit events contain action, actor, dataset/connection references, and aggregate counts. They do not contain identity tokens or source values.
- Source queries that cannot be safely rewritten are blocked at the enabled connection namespace while another actor has a claim. This is intentionally conservative.
- Legacy source-backed versions without trustworthy lineage fail closed when entity locking is enabled. They must be regenerated from an approved source workflow; no backfill from masked output is allowed.
- The global environment switch can disable entity-aware claims and filtering. Connection-level disable synchronously releases its claims while preserving version locks and reservations.

## Review status and acceptance evidence

The code-level review covers the permission checks above, default-off rollout, advisory-lock serialization for acquisition and disable operations, stale fencing checks, safe aggregate reconciliation, and additive migration downgrades. The non-production acceptance suite exercises two QA actors against two overlapping datasets through the lock and preview APIs, including a safe 409 and blocked-row count. The local Phase 6 rollback rehearsal downgraded `0027_entity_lock_rollout` to `0026_entity_claim_registry` and upgraded it again; that migration only removes and recreates the rollout flag column. No production rollout was performed. Any broader full-feature rollback rehearsal should record only aggregate preservation counts, not source row samples or token material.
