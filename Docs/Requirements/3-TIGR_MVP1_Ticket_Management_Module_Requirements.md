# TIGR MVP1 — Ticket Management Module Requirements

**Document type:** Functional and implementation requirements  
**Status:** Build baseline  
**Module:** Test Data Request Tickets  
**Primary users:** QA Engineer, Test Data Engineer (TDE), Admin

## 1. Purpose

Add a ticket management module to TIGR so a QA Engineer can raise and track a test data request, and a Test Data Engineer can receive, assess, fulfill, and close it. A ticket remains linked to the resulting TIGR dataset. Both the requester and the responsible TDE can see ticket status and aging in their role-scoped dashboards.

The ticket history must provide a time-stamped, append-only record from creation through closure. TIGR must calculate and report how many days a ticket has remained open and, for closed tickets, its total time to closure.

## 2. Scope and relationship to existing TIGR capabilities

This module covers the request and fulfillment coordination workflow. It complements the existing Dataset, Source Onboarding, Scenario to Data, Provisioning, Reservation/Lock, Dashboard, and Audit capabilities.

- A ticket is the work item for a QA user's request to create or prepare test data.
- A dataset remains the reusable data asset and the primary unit for dataset lifecycle, reservation, locking, provisioning, and masking.
- A ticket may reference one resulting dataset. A dataset may be linked to more than one ticket when the same approved dataset fulfills equivalent requests, subject to workspace permissions and an explicit fulfillment note.
- Creating a ticket does not itself reserve or lock a dataset. The QA user uses the existing reservation and lock workflow after the dataset is available.
- Dataset data stays under existing TIGR storage rules. The ticket stores request metadata and dataset references, not extracted customer/test data or credentials.
- The module uses the existing three human roles: QA Engineer, Test Data Engineer, and Admin. Service identities remain non-human and follow existing scoped access controls.

## 3. Goals

1. Let a QA user submit a complete, actionable test data request.
2. Route submitted requests to an authorized TDE work queue for the workspace/project.
3. Let TDEs acknowledge, triage, work, block, fulfill, and close requests.
4. Keep requester and fulfillment details visible to the right users.
5. Maintain a trustworthy event history, including comments, status changes, assignment changes, dataset linking, and closure.
6. Show current open age and closed duration in dashboards and reports.
7. Support filtering, sorting, export, and operational reporting without weakening workspace-level access controls.

## 4. Roles and access

| Capability | QA Engineer | Test Data Engineer | Admin |
|---|---|---|---|
| Create ticket | Yes, in authorized workspace/project | No in MVP1 | No on behalf of another user in MVP1 |
| View ticket | Own tickets only, in authorized workspace/project | Tickets in assigned/authorized workspace queue; tickets assigned to them | All tickets in authorized administrative scope |
| Edit request details | Own ticket while Draft; after submission, add comments or clarification responses | Edit fulfillment fields; request fields are read-only except triage clarification | Read and administer within existing authorized scope |
| Change status | No; may cancel own ticket before work starts, with reason | Yes, according to transition rules | Admin actions only if explicitly permitted by existing admin policy; every intervention requires a reason |
| Assign/reassign | No | TDE users may assign/reassign within authorized workspace | Yes, within authorized scope; reason required |
| Link dataset / record fulfillment | No | Yes | No, except a future explicitly authorized override |
| Close ticket | No | Yes, only after fulfillment conditions pass | No routine close; exceptional administrative closure only if product policy enables it, with mandatory reason and audit |
| View/export ticket reports | Own-ticket scope | Authorized workspace scope | Administrative scope |
| View audit history | Own-ticket history | Authorized workspace history | Administrative scope |

Access checks must be applied in the API/service layer, not only in the UI. A user must not see ticket titles, comments, dataset names, attachment metadata, or activity events outside their authorized scope.

## 5. End-to-end user journeys

### 5.1 QA Engineer raises and tracks a request

