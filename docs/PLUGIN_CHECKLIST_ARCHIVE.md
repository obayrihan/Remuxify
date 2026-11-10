# Archived plugin workstream

Archived 11 November 2026 after the user selected standalone Remuxify with no plugin dependency. Historical implementation and validation are retained below; plugin packaging and further native UI work are outside the standalone release scope.

# Remuxify — grouped implementation checklist

Created: 9 November 2026. Reorganized: 10 November 2026.

Status reconciled against the first plugin milestone on 10 November 2026. Draft review: https://github.com/obayrihan/Remuxify/pull/1. Checked implementation items do not imply production deployment or completion of the separate acceptance tests below.

**Chosen name: Remuxify.** The live 10.11.8 server now loads the plugin, and its existing Python worker is paired in plugin mode. The deployment directory, database, encrypted credentials, conversion engine, and SYSTEM task remain in place. The sanitized standalone baseline remains on `main`; migration work is on `feature/jellyfin-plugin`. See `docs/PLUGIN_MIGRATION.md` and `docs/PLUGIN_VALIDATION.md`.

### Stopping point — 10 November 2026

- [x] Install the matching 10.11.8 plugin and confirm the Remuxify page opens inside Jellyfin through the renamed main-menu shortcut. The user's screenshot confirmed the native page and embedded dashboard.
- [x] Back up the stopped worker, deploy the plugin integration files, pair its local service channel, and restart only its existing task with the queue idle and automatic queueing disabled.
- [x] Verify private state/settings/history APIs, rejection of anonymous worker access, disabled standalone login, unchanged job history, and unchanged encrypted qBittorrent credentials. No media job was retried or processed during cutover.
- [ ] Tomorrow: refresh the native page and confirm that Reconnecting clears, library counts and telemetry populate, and queue/history/settings match the existing deployment.
- [ ] Polish the native page so the connected dashboard is the main view and connection controls can be collapsed. Review the nested dashboard navigation and scrolling with the user.
- [ ] Run a disposable-media conversion through the rendered native UI, then verify playback, validation/replacement, qBittorrent protection, and playback policy. Leave wider acceptance and packaging work below pending.

Public maintenance has been removed at the user's request, and public Jellyfin access is verified. The separately protected private hostname remains available, including during future maintenance. The temporary maintenance task was removed. Machine-specific routing, backups, and operational receipts stay outside Git.

Workstreams below are ordered **largest to smallest estimated implementation effort**, including their associated tests. Sizes are relative estimates, not time commitments. This is a size ranking rather than a strict execution sequence: perform the safety checks and create a clean source checkout first; resolve the plugin compatibility target before implementing it; publish after the release checks pass. Optional enhancements are explicitly marked and are not required for the first plugin release.

## Safety and dependencies — source now, production backup before cutover

- [x] Inspect the current Forge queue and any independent HandBrake work. Historical job IDs in earlier notes are not current status. Do not interrupt HandBrake or restart a worker that is encoding, validating, or committing.
- [ ] Review a completed production job's validation receipt and confirm playback on the originally affected client.
- [x] Back up worker settings, the SQLite database, queue/recovery data, encrypted credentials, and replaced source before the live worker cutover. Keep the local backup outside version control.
- [ ] Make and test a full Jellyfin configuration/database backup before a future server upgrade; retain the worker rollback backup.
- [x] Create a clean source checkout with local runtime data excluded before changing code. Select the repository destination, visibility, and license before publication; see workstream 8.

## 1. Native Jellyfin plugin integration — XL

**Goal:** an administrator uses Remuxify inside Jellyfin, with Jellyfin authentication and a private conversion worker.

**User-approved architecture:** a C# Jellyfin plugin paired with the existing Python worker. Preserve the tested conversion engine during the first migration.

### Compatibility and development environment

- [x] Recheck the latest stable Jellyfin release and pin the server/API target. A previous public release check reported **12.4**. Recheck the release and locally installed version before selecting compatible builds. Do not upgrade production Jellyfin as a side effect of development.
- [x] Support both **10.11.8** and latest stable **12.4**, as approved by the user, with separate net9.0/net10.0 builds. Keep the existing Python worker as a separate local service; do not upgrade production during development.
- [x] Match the .NET SDK and Jellyfin packages to the chosen release. The inspected Jellyfin 12.4 Controller project targets .NET 10; check the installed SDK locally before building.
- [x] Review the official plugin template and update its dependencies for the target release. The template inspected on 9 November still specified net9.0/Jellyfin 10.11.5.
- [x] Establish a disposable Jellyfin development instance for build and integration testing.

### Dashboard, authorization, and worker protocol

