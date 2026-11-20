# TIGR Record-Level Locking — Change Requirements and Implementation Specification

**Document type:** Product and technical change requirement  
**Status:** Ready for implementation planning; no application changes are included in this document task  
**Code baseline reviewed:** 2026-10-16  
**Scope:** Cross-data-set locking of source-backed entity records and explicitly linked records

## 1. Purpose

Define a safe, implementable design for a QA Engineer to reserve and lock a data set for exclusive use, while preventing other users from accessing or reusing the same underlying customer, account, card, or other entity records through any TIGR data set.

The lock must follow relationships explicitly approved by a Test Data Engineer (TDE). For example, when a TDE approves `customers → accounts → cards`, locking a customer in one data set also locks the linked account and card records. Another QA Engineer must not be able to retrieve those records from a different data set or create a new data set containing them while the lock is active.

This is an application-enforced data-use lock backed by PostgreSQL state. PostgreSQL row locks alone are not an access-control mechanism: ordinary reads can continue while rows are locked for update. TIGR must enforce the entity claims on every record-use path.

## 2. Current-code baseline and implementation gap

This section records the implementation observed in the repository as of the review date. It takes precedence over older planning documents where they describe different behavior.

| Area | Current implementation observed | Change required |
|---|---|---|
| Reservation and lock | `dataops.dataset_locks` stores a lock for a data-set version, holder, expiry, status, and fencing token. A partial unique index permits one active lock per version. QA acquisition requires an approved reservation in its active time window. | Preserve version locking and add entity-level claims that can conflict across different data-set versions. |
| Eligibility | Lock endpoints use `authorize_dataset_read`; a lock requires a published, validated, privacy-compliant, unexpired version. | Preserve these gates for the base data set. Define how entity conflicts affect other versions. |
| Schema metadata | PostgreSQL connection testing stores discovered tables, fields, primary-key flags, foreign-key references, unique constraints, and checks in the connection structure preview. Foreign-key metadata is currently represented as individual field references. | Retain relationship metadata as first-class, grouped edges, including composite foreign keys. Add explicit TDE approval and lock-propagation configuration. |
| Stored records | `dataops.dataset_records` stores output JSONB rows keyed by data set version and ordinal. It has no source-row identity or linked-entity lineage. | Add protected lineage references that survive output renaming and privacy masking. |
| Source-backed privacy flow | Source-backed records are re-extracted for masking and stored after privacy and validation complete. | Capture lineage during extraction in worker memory and persist only protected identity tokens alongside approved masked output. Never persist unmasked source rows to create lineage. |
| Access enforcement | Existing reservations and lock APIs manage a version lease. Record reads and generation paths are not currently governed by cross-data-set entity claims. | Add one centralized authorization/filtering service and apply it to every read, copy, generation, and delivery path. |

### Current-code files inspected

- `backend/tigr/governance.py` — `DataSetReservation`, `DataUseRequest`, and version-scoped `DataSetLock`.
- `backend/tigr/reservations_api.py` — reservation eligibility, lease acquisition, renewal, release, expiry, and fencing-token validation.
- `backend/tigr/connection_testing.py` — connection-time structure discovery for PostgreSQL tables and constraints.
- `backend/tigr/schema_records.py` — JSONB `DataSetRecord` storage keyed by version and ordinal.
- `backend/tigr/dataset_services.py` — dataset record storage and retrieval helpers.
- `backend/tigr/privacy_api.py` — fresh source extraction, masking, validation, and post-mask persistence.
- `backend/tigr/dataset_api.py`, `backend/tigr/source_to_data.py`, `backend/tigr/scenario_api.py`, `backend/tigr/delivery_api.py`, and `backend/tigr/outbox_worker.py` — record access and generation paths to inventory and protect during implementation.

The existing MVP plan and baseline docs may predate the current implementation. Codex implementing this change must inspect the current files and migrations again and adapt this specification to the code as it exists at implementation time. Do not restore older behavior merely because it appears in a baseline document.

## 3. Goals and non-goals

### Goals

1. Let a TDE configure which source keys identify entities and which declared relationships propagate locks.
2. Let a QA Engineer acquire an entity lock only through the existing approved-reservation and lease flow.
3. Prevent other users from reading, exporting, delivering, or deriving rows that contain any entity claimed by another active lock.
4. Apply the restriction across data sets generated from the same canonical source identity namespace.
5. Retain internal lineage through joins, output-field aliases, and masking without exposing source identifiers in output or UI.
6. Ensure concurrent lock attempts and data-generation jobs cannot bypass or race past claims.
7. Audit lock scope, conflicts, restricted access decisions, release, expiry, and administrative force release without logging raw keys or record values.