1. From the dashboard or Ticket Management, QA selects **New test data request**.
2. QA selects the workspace and project from their authorized list.
3. QA supplies the request information in Section 7 and can save as Draft.
4. On **Submit**, TIGR validates required fields, creates the ticket ID, stamps the creation/submission timestamps, sets status to **Submitted**, and places it in the authorized TDE queue.
5. TIGR records a creation/submission event and notifies the relevant TDE queue/users using the available in-product notification mechanism.
6. QA can view the ticket, current status, assigned TDE if assigned, activity history, linked dataset after fulfillment, and current open age.
7. While work is in progress, QA can respond to clarification requests through a ticket comment. QA cannot silently edit the submitted requirements.
8. When TDE closes the ticket, QA receives a closure notification and can inspect the linked dataset and fulfillment note, then proceed through the existing dataset reservation/lock/provision flow.

### 5.2 TDE processes and fulfills a request

1. TDE opens **Ticket Inbox** from the dashboard or module.
2. TDE filters the authorized queue by status, workspace/project, priority, age, or assignment.
3. TDE acknowledges or assigns the ticket and records a comment if needed.
4. TDE may request clarification. This changes the ticket to **On Hold** and records the question and timestamp.
5. QA responds; TDE records the response as seen and resumes the ticket to **In Progress**.
6. TDE creates or prepares the dataset through the existing TIGR workflow. TDE may link a draft dataset before it is published, but cannot close the ticket until the dataset meets the closure conditions.
7. TDE selects **Share dataset and close** and supplies a fulfillment summary. TIGR validates that a permitted dataset is linked and available to the requester in the same authorized workspace/project.
8. TIGR records the dataset reference/version, fulfillment summary, actor, and timestamp; changes status to **Closed**; calculates final duration; and notifies QA.
9. The ticket remains searchable and its history remains read-only except for additional audit events or comments allowed by policy.

### 5.3 Admin monitors operations

1. Admin views aggregate ticket counts and aging within their authorized scope.
2. Admin filters open/closed tickets, assignment, priority, workspace/project, creation/closure date, and age bucket.
3. Admin exports a scoped report as CSV.
4. Administrative assignment, escalation, or exceptional intervention is recorded with actor, reason, timestamp, and before/after values.

## 6. Ticket status model

### 6.1 MVP1 statuses

| Status | Meaning | Open for aging? |
|---|---|---:|
| Draft | QA has saved a request but not submitted it | No |
| Submitted | Request is submitted and waiting for TDE acknowledgement/triage | Yes |
| In Progress | TDE has acknowledged the request and is working it | Yes |
| On Hold | TDE is waiting for requester clarification or an identified dependency | Yes |
| Closed | TDE has linked and shared the resulting dataset and completed the fulfillment note | No |
| Cancelled | Requester cancelled before fulfillment, or an authorized exceptional cancellation occurred | No |

### 6.2 Allowed transitions

- Draft → Submitted
- Draft → Cancelled
- Submitted → In Progress
- Submitted → On Hold
- Submitted → Cancelled (QA requester, before TDE work begins)
- In Progress → On Hold
- In Progress → Closed (only through **Share dataset and close** validation)
- In Progress → Cancelled (TDE may cancel only with a mandatory reason and requester notification; intended for duplicate, invalid, or withdrawn requests)
- On Hold → In Progress
- On Hold → Cancelled (with mandatory reason and requester notification)
- Closed → In Progress only through **Reopen** by an authorized TDE, with mandatory reason; reopening clears the active closure timestamp and preserves the prior closure event in history. A reopened ticket resumes its original age clock; final closure duration is measured from first submission to most recent closure, and prior closure intervals remain visible in history.

Invalid transitions must be rejected by the API and explained to the user. Every status transition is an audited event. Admin cannot silently alter statuses.

### 6.3 Status semantics and closure

- **Submitted** begins the open-age clock, not Draft creation.
- **On Hold** remains an open ticket. On Hold time is included in calendar aging and reported separately so the organization can distinguish TDE processing time from requester/dependency wait time.
- **Closed** requires a linked dataset and a non-empty fulfillment summary. Dataset access must be authorized for the requester; dataset must be in an eligible state according to existing dataset lifecycle rules.
- Closure means that the dataset details have been shared with the QA requester in TIGR and are visible from the ticket. It does not mean the QA user has reserved, locked, provisioned, or consumed the data.
- A closure notification is created only after the closure transaction commits successfully.