- [x] Create the C# plugin and embedded admin dashboard/configuration page, accessible through Dashboard → Plugins → Remuxify. Native servers discover the plugin and serve its resources on both target versions.
- [x] Implement the required Remuxify main-menu shortcut in place of Media Forge, opening the native plugin page in the current Jellyfin tab. Install a scoped web-shell hook, preserve unrelated links, and reapply it through the boot integration.
- [ ] Complete rendered shortcut navigation, back/forward behavior, and dashboard interactions in both web clients, including after a web-client update. Native shortcut opening on the live 10.11.8 client is user-confirmed; broader browser acceptance remains pending.
- [x] Require Jellyfin administrator authorization on browser-facing plugin data/action endpoints, including inventory, settings, queue/history, logs, and mutations. Authenticate private worker callbacks separately with the service credential; public static assets contain no user data. Use Jellyfin's existing session rather than a second browser login.
- [x] Define a versioned plugin/worker protocol and authenticate local commands using a protected channel or service credential. Loopback alone is not authentication. Restrict file operations to current Jellyfin libraries.
- [x] Forward authorized browser requests through Jellyfin, keeping worker credentials out of the browser and the worker API private.
- [x] Connect the native dashboard/API to the existing out-of-process worker, GPU capability/state reporting, queue, and unavailable/pairing error handling. Conversion remains outside Jellyfin request threads.
- [ ] Complete portable worker service/startup management and verify worker restart/reconnect behavior with the plugin.
- [x] Reuse the existing dashboard views and conversion engine. Verify the native API path with synthetic conversions preserving all video frames, surround audio, and full-validation receipts on both target versions.
- [ ] Complete rendered feature-parity acceptance for overview, inventory/filtering, rules, queue/history, logs, settings, and telemetry, plus the integrated recovery/qBittorrent/playback cases below.

### Dynamic Jellyfin data integration

- [x] Supply library roots and playback information through Jellyfin services. Preserve discovery when library locations change, recursive filesystem watching, periodic reconciliation, and file-settling checks. Never hardcode media directories.
- [x] Remove the dependency on reading an API key from Jellyfin's database in plugin mode. The existing worker now uses native plugin context; legacy standalone mode remains available as a rollback option.
- [x] Verify native administrator access, anonymous/viewer rejection, immediate privilege revocation, library additions, API commands, and synthetic conversion against both isolated native servers.
- [ ] Verify rendered native navigation, library removals, and worker restart/reconnect against both targets.

**Current milestone:** both-target development checks passed, and the user-approved 10.11.8 worker cutover is complete. The native page opens and the private worker APIs are healthy. Continue tomorrow with the rendered connected dashboard and disposable UI-driven conversion; do not treat this as completed release acceptance.

## 2. Conversion recovery and progress preservation — L

**Goal:** avoid repeating completed encoding work while preserving exact validation and safe replacement.

- [ ] Persist a recoverable **encoded, awaiting validation** stage. A resource shortage or shutdown during validation should retain the finished encode for later validation rather than force another encode.
- [ ] Verify source/output identity and integrity before reusing a retained encode. Re-run full validation as required, and persist the successful hash/validation receipt before any original is replaced.
- [ ] Extend recovery to cover every boundary between encoding, awaiting validation, validating, and committing. Keep ambiguous files for inspection.
- [ ] Show retained progress, the current phase, and the reason for waiting accurately. Distinguish pausing new jobs from stopping/retrying active work.
- [ ] Test resource interruptions, shutdown/reboot, changed sources/outputs, and retained-output reuse with disposable media. Preserve the existing file-lock and commit-journal recovery guarantees.
- [ ] **Optional, only if selected:** implement manual pause/resume of a running FFmpeg encode without losing progress. Test NVDEC/NVENC suspension/resumption, retained RAM/VRAM, pause-aware watchdogs and ETA, cancellation, shutdown, and source protection. Distinguish an in-memory pause from recovery after reboot. Do not reintroduce automatic playback interruptions.
- [ ] If manual pause is implemented, add explicit active-processing controls and tests for cancellation/shutdown while suspended.

## 3. Portable installation, upgrades, and deployment migration — L

**Goal:** install on another supported host without this machine's paths, username, hostname, or Cloudflare setup.

- [ ] Replace deployment-specific values in `install.ps1`, `restart-forge.ps1`, `start-local.ps1`, `integration.py`, and the launcher: Python path, Jellyfin web/data locations, Cloudflare configuration, installation directory, hostname, and ports. These are deployment values; media roots are already discovered dynamically.
- [ ] Use configuration and safe dependency discovery, with clear errors when required programs or paths cannot be found.
- [ ] Separate worker installation from optional public routing. Do not require Cloudflare Tunnel on other installations.
- [ ] Declare the initial supported operating systems and GPUs honestly. Startup integration and DPAPI credential storage are currently Windows-specific; do not claim untested cross-platform support.
- [ ] Preserve settings, encrypted credentials, queue history, recovery journals, and job-owned files through upgrade and rename migrations. Keep secrets local; document re-entering credentials on a different host.
- [ ] Provide startup/health management and documented update, uninstall, and rollback procedures without interrupting active conversions.
- [ ] Coordinate directory, service/task, and URL changes with workstream 7. Do not silently abandon the old queue or journals.
- [ ] After the plugin path is verified end to end, retire the old web hotlink/launcher and separate `/forge/` public route with a rollback plan. Keep the working deployment until then.

