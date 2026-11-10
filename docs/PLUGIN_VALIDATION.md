# Plugin development-preview validation

Verified 10 November 2026. These are development checks, not approval for a production cutover.

| Check | Jellyfin 10.11.8 / net9.0 | Jellyfin 12.4 / net10.0 |
| --- | --- | --- |
| Plugin build | Passed | Passed |
| ASP.NET API-host tests | 19 passed | 19 passed |
| Plugin discovered by an isolated native server | Passed | Passed |
| Native administrator access | Passed | Passed |
| Anonymous/viewer rejection | Passed | Passed |
| Existing session rejected after admin privilege revocation | Passed | Passed |
| Private local pairing and native context | Passed | Passed |
| Native library roots | Passed | Passed |
| Dashboard page and static resources served | Passed | Passed |
| Plugin → worker state/settings reads and settings write | Passed | Passed |
| Real synthetic conversion through the plugin API | Passed | Passed |
| Synthetic output: 48 video frames, independent full decode, six audio channels | Passed | Passed |
| Replacement of the disposable source only after validation | Passed | Passed |
| Visual browser/native-UI interaction | Pending | Pending |

The 10.11.8 disposable instance used the installed server binaries with separate data/config/cache/log/plugin directories. The 12.4 disposable instance was built from the official `v12.4` source tag, commit `83d0d0e7f76e23e2e085c8ff67d28b772d35967d`, and used separate directories. Both were bound to loopback, with auto-discovery and UPnP disabled. Neither used production databases, accounts, media, qBittorrent credentials, or queue history.

Python validation additionally passed 57 non-GPU unit/integration tests covering the existing API, filesystem watching, permissions, qBittorrent coordination, telemetry/resource guards, native bridge authentication, protocol validation, and pairing configuration. Existing worker hardware tests remain in the suite; the native synthetic conversions above verify the new plugin path without repeating the entire hardware suite.

Development-only test accounts, pairing files, tokens, runtime databases, raw logs, and machine-specific reports are outside the source checkout and are not committed. Published summaries intentionally omit those values.

## Separate live deployment checkpoint — 10 November 2026

After the isolated checks, the user installed the 10.11.8 build and confirmed the native page and embedded dashboard in a screenshot. A separately approved worker cutover backed up the stopped worker configuration, database, encrypted credentials, recovery data, and replaced source. The existing worker was paired in plugin mode and restarted using its existing SYSTEM task. Jellyfin was not upgraded or restarted by this cutover; independent HandBrake work was not stopped.

Twenty-four relevant API/authentication, private-bridge, pairing, and integration-planning checks passed in an isolated worker directory before deployment. Live private worker state/settings/history/credential-status reads passed; anonymous access was rejected and standalone login disabled. The migration verified unchanged job history, encrypted qBittorrent credentials, and existing presets. It did not retry or process any media job. Native bridge context was available from Jellyfin services.

The rendered dashboard showed Reconnecting before the worker cutover. Its connected state, complete visual interactions, and UI-driven conversion still require user acceptance; they are not counted as passed by the private HTTP checks. Live backup paths and receipts remain local and excluded from this repository.

Remaining acceptance work includes rendered UI/navigation on both clients, base-URL hosting, UI-driven conversion, packaging/install/update/rollback on a disposable deployment, and a reviewed migration plan for production settings, credentials, and recovery journals. No production service restart, Jellyfin upgrade, tunnel change, or real-media conversion was performed for these checks.