### Non-goals

- Preventing a database superuser or infrastructure administrator from querying PostgreSQL.
- Revoking data already downloaded, delivered, or copied outside TIGR before a lock was acquired.
- Inferring undocumented business relationships from field names alone and treating them as approved.
- Replacing existing reservation approval, publication, privacy, or versioning workflows.
- Using PostgreSQL `SELECT FOR UPDATE` or advisory locks as the sole access-control solution.
- Applying the feature to arbitrary external databases without a configured source connection and supported extraction path.

## 4. Terminology

- **Source identity namespace:** A stable identifier for a logical source database and schema. By default it is the TIGR source connection. Connections that point to the same logical source may be explicitly configured as aliases if cross-connection matching is required.
- **Entity key:** A declared primary or unique key identifying an entity, including composite keys.
- **Relationship edge:** A declared foreign-key relationship or a manually configured and approved relationship between source objects and fields.
- **Lock propagation path:** An explicitly TDE-approved sequence of relationship edges that extends a lock from an anchor entity to related entities.
- **Entity reference:** A protected, non-output token representing one source entity identity, namespaced by source identity, object, and key definition.
- **Record lineage:** The protected mapping from an output record to one or more source entity references that contributed to it.
- **Entity claim:** An active claim on an entity reference, owned by a data-set lock lease.
- **Fencing token:** A monotonically increasing lease token used to reject work submitted by an expired or superseded lease holder.

## 5. Roles and functional requirements

### 5.1 TDE relationship and key configuration

**FR-01 — Discover key metadata.** For PostgreSQL sources, discover primary keys, unique constraints, foreign keys, referenced objects/fields, column order in composite keys, and whether constraints are validated/enforced when available.

**FR-02 — Preserve composite relationship structure.** Store a multi-column foreign key as one relationship edge with ordered source and target field lists. Do not flatten it into unrelated independent edges.

**FR-03 — Configure entity anchors.** Allow a TDE to mark eligible keys as lock anchors. Prefer declared primary keys; permit a declared unique key only when a TDE explicitly approves it. A field merely named `customer_id` is not sufficient proof of identity.

**FR-04 — Explicitly approve propagation.** Show each discovered relationship as an edge and let a TDE enable or disable it for lock propagation. The TDE must be able to configure paths such as `customers → accounts → cards`, including direction and maximum traversal depth. Unselected edges do not propagate a lock.

**FR-05 — Manual relationships.** Where the source has no declared foreign key, allow an authorized TDE to add a relationship with exact object/field pairs, join semantics, and a reason. Keep these relationships visibly marked as manually approved rather than database-declared.

**FR-06 — Version the metadata configuration.** Persist the source structure snapshot/version and the lock relationship configuration version used by each data set plan and lineage record. A later schema refresh must not silently rewrite the lock interpretation of an already-created version.

### 5.2 Lock acquisition and use

**FR-07 — Preserve current reservation gates.** QA Engineers can acquire a lock only for a published, validation-passed, privacy-compliant, non-expired version and within an approved active reservation window.

**FR-08 — Expand lock scope.** On acquisition, resolve the base data set's protected lineage references and expand them only along the TDE-approved propagation paths. The resulting scope includes the selected anchor entities and reachable related entity keys represented by records in the version.

**FR-09 — Detect cross-data-set conflicts.** Acquisition must atomically fail if any requested entity reference is already claimed by a different active lease in the same source identity namespace. Return a safe conflict response with counts and affected data-set references, never raw entity keys.

**FR-10 — Permit disjoint use.** A different QA Engineer may lock and use disjoint entities even if the same source tables, fields, or data-set family are involved.

**FR-11 — Owner access.** While a lease is active, the holder may use the base data set under existing role and reservation controls. Other users are denied access to records whose lineage intersects the holder's active claims, including those records when they appear in another data set.

**FR-12 — Apply to newly derived data.** A generation or copy operation must not include entities claimed by another active lease. The operation must return a safe reason and actual generated count. It must not silently replace blocked rows with unrelated records unless the user explicitly retries with revised criteria.

**FR-13 — Protect joined rows as a unit.** If an output row contains an entity reference claimed by another active lease, deny or exclude the entire output row. Do not expose partial customer/account/card columns that would reveal a linked record.