## 4. Release validation, CI, and plugin distribution — M–L

**Goal:** a reproducible, tested plugin release with explicit compatibility and installation instructions.

- [ ] Add hardware-independent build/API/configuration checks to ordinary CI. Run GPU integration tests only on a suitable trusted test host with disposable media.
- [ ] Re-run conversion/recovery checks: incomplete downloads, active locks, low storage/resources, cancellation, reboot, changed sources/outputs, and interrupted commit. Include the new recovery-stage tests from workstream 2.
- [x] Test unauthenticated/non-admin rejection, revoked administrator sessions, worker-channel authentication, allowed proxy routes, rejected external worker endpoints, and restricted asset names. Keep service credentials out of browser responses and committed files.
- [ ] Complete native browser/request-forgery and base-URL acceptance, and the wider filesystem/recovery path checks for release.
- [ ] Verify install, restart, update, uninstall, and rollback on every advertised Jellyfin/platform combination, including the latest stable target.
- [ ] Verify qBittorrent and playback policies in the complete plugin-to-worker flow, as specified in workstream 5.
- [x] Produce separate local development-preview ZIPs and checksums for 10.11.8 and 12.4, with ABI-specific metadata. Document that the Python worker remains required.
- [ ] Prepare and verify the release/catalog repository manifest after acceptance. No plugin catalog or GitHub release is published yet.
- [x] Document preview setup/pairing, protocol/authentication, compatibility, test evidence, and known acceptance limits in `docs/PLUGIN_MIGRATION.md` and `docs/PLUGIN_VALIDATION.md`.
- [ ] Finish production installation, existing-data migration, update/uninstall, and rollback documentation after those flows are tested.
- [ ] Complete end-to-end acceptance on the disposable server before production cutover or announcing plugin availability.

## 5. Playback, queue controls, and qBittorrent coordination — M

**Goal:** conversions coexist with viewing, while watched files and torrent-managed sources remain protected.

### Already implemented — preserve these behaviors

- [x] Playback no longer cancels/restarts an active encode or validation, including when playback monitoring fails mid-job.
- [x] The ordinary **Wait to start while Jellyfin is playing** setting gates new work only. With it off, unrelated playback does not prevent starting a conversion.
- [x] **Force transcode during playback** is available and defaults off. It overrides playback-related start waits, including for the watched source, while keeping resource checks enabled.
- [x] Before replacing a source, require a fresh playback check. Wait if that exact file is being watched or its playback status cannot be checked, retaining the fully validated output. Unrelated playback does not block replacement.
- [x] qBittorrent stops only completed torrents containing the exact source path, confirms the stopped state, and leaves unrelated torrents running. A matching multi-file torrent stops as a whole and stays stopped because converted content has different hashes.
- [x] A verified output survives file-lock waits and can be committed without encoding again. The local qBittorrent connection is configured and verified.

### Remaining work

- [ ] Refine detection of active, paused, and stale playback sessions. Define the paused-client policy explicitly and always protect an in-use source from replacement.
- [ ] Review the wording and interaction of the wait/force/queue-pause controls so users can predict which jobs start and which operations still wait.
- [x] Wire the existing playback policies to native Jellyfin session context without restoring playback-triggered cancellation or changing the paused-session policy.
- [ ] Test playback beginning/ending during encoding, validation, and replacement; watched versus unrelated files; monitoring failures; and each wait/force setting combination.
- [ ] Verify qBittorrent exact-path matching, completed versus incomplete downloads, stop confirmation, unavailable API, and lock release. Never automatically resume, recheck, or delete torrents or their data.

## 6. Encoding quality and large-resolution presets — M, optional scope

**Goal:** decide and document appropriate presets before advertising high-resolution conversions.

Current baseline: P5, CQ 23, 20 Mbps video ceiling, 8-bit HEVC, original audio copied. Resolution is preserved where supported, but encoding is lossy; HDR/Dolby Vision inputs remain blocked.

