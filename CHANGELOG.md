# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Granular role based access control on the gateway. A route declares the permission it requires, and a request is allowed only when its verified token carries that exact permission. Otherwise the gateway answers `403` with the missing scope named. `POST /v1/tools/execute` is the first secured route. Permissions are read from the namespaced Auth0 claim, so a token can only act within the scopes it was actually granted. (Spec 0005)
- Platform pages in the portal: Help center, Agent runtime, Evaluations, and Models and routing. Added routes and scaffolding for agent runtime, evaluations, and models routing views.
- API key scope editing. `PATCH /v1/keys/{key_id}` sets a key's permission scopes, so a key's access can be widened or narrowed without issuing a new credential. It is a key write, so it needs `keys:write`, and a caller may only grant scopes it holds itself, so no credential can be raised above its manager. A revoked key cannot be rescoped (`409`). Each change records an `agentshield.security.key_rotation` event carrying the new scope set. The portal keys page now shows the signed in principal's own scopes and each key's scopes, and its "Manage" control opens a real scope editor that calls this route. (Spec 0010)

### Changed
- Updated portal navigation to include platform section links and improved page titles for platform pages.

### Fixed
- UI error handling now correctly identifies gateway successes. The interceptor only marks responses as accepted on real HTTP responses, preventing Angular send events from rearming the session redirect loop. A persistently rejected token now triggers at most one redirect with a visible session notice and sign in control. Staleness notes now appear on dashboard usage and health widgets and on the keys list. (Spec 0013)
- Authorization failure telemetry now records the permission that was required, alongside the user and tenant. A denied request can be traced to the exact missing scope instead of only showing that access was refused. (Spec 0005 AC-4)
- The portal keys page no longer renders a fabricated "Workspace roles / RBAC policy" card listing hardcoded people, roles, and a Manage button with no handler. The gateway stores no members, roles, or team directory, so the card described state that never existed and the control did nothing. It is replaced by the access state the gateway actually holds: the signed in principal's own token scopes and each key's scope allowlist. (Spec 0010)