**FR-14 — Recheck lease at work boundaries.** Long-running workers must validate the lock and fencing token before source extraction, before writing generated output, and before any delivery/commit step. Expired or superseded work must stop and clean up temporary results.

**FR-15 — Release and expiry.** Explicit release, lease expiry, and Admin force release deactivate all claims associated with the lease. Claims must have expiry semantics consistent with the lock lease and be recoverable after worker or application failure.

### 5.3 User information and audit

**FR-16 — Show safe lock status.** Where a user tries to use affected data, show that some records are unavailable due to an active reservation/lock, with safe counts and next steps. Do not display locked customer/account/card IDs or values.

**FR-17 — Cross-data-set impact view.** TDE/Admin can inspect affected data-set names/IDs and claim counts for an active lock. QA users can see only safe use status and lease expiry/holder details permitted by current policy.

**FR-18 — Record audit events.** Record acquisition, renewal, release, expiry, force release, conflict, blocked access, and blocked generation. Audit payloads may include lease ID, data-set/version IDs, relationship-configuration version, counts, and reason codes; never include raw keys, values, credentials, or generated samples.

## 6. Data and lineage design requirements

### 6.1 Protected identity tokens

Create a canonical identity token from:

`source identity namespace + schema/object + key definition + typed, ordered key values`

Use a keyed HMAC or equivalent protected token, not a plain hash of low-entropy identifiers. Keep the secret in the configured secret manager. Tokens must be stable across data sets from the same namespace so records can be matched, but must not be exposed through APIs, logs, artifacts, previews, exports, or user-facing errors.

Composite keys must preserve field order and type-aware encoding to avoid ambiguous concatenations. Include a key/token format version to support secret rotation and migrations.

### 6.2 Record lineage

Add protected lineage storage that can associate:

- a TIGR data-set version and output record ID/ordinal;
- one or more entity references contributing to that output row;
- the metadata configuration version and relationship path used;
- optional parent TIGR data-set/version lineage for copies or derived data.

One output row may map to several source entities (for example, customer + account + card). A source entity may occur in many TIGR data sets. Store the lineage separately from output JSONB values so no privacy transformation or output alias can erase it.

For source-backed generation, capture keys and relationship references in worker memory during the approved source extraction. Apply masking to output values, then persist only the masked/validated records and protected lineage tokens. Do not store unmasked source record values to support locking.

For synthetic generation, use dataset-scoped synthetic entity tokens if those records are within the feature's lock scope. Synthetic tokens must not be mistaken for source identity tokens.

### 6.3 Entity claim registry

Add a normalized registry for active claims with at least:

- claim ID and parent lock lease ID;
- canonical source identity namespace;
- entity object/key definition and token version;
- protected entity token;
- base data-set/version and owner subject;
- acquired/expiry/released timestamps and status;
- relationship path/configuration version for propagated claims.

Enforce uniqueness/conflict rules in PostgreSQL so concurrent attempts cannot both claim the same entity. Use transactional locking/constraints for claim acquisition. PostgreSQL row/advisory locks may serialize claim changes but must not be mistaken for read authorization.

## 7. Access enforcement requirements

### 7.1 Central authorization service

Implement one backend service/API dependency that evaluates record lineage against active entity claims. It must accept an authenticated principal, operation, data-set/version, and set/query of candidate record lineage references. It returns only allowed records or a deny/conflict result; callers must not implement divergent local lock logic.

### 7.2 Required path inventory

Codex must re-inventory current routes and workers at implementation time. At minimum, enforcement must cover:

- data-set details record previews;
- downloadable CSV/JSON artifacts;
- target delivery/provisioning and validation that reads records;
- source-to-data object-mapped extraction and pre-built query execution;
- scenario-to-data source-backed query execution;
- new data-set version creation, copy, replay, and edit/regenerate paths;
- background jobs, retries, and outbox workers;
- administrative exports or reports that could contain record values;
- any cached artifacts, signed URLs, or temporary files containing records.

Metadata-only views can remain available where they do not reveal record keys/values. APIs must return safe block messages and audit events rather than raw records when access is denied.

### 7.3 Source query filtering

When a source-backed operation queries the external source, apply active entity claims to the source query before source rows can become output. Use parameterized predicates/query construction and bounded batches; do not interpolate lock keys into SQL text. For linked entities, use the approved relationship graph and exact join keys. If the connector/query cannot safely enforce the claimed scope, fail closed before generation.

### 7.4 Database privileges and RLS