## 7. Ticket fields

### 7.1 System fields

| Field | Type | Required | Rule |
|---|---|---:|---|
| Ticket ID | String/UUID | Yes | Unique, immutable, human-readable display ID (for example, TDR-2026-000123); generated by TIGR |
| Title | String | Yes on submit | Concise request summary; maximum 160 characters |
| Description / test objective | Text | Yes on submit | What the QA user needs to test and expected outcome |
| Request type | Enum | Yes on submit | New dataset, change to existing dataset, or additional records for an existing dataset |
| Requester ID / name | User reference | Yes | From authenticated identity; not manually entered |
| TDE assignee ID / name | User reference | No | Authorized TDE; may be unassigned in queue |
| Workspace ID/name | Reference | Yes on submit | Must be accessible to requester and assigned TDE |
| Project ID/name | Reference | Yes on submit | Must belong to selected workspace |
| Priority | Enum | Yes on submit | Low, Normal, High, Urgent; default Normal |
| Status | Enum | Yes | System-managed using transition rules |
| Created at | Timestamp | Yes | Set when Draft is first saved |
| Submitted at | Timestamp | On submit | Starts open-age clock |
| Updated at | Timestamp | Yes | Updated on any ticket mutation |
| Closed at | Timestamp | On close | Current successful closure timestamp; null while open |
| Cancelled at | Timestamp | On cancel | Null unless cancelled |
| Reopened at | Timestamp | On reopen | Latest reopen timestamp; null if never reopened |
| Linked dataset ID/version | Reference | At closure | Dataset/version shared to QA; one primary dataset in MVP1 |
| Fulfillment summary | Text | At closure | TDE explanation of what was delivered, any limitations, and where to access it |
| Closure actor | User reference | At closure | TDE who closed ticket |
| Current age | Derived | Open tickets | Derived from submitted_at and current time |
| Final duration | Derived | Closed/cancelled tickets | Derived from submitted_at and terminal timestamp |
| Correlation ID | String | Yes | Connects ticket events to related TIGR jobs, dataset, and audit records |

### 7.2 Request details

| Field | Type | Required on submit | Notes |
|---|---|---:|---|
| Environment | Enum/text | Yes | Target test environment (for example, SIT, UAT, Staging); allow workspace-configured values |
| Application/system | Text or configured reference | Yes | Application under test |
| Scenario(s) | Text/list | Yes | Test scenarios and expected conditions; allow positive, negative, boundary, exceptional categories |
| Required entity/schema | Reference/text | Yes | Select an existing schema/dataset or describe required entities and fields |
| Record count | Positive integer | Yes | Requested count; enforce configured maximum or show validation message |
| Data source preference | Enum | Yes | Source-backed, synthetic, either; synthetic generation uses existing capability |
| Data date/as-of range | Date/range | Optional | Relevant business dates or transaction periods |
| Data relationships | Text/list | Optional | Required customer/account/card/transaction relationships, etc. |
| PII/masking needs | Text or policy reference | Optional | Apply existing approved masking policy; do not include actual sensitive values |
| Needed by | Date/time | Yes | Requested completion target, timezone-aware; informational priority input, not an SLA guarantee |
| Test window start/end | Date/time | Optional | Used to plan later reservation; ticket submission does not itself reserve data |
| Additional acceptance criteria | Text | Optional | Conditions TDE must meet before sharing |
| Attachments | File references | Optional | If enabled, apply existing upload restrictions and malware scanning; attachments are MVP1-configurable and must not allow secrets or production data |

### 7.3 Comments and activity

- A comment has immutable ID, ticket ID, author ID/role, created timestamp, content, visibility (requester/TDE participants; Admin per scope), and optional attachment references.
- Comments are append-only; users cannot edit or delete an existing comment in MVP1. A correction is added as a new comment.
- System activity events appear in the same chronological timeline but are visually distinct from user comments.
- Do not permit comments to contain or render credentials, connection secrets, or raw test records; apply existing security and PII controls where available.

