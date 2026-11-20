# TIGR MVP1 Phase 2 Implementation Report

## Scope

Phase 2 adds identity context, workspace administration, role and scoped access management, activity visibility, and the Dashboard foundation described in the phased implementation plan.

## Delivered

- Added the `iam` schema for user identities, platform administrators, workspaces, workspace memberships, scoped access grants, and distinct service identities.
- Added migrations `0003_identity_workspaces_access` and `0004_job_actor_role`.
- Added explicit development and OIDC authentication modes. Development role headers are accepted only when both `TIGR_ENV=development` and `TIGR_AUTH_MODE=development`. OIDC mode validates bearer tokens using issuer, audience, JWKS, and configured signing algorithms.
- Added `tigr-admin bootstrap-admin <OIDC-subject>` for initial platform administrator setup.
- Added workspace/member/grant/service identity endpoints, including time-bound membership and grants, revocation, audit events, and protection against removing the last active workspace Admin.
- Added `/api/v1/me`, `/api/v1/activities`, and `/api/v1/dashboard`; activity visibility is applied before search, count, and pagination. Dashboard job counts are role-filtered. Metrics for later workflow capabilities report as unavailable.
- Added the Dashboard, workspace selection, Activities & Audit, and Admin Access & Roles UI. The development role selector is labeled as a local preview fixture.
- Updated setup documentation and added Phase 2 authorization tests.

## Verification

The phase checkpoint includes backend authorization tests for role access, activity visibility, dashboard job scope, expired/revoked grants, and service identity scopes, plus frontend production build and backend lint checks.

Database migrations were applied to the local `TIGR` database on LocalDBServer. The local app uses UI port `5174` and API port `8001`.

## Integration still required

No identity provider or browser client configuration was supplied. The API's OIDC bearer-token validation and administrator bootstrap command are ready for provider values, but the browser does not yet redirect to or complete an OIDC sign-in flow. Local development continues to use the development role fixture. Configure the provider issuer, audience, JWKS URL, browser client, and callback URL before treating SSO as production-ready.

The Dashboard intentionally marks data sets, reservations, locks, and connections as unavailable until their later implementation phases provide those records.