QA Engineers have no direct PostgreSQL credentials under the stated operating assumption. Keep record tables accessible only to TIGR backend/worker identities. RLS may be used as defense in depth if the principal identity is securely bound to each DB transaction. A shared privileged backend login must not be treated as sufficient identity on its own. Database owners/superusers remain outside this application lock guarantee.

## 8. API and UI requirements

API paths and payloads must follow current route and schema conventions; final route names are determined during implementation. Expected capabilities:

- list discovered relationship metadata and TDE lock propagation configuration;
- save/version approved lock propagation configuration;
- acquire a lease and atomically create expanded entity claims;
- inspect lock status and safe impact counts;
- renew/release/force-release an existing lease using the current fencing model;
- query record eligibility for a protected operation without returning identifiers.

UI updates:

1. **Connection / schema metadata:** mark PK and unique-key candidates; show relationship endpoints, composite fields, source of relationship evidence, and approval state.
2. **TDE lock configuration:** choose anchors and explicit relationship paths; show traversal direction/depth and a readable path preview. Save configuration as a versioned approval.
3. **Reservations & Locks:** continue using current reservation and lease flow; show entity claim counts and safe cross-data-set impact details. Do not display entity IDs.
4. **Use/generation flows:** explain when rows are blocked by another active lock, indicate requested vs available record counts, and offer retry after the conflicting lease ends.
5. Keep current user-facing status, privacy, and reservation behavior unless a requirement above explicitly changes it.

## 9. Concurrency, lifecycle, and failure behavior

- Lock acquisition and claim creation must be all-or-nothing.
- Define deterministic ordering for multiple claim inserts to reduce deadlocks.
- An expired lease must not leave active claims. Add an idempotent sweeper/reconciliation process and ensure each operation also checks `expires_at` synchronously.
- Renewal must preserve the same lease owner and fencing token; a newly acquired lease must use a greater token.
- A stale worker must be unable to commit output after the lock expires or a new holder acquires the conflicting entities.
- If lineage is missing, malformed, token version unsupported, or relationship expansion fails, fail closed for source-backed record use. Do not treat missing lineage as an empty lock scope.
- A schema refresh may create a new metadata version. Existing data-set versions retain the old relationship configuration snapshot; newly generated versions use the newly approved configuration.
- Re-locking an already held version by the same actor must be idempotent or return a clear conflict; it must not create duplicate active claims.
- Handle cancellation and process crashes by expiring claims and cleaning temporary files/artifacts without deleting previously published compliant records.

## 10. Migration and compatibility

1. Add migrations for relationship configuration/versioning, record lineage, entity claims, indexes, and constraints.
2. Existing active version locks must remain visible and valid during rollout. They do not have entity coverage until the related data-set version has lineage.
3. Existing data-set records without lineage must be treated as **not safe for entity-level cross-data-set enforcement**. For source-backed records, require regeneration through the lineage-aware path or prevent lock acquisition/use until lineage exists. Do not fabricate source identities from masked output fields.
4. Preserve current reservation approval, lock lease, force-release, audit, and fencing behavior.
5. Roll out behind a feature flag by workspace/connection. Enable source-backed generation only after schema configuration, lineage capture, and enforcement are complete end-to-end.
6. Provide reconciliation reports for missing lineage, orphan claims, expired leases, and duplicate entity tokens. Reports must contain counts and IDs of TIGR objects only, not source key values.

## 11. Phased implementation plan

Each phase is a separate Codex implementation task. Stop at each phase gate, review its migration/security implications, and wait for explicit direction before proceeding to the next phase.

Every implementation phase must include a phase-specific rollback procedure before its exit gate is accepted. Rollback must preserve the existing reservation, version-lock, publication, privacy, and dataset behavior.

### Phase 0 — Current-code and route inventory

**Deliverables:** Confirm current migrations/models, connection metadata shape, record-read/generation route map, privacy timing, storage paths, and test coverage. Update this requirement document if repository code changed since 2026-10-16.  
**Exit gate:** Every route/worker able to read, copy, export, or deliver record values is listed; no code change is required unless separately requested.

### Phase 1 — Schema relationship catalog and TDE configuration

**Deliverables:** Persist grouped PK/unique/FK metadata and configuration versions; preserve composite relationships; add TDE controls for entity anchors and explicit propagation paths; support manually approved relationships with audit trail.  
**Exit gate:** Metadata can represent and display a path such as `customers → accounts → cards` without inferring undocumented links or exposing record values. The new metadata/configuration can be disabled and its migration reversed without changing existing connection previews or saved data-set versions.