## 8. Functional requirements

### FR-01 — Create and save draft

- QA can create a Draft ticket and save incomplete request details.
- Draft is visible only to its requester and authorized Admins; it is not routed to TDE and does not count as open.
- QA can edit/delete a Draft before submission, with deletion recorded in the audit trail if hard deletion is disallowed by platform policy. Preferred behavior: allow cancellation, retain the audit record.

### FR-02 — Validate and submit

- Submission validates all required fields, date ranges, record count, workspace/project authorization, and selected data references.
- On successful submit, assign ID and Submitted status, store submitted_at, start aging, and route ticket to the correct workspace/project TDE queue.
- Double-click/retry must not create duplicate tickets; use idempotency handling.

### FR-03 — Ticket inbox and assignment

- TDE can view all submitted tickets in authorized workspaces, including unassigned tickets.
- TDE can assign/reassign to an authorized TDE user; each change records old/new assignee and a reason/comment.
- The inbox supports filters and sorting from Section 10.
- Assignment does not reset submitted_at or aging.

### FR-04 — Triage, work, and clarification

- TDE can move a Submitted ticket to In Progress or On Hold.
- When moving to On Hold, a reason and clarification question are mandatory, and the requester is notified.
- QA can reply to clarification through comments. TDE resumes work by changing On Hold → In Progress.
- Record on-hold start/end timestamps so on-hold time can be reported. Multiple on-hold periods are supported.

### FR-05 — Dataset fulfillment and sharing

- TDE can link a dataset and version from an authorized workspace/project to the ticket.
- Before close, validate that the dataset exists, is not deleted, is in an eligible published/ready state, and is accessible to the QA requester.
- Close action requires a fulfillment summary and confirmation that the dataset details are shared with the requester.
- The ticket detail view displays a deep link to the dataset and its version. No underlying records are shown in the ticket unless the existing dataset preview permission allows it.
- The ticket retains the dataset ID and version even if the dataset later changes; current dataset state is fetched live and historical shared version remains identifiable.

### FR-06 — Close and notify

- Successful closure stores closed_at, closure actor, linked dataset/version, fulfillment summary, final duration, and a Closed event in one transaction.
- Notify the requester in product and via any configured notification channel.
- If a required linked dataset or permission check fails, closure must fail without partially changing ticket status or emitting a closure notification.
- QA cannot close tickets. Admin exceptional close, if enabled by existing policy, requires a mandatory reason and is separately labeled in history.

### FR-07 — Cancel and reopen

- QA may cancel own Submitted tickets only before TDE work begins; require a reason.
- TDE may cancel with a reason when request is duplicate, invalid, or withdrawn; notify requester.
- Authorized TDE can reopen a closed ticket with a reason. Preserve all prior closure data and events. Reopened ticket aging resumes from its original submitted_at.
- Reopening requires revalidation that requester and workspace access are still valid. If not, Admin remediation is required.

### FR-08 — Timeline and audit

- Maintain a chronological ticket event log from first save/creation through final state.
- Events include created, edited draft, submitted, assignment changes, status changes, comments, clarification requested/responded, dataset linked/unlinked/version changed, fulfillment details updated, closed, reopened, cancelled, notification result, and export where audit policy requires.
- Each event records event ID, ticket ID, actor ID and role/type, workspace/project, action, timestamp (UTC), outcome, reason/comment reference, before/after metadata, correlation ID, and related dataset/job/version references when applicable.
- Event records are append-only and searchable/exportable under existing TIGR audit policy. Never store secrets or raw sensitive data in before/after snapshots.
- UI displays timestamps in the user's configured timezone and stores canonical UTC timestamps.

### FR-09 — Dashboard cards and widgets

