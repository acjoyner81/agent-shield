# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Granular role based access control on the gateway. A route declares the permission it requires, and a request is allowed only when its verified token carries that exact permission. Otherwise the gateway answers `403` with the missing scope named. `POST /v1/tools/execute` is the first secured route. Permissions are read from the namespaced Auth0 claim, so a token can only act within the scopes it was actually granted. (Spec 0005)
- Platform pages in the portal: Help center, Agent runtime, Evaluations, and Models and routing. Added routes and scaffolding for agent runtime, evaluations, and models routing views.

### Changed
- Updated portal navigation to include platform section links and improved page titles for platform pages.

### Fixed
- UI error handling now correctly identifies gateway successes. The interceptor only marks responses as accepted on real HTTP responses, preventing Angular send events from rearming the session redirect loop. A persistently rejected token now triggers at most one redirect with a visible session notice and sign in control. Staleness notes now appear on dashboard usage and health widgets and on the keys list. (Spec 0013)
- Authorization failure telemetry now records the permission that was required, alongside the user and tenant. A denied request can be traced to the exact missing scope instead of only showing that access was refused. (Spec 0005 AC-4)