### Phase 2 — Protected record identity and lineage capture

**Deliverables:** Token format/key management; lineage tables and indexes; source-backed extraction captures entity keys and approved linked references in memory; masking output persistence writes protected lineage; derived datasets retain parent entity lineage.  
**Exit gate:** Tests prove that renamed/masked output identifiers still map to the same protected source identity, and no raw source key is present in the output artifact, logs, or API response. Rollback removes or disables the new lineage/token paths without deleting dataset records or artifacts that predate this feature.

### Phase 3 — Entity claim registry and atomic leases

**Deliverables:** Claim model, uniqueness/conflict controls, expansion using the approved relationship graph, acquisition/renewal/release/expiry/force-release integration with the current version lease and fencing token.  
**Exit gate:** Concurrent attempts for overlapping entity scopes yield one active owner; disjoint scopes can be acquired concurrently; expired claims are denied and cleaned up. Rollback deactivates feature claims and restores version-only lease behavior without invalidating existing reservations or version locks.

### Phase 4 — Enforce claims across all data paths

**Deliverables:** Central access service and integration across every path in Section 7.2, including source query extraction, new data-set creation, previews, artifacts, jobs, and delivery. Add stale-worker rejection and safe row-count outcomes.  
**Exit gate:** A conflicting user cannot retrieve or derive any row whose lineage intersects an active claim, regardless of which TIGR data set or workflow is used. Rollback disables entity enforcement consistently across APIs, workers, caches, and artifact paths before application code is reverted.

### Phase 5 — User experience, notifications, and operational audit

**Deliverables:** TDE relationship configuration UI; lock impact and safe conflict messages; blocked-row counts; role-appropriate affected data-set list; audit and operator views.  
**Exit gate:** Users can understand why records are unavailable and when they can retry without seeing protected source identifiers. UI/API rollback leaves the existing reservation and version-lock experience usable.

### Phase 6 — Migration, backfill policy, rollout, and security review

**Deliverables:** Legacy data handling, feature flags, reconciliation jobs/reports, deployment notes, permission review, and rollback plan. Backfill only from trustworthy lineage sources; otherwise require safe regeneration.  
**Exit gate:** End-to-end acceptance and security review pass in a non-production workspace; a rollback rehearsal succeeds and preserves existing dataset and reservation behavior.

## 12. Acceptance criteria

1. TDE sees PostgreSQL-declared PK, unique, FK, and composite FK metadata and explicitly approves the relationships used for lock propagation.
2. Locking a source-backed data set creates claims for its anchor entity IDs and all IDs reachable through selected relationship paths.
3. Another QA Engineer cannot preview, download, deliver, or use those entities to create a new data set through any supported TIGR path while the lease is active.
4. The denial applies when the same entity appears under a different data-set name, output field alias, or masked output identifier.
5. Rows linked to a claimed entity are blocked as a whole; TIGR does not reveal partial linked values.
6. A disjoint entity scope remains usable by another authorized QA Engineer.
7. Overlapping concurrent acquisitions cannot both succeed, including across different data-set versions.
8. Renewal, release, expiry, force release, and stale fencing-token behavior correctly activate/deactivate every associated entity claim.
9. A generation job started before lease expiry cannot publish results after its fencing token becomes stale.
10. Users see safe messages and counts; API payloads, logs, audit records, and exports contain no raw key values or hidden tokens.
11. Source-backed lineage is persisted only with masked/validated records; unmasked source records remain transient in worker memory.
12. Missing lineage or unsupported relationships fail closed with a clear remediation path.
13. Existing reservation, publication, privacy, and dataset version behavior remains intact for data not yet enabled for entity locking.

## 13. Required future verification

The implementation phase must add focused automated tests for metadata ingestion, composite keys, relation expansion, token stability, masking/alias preservation, atomic lock conflicts, lock expiry, access enforcement by operation, source-backed generation filtering, worker fencing, API leakage, and legacy records without lineage. Add an end-to-end scenario with two QA users and at least two overlapping data sets. Do not claim completion from UI-only verification.

## 14. Risks and decisions to resolve during Phase 0