- QA dashboard: **My Test Data Tickets** with counts for Submitted, In Progress, On Hold, and Closed in a selected recent period; list recent tickets with status, priority, needed-by date, linked dataset when closed, and current open age.
- TDE dashboard: **Ticket Queue** with unassigned and assigned-to-me open counts, status counts, aging buckets, overdue-to-needed-by count, and oldest open tickets within authorized workspaces.
- Admin dashboard: aggregate ticket volume, open/closed counts, average and median closure duration, open-age buckets, On Hold tickets/time, priority distribution, workspace/project breakdown, and SLA/needed-by attainment if configured.
- Dashboard respects existing visibility policy: QA sees own tickets; TDE sees authorized workspace queue and own assignments; Admin sees administrative scope.
- Dashboard counts and rows must use the same filters and permission rules as the Ticket Management list.

### FR-10 — Open-days tracking and reporting

- The open-age clock starts at submitted_at; draft time is excluded.
- For an open ticket, current age is calculated as current UTC time minus submitted_at. For a closed ticket, duration is closed_at minus submitted_at. For cancelled tickets, duration ends at cancelled_at.
- Store timestamps as the source of truth; compute age/duration from timestamps to avoid stale counters.
- Display age as whole elapsed calendar days plus hours (for example, **3 days 5 hours**) and expose numeric elapsed hours and elapsed calendar days in reports.
- A ticket is open until Closed or Cancelled. On Hold time remains part of total age; report on-hold duration separately.
- Reopened tickets retain the original submission time; report total elapsed lifecycle duration and cumulative closed intervals separately. The current open age after reopening is from original submitted_at per lifecycle duration; show prior closure interval(s) so the metric is auditable. (If business stakeholders instead want active-work time excluding closed intervals, retain it as a separate derived metric, not a replacement.)
- Report fields: ticket ID, title, workspace, project, requester, assignee, status, priority, submitted_at, current age/final duration, on-hold duration, needed_by, closed_at, linked dataset/version, and closure actor.
- Support filters by date range, status, workspace, project, requester, assignee, priority, and age bucket; CSV export is permission-scoped.
- Age buckets for MVP1: under 1 day, 1–3 days, 4–7 days, 8–14 days, over 14 days. Use elapsed hours for boundary calculation; make bucket labels configurable later.
- Do not claim SLA compliance from the needed-by date alone. Needed-by attainment is a separate requested-date metric; configured SLA targets, if introduced, require explicit workspace policy.

### FR-11 — Notifications

- Notify TDE queue on submission; notify the requester and affected TDE users when assignment changes; notify the other ticket participant when a comment needs a response.
- The ticket notification panel shows assignment changes and action-needed comments, not every ticket status update.
- Notify the requester on clarification request, status change to In Progress, closure, cancellation, and reopen.
- Notification delivery failure must not roll back a committed ticket action. Record delivery outcome and allow retry under existing notification infrastructure.
- Notifications contain ticket ID, title, status, event summary, and a secure link. Do not include sensitive test data or PII.
- Avoid duplicate notifications for idempotent retries.

### FR-12 — Search, filter, sort, export

- Search by ticket ID and title; full text across descriptions/comments is optional and permission-scoped.
- Filter by status, workspace, project, requester, assignee, priority, submitted/closed date, needed-by date, and age range.
- Sort by submitted time, needed-by time, priority, age, last updated, and status.
- Export current filtered results to CSV; include a generated-at timestamp and applied filters. Respect the same row and field-level access rules as the UI.

## 9. Business rules and validations

1. Only an authenticated QA Engineer may submit a request as requester. Requester identity is derived from SSO.
2. A ticket is routed only to TDEs authorized for its workspace/project. Admin can inspect according to administrative scope.
3. Workspace/project references, dataset references, and linked versions must be validated server-side on every read and mutation.
4. A ticket cannot be Closed without a linked eligible dataset, a fulfillment summary, a valid requester access check, and a successful share action.
5. A ticket cannot be Closed while status is Draft, Submitted, or On Hold.
6. Submitted request fields are immutable to QA except through an explicit clarification/update mechanism that records before/after values and is visible to TDE.
7. Record count must be > 0 and within configured limits. Needed-by must be a valid future date/time at submission unless workspace policy permits urgent retroactive requests.
8. Test window end must be later than start. Date/time inputs must include or inherit an explicit timezone.
9. Priority Urgent requires a justification.
10. Status and assignment changes require authorization and are recorded with actor and timestamp.
11. Concurrent updates use optimistic locking/version checks; stale updates return a conflict and refresh the ticket.
12. Dataset deletion, revocation, or access changes after closure do not erase ticket evidence. Show current dataset availability and preserve the historical link/version.
13. Ticket and event retention follows TIGR's configured audit/data retention policy; no permanent hard deletion through standard UI.