- [ ] Decide whether an 8K/source-bit-depth preset belongs in the first release or a later release. This is not yet an approved requirement to add HDR support.
- [ ] If selected, preserve the requested source bit depth, choose appropriate bitrate limits, check GPU capabilities and storage estimates, and evaluate representative samples before allowing permanent replacement.
- [x] Document that encoding is lossy and structural/full-decode validation does not prove equivalent visual quality. The current 20 Mbps/8-bit defaults are not advertised as a same-quality 8K profile.
- [ ] Include preset-specific P4/P5 speed/compression guidance when the quality/preset documentation is expanded.
- [ ] Validate playback and output quality on the affected clients before advertising the new preset.

## 7. Remuxify naming and branding — S–M

- [x] Choose **Remuxify** to replace Media Forge; the user identified an existing media organiser named MediaForge.
- [ ] Check existing projects, repositories, and Jellyfin plugins for conflicts with Remuxify before publishing. Availability has not been verified.
- [x] Use Remuxify for the repository, native plugin display name/namespace, preview package names, and migration documentation.
- [ ] Finish consistent dashboard/logo branding, installer/service/task labels, and release-manifest branding while preserving the existing live installation until a reviewed migration.
- [ ] Coordinate any directory, task/service, and URL migration with workstream 3, preserving configuration, encrypted credentials, queue history, and recovery journals. Do not rename the deployed installation during active work.

## 8. Git source preparation and publication — S

- [x] Select the private repository `obayrihan/remuxify` and prepare the standalone version-control baseline before plugin development.
- [ ] Select a project license before an open-source release.
- [x] Prepare a clean, privacy-sanitized standalone source checkout with `config.example.json`, setup documentation, tests, and this checklist. Runtime data and credentials are excluded.
- [x] Verify exclusions for `.dpapi` and pairing secret/temporary files, databases, logs, media, local configuration, backups, staging, and build artifacts before both milestone pushes. Repeat this check for future commits.
- [x] Review the initial staged baseline for personal data, API tokens, credentials, cookies, local configuration, and generated runtime files. Repeat this review for future commits; `.gitignore` does not remove previously tracked files or secrets from history.
- [x] Commit and push the standalone baseline (`7ddfacc`) and first plugin preview (`b8289b8` on `feature/jellyfin-plugin`). Open draft PR #1; keep the standalone baseline on `main` until review.
- [ ] After compatibility, packaging, and release checks pass, set the selected remote and push. Do not advertise an unimplemented plugin as available.

## Working baseline and historical verification

- [x] The existing SYSTEM scheduled task starts the standalone dashboard on boot; Jellyfin links to it under `/forge/`.
- [x] Media locations come from Jellyfin's `/Library/VirtualFolders` API, with library XML fallback. Roots refresh every 30 seconds, with recursive watching and periodic reconciliation. Media paths are not hardcoded.
- [x] NVDEC/NVENC, timestamp precision, explicitly discarded preroll accounting, audio preservation, full output decoding, exact playable-frame checks, durable hash receipts, and recoverable replacement work.
- [x] CPU/disk telemetry is fixed and active.
- [x] Windows machine DPAPI and explicit restricted file permissions protect locally stored qBittorrent credentials. Runtime data, credentials, host configuration, backups, and staging are excluded from Git; ignore behavior was checked.
- [x] The qBittorrent update passed all 61 tests; its evidence is in `data/diagnostics/qbittorrent-tests.log`. Later playback changes passed 13 targeted tests, and the force-start option passed 17 targeted tests. These are separate historical runs, not a claim that a new full suite has run today.
- [x] Production replacement was reported working and completed jobs were observed. Validate client playback and current job status again before migration.

Queue state, installed software versions, settings, and running HandBrake work must be checked again before deployment actions. Check `data/restart-result.json` for loaded-source hashes. The standalone baseline was pushed before migration. The first plugin development milestone has since been implemented and tested; production Jellyfin has not been upgraded.

## References and next step

- Latest stable release (recheck before pinning): https://api.github.com/repos/jellyfin/jellyfin/releases/latest
- Release inspected on 9 November: https://github.com/jellyfin/jellyfin/releases/tag/v12.4
- Inspected target framework/package version: https://github.com/jellyfin/jellyfin/blob/v12.4/MediaBrowser.Controller/MediaBrowser.Controller.csproj
- Official plugin template: https://github.com/jellyfin/jellyfin-plugin-template
- Plugin installation: https://jellyfin.org/docs/general/server/plugins/

Resume prompt: **Read the stopping-point section in `TOMORROW_CHECKLIST.md`, plus `docs/PLUGIN_MIGRATION.md` and `docs/PLUGIN_VALIDATION.md`, on `feature/jellyfin-plugin`. The live 10.11.8 plugin and existing worker are paired; public access is restored and the protected private hostname remains available. First verify the connected native dashboard and telemetry, then discuss page layout and run a disposable UI-driven conversion. Preserve the existing data, credentials, Python worker architecture, and conversion/playback safeguards. Do not repeat the cutover or upgrade Jellyfin automatically.**