1. **Same source through multiple connections:** Decide whether entity identities are scoped to a connection or whether Admin/TDE can define aliases for connections to the same database. Default to connection-scoped tokens until explicitly aliased.
2. **Missing or incorrect constraints:** Require manual TDE approval for undeclared links; do not infer production lock paths from names or matching values alone.
3. **Masking key fields:** Keep internal identity tokens separate from transformed output fields. Never restore original source IDs into an artifact to support locking.
4. **Query-derived datasets:** Verify that the SQL parser/execution path can retain lineage for every contributing table. If it cannot, block entity-level lock enablement for that query rather than asserting false coverage.
5. **Cardinality/large claim sets:** Measure claim-table volume and source query filtering for realistic bank data sizes; use indexes and bounded operations.
6. **Already delivered copies:** Define operational messaging that TIGR can control future TIGR access but cannot recall external copies delivered before the lock.
7. **Privacy and retention:** Define retention and secret rotation for lineage tokens and released claims; keep lineage access service-restricted and audited.

## 15. Rollback and recovery requirements

Rollback must be available throughout implementation and after rollout. It must restore the application to the pre-feature behavior while preserving data-set records, artifacts, reservations, and version-scoped locks that existed before this feature. It must not expose protected source keys or tokens during rollback.

### 15.1 Rollback controls

1. Add a feature flag scoped by workspace/connection and default it off. Turning it off must synchronously prevent new entity claims and entity-aware generation, and route reads back through the established version-level authorization behavior. Feature-off behavior must be safe even when the new database tables remain present.
2. Keep entity locking changes isolated behind dedicated modules, configuration, and additive migrations. Do not rewrite or repurpose existing reservation/lock fields in a way that requires destructive data conversion to restore the previous behavior.
3. Before changing any existing file, create a restorable source snapshot of the pre-feature file contents and maintain a manifest of each implementation change and its inverse. The current project directory is untracked in its parent Git repository, so a Git revert is not currently a reliable rollback mechanism. Reassess this condition before implementation; do not assume a commit-based rollback exists unless the files are under a usable version-control baseline.
4. Every migration must have a tested downgrade or a documented, safe forward-recovery alternative. Feature-only tables, constraints, indexes, and configuration records must be distinguishable from pre-existing TIGR data. Never drop or rewrite any dataset records, generated artifacts, reservations, or version locks during rollback.
5. Keep a deployment runbook with the exact order for disabling flags, stopping/draining workers, rejecting stale feature jobs, releasing or expiring feature claims, reverting application code, and downgrading feature migrations. The runbook must identify which steps are reversible and which remove feature-only state.

### 15.2 Rollback procedure

For a complete rollback of this requirement:

1. Disable entity-lock feature flags for all workspaces/connections and stop accepting new entity-lock configuration, claim, and lineage-aware generation requests.
2. Drain or stop workers that can read or write feature lineage/claims. Mark their in-flight feature jobs cancelled or failed using safe reason codes, and prevent stale jobs from committing records or artifacts.
3. Revoke all active entity claims through the normal transactional release path, audit the rollback, and confirm no active claim remains. Preserve unrelated version-scoped locks and reservations.
4. Revert the application and UI changes using the pre-feature source snapshot/change manifest. Existing record access then follows the pre-feature version-level checks.
5. After confirming the reverted application no longer references feature schema, downgrade only the locking-feature migrations. Drop feature-only token, lineage, relationship-configuration, claim, audit, and feature-flag data as specified by those migrations. Preserve all dataset records and generated artifacts, including those created while the feature was enabled. Remove only feature-specific metadata/lineage in the documented cleanup step; do not infer source identity from output data to replace it.
6. Remove feature-only secrets and key references from the configured secret manager after confirming no remaining process needs them. Never print token material or secret values in the runbook or audit trail.
7. Reconcile and report safe counts for remaining feature tables/configuration, active claims, pending jobs, and missing legacy lineage. Record completion in the deployment log without source identifiers or token values.

If a destructive downgrade cannot safely distinguish feature-only state from existing TIGR state, stop after disabling the feature and reverting application behavior; keep the unused additive schema in place until a reviewed cleanup migration is available. This still provides an operational rollback without risking existing data.

### 15.3 Rollback acceptance criteria

- A non-production rollback rehearsal demonstrates restoration of the pre-feature app and schema behavior from a recorded implementation snapshot.
- Existing reservations, active or historical version locks, data-set versions, all records, and all generated artifacts remain readable and retain their prior lifecycle behavior after rollback.
- No new entity claims can be acquired and no stale worker can write feature lineage or publish output after flags are disabled.
- Feature-only state is removed only by an explicit, scoped migration/cleanup step; rollback logs and reports contain no raw source keys, entity tokens, credentials, or record samples.
- The tested rollback steps, order, migration revisions, and recovery point are included in the deployment notes before production enablement.