## 10. User interface requirements

### 10.1 Navigation

Add **Test Data Tickets** to the primary TIGR navigation. Provide entry points from:
- Dashboard ticket cards
- Dataset detail page (optional **Request changes/additional data** action, pre-populating dataset reference)
- Ticket inbox/list
- Ticket detail page

### 10.2 Ticket list / inbox

Columns: Ticket ID, Title, Workspace/Project, Requester, Assignee, Status, Priority, Needed by, Submitted, Open age, Last updated, Linked dataset (when available).

- Show role-appropriate tabs: QA **My Requests**; TDE **Queue**, **Assigned to Me**, **All Open**; Admin **All Tickets**.
- Highlight overdue needed-by date without implying SLA breach.
- Show open-age buckets and allow age sorting.
- Use pagination or virtualized lists for large result sets.
- CSV export action is available only for rows the user is authorized to see.

### 10.3 Ticket detail

Show:
- Ticket identity, status, priority, requester, assignee, workspace/project, dates, and current age/final duration.
- Request summary and all request criteria.
- Activity timeline with event type, actor, local timestamp, and event details.
- Comments and clarification responses.
- Linked dataset/version and current dataset state.
- TDE action panel for acknowledge/start, assign/reassign, block/request clarification, resume, link dataset, share and close, cancel, reopen.
- QA action panel for draft edit, submit, clarification response/comment, and permitted cancellation.
- Admin actions only within authorized scope and with required reason.
- Confirmation dialog for cancel, close, and reopen. Close dialog summarizes linked dataset/version, share target (requester), and fulfillment note.

### 10.4 Accessibility and usability

- Status must be represented by text and color/icon; color alone is insufficient.
- Forms provide inline validation, keyboard navigation, labels, and accessible error summaries.
- Display dates with timezone context where ambiguity is possible.
- Warn before navigating away from unsaved Draft edits or comments.

## 11. Data model proposal

Use PostgreSQL for transactional ticket data and existing TIGR audit storage/patterns. Keep dataset and user references as foreign keys where services share a database; otherwise store stable IDs and validate through service APIs.

### 11.1 `test_data_tickets`

Core columns:
- `id` UUID primary key
- `display_id` unique text
- `title`, `description`
- `request_type`, `environment`, `application`, `scenarios` JSONB/text
- `schema_reference`, `record_count`, `data_source_preference`
- `data_date_from`, `data_date_to`, `relationships`, `masking_policy_id`, `acceptance_criteria`
- `needed_by`, `test_window_start`, `test_window_end`
- `requester_user_id`, `assignee_user_id`
- `workspace_id`, `project_id`
- `priority`, `status`
- `linked_dataset_id`, `linked_dataset_version_id`
- `fulfillment_summary`, `closure_actor_user_id`
- `created_at`, `submitted_at`, `updated_at`, `closed_at`, `cancelled_at`, `reopened_at`
- `row_version` integer for optimistic concurrency
- `correlation_id`

Add indexes for display_id, workspace/project/status, requester/status, assignee/status, submitted_at, needed_by, closed_at, priority, and linked_dataset_id.

### 11.2 `ticket_events`

- `id` UUID primary key
- `ticket_id` foreign key
- `event_type`, `actor_user_id`, `actor_role`
- `occurred_at` UTC timestamp
- `from_status`, `to_status`, `reason`
- `event_data` JSONB with safe before/after metadata
- `workspace_id`, `project_id`, `correlation_id`
- `related_dataset_id`, `related_dataset_version_id`, `related_job_id`
- append-only database permissions/pattern aligned to TIGR audit implementation

Indexes: ticket_id + occurred_at; workspace/project + occurred_at; actor + occurred_at; event_type + occurred_at.

### 11.3 `ticket_comments`

- `id` UUID primary key
- `ticket_id` foreign key
- `author_user_id`, `author_role`
- `body`
- `created_at` UTC timestamp
- `visibility_scope`
- Optional attachment references
- Append-only in MVP1

### 11.4 `ticket_block_periods`

- `id` UUID primary key
- `ticket_id` foreign key
- `started_at` UTC timestamp
- `ended_at` nullable UTC timestamp
- `reason`
- `requested_by_user_id` (TDE)
- `resolved_by_user_id` nullable
- Supports multiple on-hold intervals; an open interval has ended_at null.

### 11.5 Derived metrics

Calculate from authoritative timestamps:
- `current_age_seconds = now - submitted_at` for open tickets.
- `final_duration_seconds = terminal_at - submitted_at` for closed/cancelled tickets.
- `on_hold_duration_seconds = sum(ended_at - started_at)`, with the current open on-hold interval measured through now.
- `closed_interval_seconds` from prior closure intervals after reopen, retained through events or a closure-period table.
Do not maintain a periodically incremented age counter as the system of record.

## 12. API/service requirements

Names are illustrative; align with existing TIGR API conventions.

- `POST /api/tickets` — create Draft; authenticated QA identity.
- `POST /api/tickets/{id}/submit` — validate and submit; idempotency key supported.
- `GET /api/tickets` — scoped list with filters, sorting, pagination.
- `GET /api/tickets/{id}` — scoped detail with computed aging and linked dataset.
- `PATCH /api/tickets/{id}` — edit Draft or permitted fields using row_version.
- `POST /api/tickets/{id}/assign` — assign/reassign TDE with reason.
- `POST /api/tickets/{id}/comments` — append comment.
- `POST /api/tickets/{id}/request-clarification` — set On Hold and append reason/question.
- `POST /api/tickets/{id}/resume` — On Hold → In Progress.
- `POST /api/tickets/{id}/link-dataset` — link/update dataset/version with validation and event.
- `POST /api/tickets/{id}/close` — atomically share/link dataset, record fulfillment, close, emit notification event.
- `POST /api/tickets/{id}/cancel` — cancel with reason.
- `POST /api/tickets/{id}/reopen` — reopen with reason.
- `GET /api/tickets/{id}/events` — scoped event timeline.
- `GET /api/reports/tickets` — scoped aggregate/report data.
- `GET /api/reports/tickets/export` — CSV export of scoped rows.

All mutations require authorization, validation, correlation IDs, audit events, and concurrency checks. Return consistent validation errors and do not expose internal connection details or secrets.

## 13. Reporting definitions

| Metric | Definition |
|---|---|
| Open tickets | Count of Submitted, In Progress, and On Hold tickets |
| Current open age | Current UTC time − submitted_at |
| Closed duration | closed_at − submitted_at; for reopened tickets, show the latest lifecycle duration and prior closure periods separately |
| Average / median closure duration | Calculated over tickets closed in selected reporting window |
| On Hold count | Count currently in On Hold status |
| On Hold duration | Sum of all on-hold intervals; current interval runs through report time |
| Needed-by attainment | Closed at or before needed_by; report separately from SLA |
| Aging bucket | Based on elapsed hours from submitted_at, with thresholds in FR-10 |
| Reopen rate | Reopened tickets / tickets closed in selected period, with period definition shown |

Reports must define the selected date field (submission date or closure date) in the UI/export and use UTC boundaries with user-local display.

## 14. Non-functional requirements

- **Security:** SSO identity, RBAC, workspace/project scoping, least privilege, safe output encoding, CSRF/API protections consistent with TIGR.
- **Privacy:** Do not store actual generated/extracted records in ticket tables. Mask or omit sensitive content in logs and exports.
- **Auditability:** Ticket timeline and audit events are durable, append-only, time-stamped, correlated to jobs and datasets.
- **Reliability:** Ticket transitions and close action are transactional. Notification failures do not lose ticket changes.
- **Performance:** Ticket list/detail and dashboard summaries should meet existing TIGR API/UI performance targets; use pagination and indexed query patterns.
- **Concurrency:** Prevent lost updates using row_version/ETag or equivalent.
- **Time:** Persist UTC; render local timezone; calculate age from exact timestamps, not local date truncation.
- **Retention:** Follow configured TIGR data/audit retention and export controls.
- **Observability:** Emit structured logs and metrics for create/submit/close failures, notification failures, backlog, and aging; do not include ticket body or PII in operational logs.

## 15. Acceptance criteria

1. QA can create a Draft, submit it, and see a generated ticket ID and Submitted status.
2. Submission routes the ticket to the authorized TDE queue; unauthorized TDEs cannot view it.
3. QA dashboard lists only the QA user's tickets; TDE dashboard shows authorized queue and assignments; Admin dashboard shows only administrative scope.
4. TDE can assign, start work, request clarification, and resume a ticket; each transition appears in the timeline with actor and timestamp.
5. On Hold tickets remain open and their on-hold duration is independently measurable.
6. TDE can link a valid dataset/version; an invalid, unavailable, or unauthorized dataset is rejected.
7. TDE cannot close a ticket without an eligible linked dataset and fulfillment summary.
8. A successful close links the dataset/version, records the sharing/fulfillment details, stamps closed_at, changes status to Closed, writes an audit event, and notifies QA.
9. QA cannot close the ticket and cannot edit submitted request details without using the audited clarification/change mechanism.
10. Open age begins at submitted_at and updates from timestamps; closed duration is stable and reportable.
11. Reopening preserves prior closure history and resumes the age calculation from the original submission.
12. Ticket list supports role-scoped filtering, sorting, pagination, and CSV export.
13. Ticket event history is chronological, append-only, and visible to authorized participants.
14. Ticket mutations reject stale row_version updates and invalid status transitions.
15. No ticket API, dashboard, event, or export exposes data outside the caller's workspace/project authorization.
16. Repeated submission/close requests with the same idempotency key do not create duplicate records or notifications.
17. Date/time displays show a consistent local timezone; reports use exact UTC timestamps for durations.

## 16. MVP1 delivery boundary

Included:
- Ticket creation, draft, submission, queue, assignment, status workflow, comments/clarification, dataset link, share-and-close, cancellation/reopen, timeline, dashboards, aging, reports, CSV export, notifications, authorization, audit, and PostgreSQL persistence.

Not required for MVP1 unless already available in TIGR:
- External ITSM integration, email-to-ticket, SLA calendar/holiday engine, automated priority prediction, multi-level approvals, arbitrary workflow designer, attachments, multiple primary datasets per ticket, or billing/chargeback.
- Do not introduce a new approval gate for ticket fulfillment. Existing dataset approval, masking, reservation, lock, and provisioning policies continue to apply independently.

## 17. Implementation notes for Codex

1. Inspect the existing repository and follow its current React/TypeScript/Vite, FastAPI, PostgreSQL, Neo4j, authentication, authorization, audit, dashboard, notification, and dataset conventions.
2. Reuse existing workspace/project, user, dataset, dataset version, job, audit, and notification models/services where possible; do not duplicate them in the ticket module.
3. Add database migrations with indexes and constraints. Include safe downgrade behavior consistent with the repository's migration policy.
4. Implement server-side role/scope checks and transition validation before wiring UI actions.
5. Make submit, close, cancel, and reopen operations transactional and idempotent where applicable.
6. Use server-computed ticket aging in API responses and report queries; do not trust a client-computed age.
7. Add dashboard widgets and a dedicated list/detail experience using the existing TIGR design system and role navigation.
8. Connect the closed ticket to the exact dataset version shared; retain the link if a later version is published.
9. Add meaningful tests for authorization boundaries, transition rules, close validation/atomicity, aging calculations, on-hold intervals, reopening, and CSV scope. Avoid tests that merely duplicate trivial UI rendering.
10. Update the TIGR API documentation, data model documentation, dashboard documentation, and user journey/feature backlog to reflect the module.
