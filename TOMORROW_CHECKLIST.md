# Remuxify — grouped implementation checklist

Created: 9 November 2026. Reorganized: 10 November 2026.

**Remuxify.** The deployed application is still named Media Forge, lives at `<deployment directory>`, and uses the existing `/forge/` URL and SYSTEM task. The plugin migration has not started. This repository records the sanitized standalone baseline for version control before plugin development.

Workstreams below are ordered **largest to smallest estimated implementation effort**, including their associated tests. Sizes are relative estimates, not time commitments. This is a size ranking rather than a strict execution sequence: perform the safety checks and create a clean source checkout first; resolve the plugin compatibility target before implementing it; publish after the release checks pass. Optional enhancements are explicitly marked and are not required for the first plugin release.

## Before implementation — safety and dependencies

- [ ] Inspect the current Forge queue and any independent HandBrake work. Historical job IDs in earlier notes are not current status. Do not interrupt HandBrake or restart a worker that is encoding, validating, or committing.
- [ ] Review a completed production job's validation receipt and confirm playback on the originally affected client.
- [ ] Back up application settings, queue/recovery journals, credentials, and Jellyfin configuration outside version control. Preserve the working deployment for rollback.
- [ ] Create a clean source checkout with local runtime data excluded before changing code. Select the repository destination, visibility, and license before publication; see workstream 8.

## 1. Native Jellyfin plugin integration — XL

**Goal:** an administrator uses Remuxify inside Jellyfin, with Jellyfin authentication and a private conversion worker.

**Proposed architecture:** a C# Jellyfin plugin paired with the existing Python worker. Preserve the tested conversion engine during the first migration.

### Compatibility and development environment

- [ ] Recheck the latest stable Jellyfin release and pin the server/API target. A previous public release check reported **12.4**. Recheck the release and locally installed version before selecting compatible builds. Do not upgrade production Jellyfin as a side effect of development.
- [ ] Target the latest stable release first. Decide whether 10.11.8 also needs a separate tested build; do not assume one binary supports both versions.
- [ ] Match the .NET SDK and Jellyfin packages to the chosen release. The inspected Jellyfin 12.4 Controller project targets .NET 10; check the installed SDK locally before building.
- [ ] Review the official plugin template and update its dependencies for the target release. The template inspected on 9 November still specified net9.0/Jellyfin 10.11.5.
- [ ] Establish a disposable Jellyfin development instance for build and integration testing.

### Dashboard, authorization, and worker protocol

- [ ] Create the C# plugin and embedded admin dashboard/configuration page, accessible through Dashboard → Plugins → Remuxify. Verify navigation in the target web client; a dedicated sidebar entry is optional and must be tested.
- [ ] Require administrator authorization on every plugin endpoint, including inventory, settings, queue/history, logs, and all mutations. Use Jellyfin's existing session rather than a second browser login.
- [ ] Define a versioned plugin/worker protocol and authenticate local commands using a protected channel or service credential. Loopback alone is not authentication. Restrict file operations to current Jellyfin libraries.
- [ ] Forward authorized browser requests through Jellyfin, keeping worker credentials out of the browser and the worker API private.
- [ ] Integrate worker health/startup, GPU capability detection, queue recovery, and clear unavailable-worker errors. Keep conversions outside Jellyfin request threads.
- [ ] Preserve the existing overview, media inventory, rules, queue/history, logs, settings, telemetry, audio preservation, validation receipts, and crash recovery.

### Dynamic Jellyfin data integration

- [ ] Supply library roots and playback information through Jellyfin services. Preserve discovery when library locations change, recursive filesystem watching, periodic reconciliation, and file-settling checks. Never hardcode media directories.
- [ ] Remove the current dependency on reading an API key from Jellyfin's database once the plugin supplies these services.
- [ ] Verify native admin navigation, session revocation, library additions/removals, worker restart/reconnect, and end-to-end commands against the target server.

**First implementation step:** compatibility spike: verify the current stable release, select supported builds, and establish the matching SDK and disposable test server. Production Jellyfin remains unchanged.

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
- [ ] Test unauthenticated/non-admin rejection, revoked sessions, request forgery, worker-channel authentication, and path restrictions. Never send service credentials to the browser or logs.
- [ ] Verify install, restart, update, uninstall, and rollback on every advertised Jellyfin/platform combination, including the latest stable target.
- [ ] Verify qBittorrent and playback policies in the complete plugin-to-worker flow, as specified in workstream 5.
- [ ] Produce a plugin release ZIP and repository manifest with compatible target versions and checksums. Explain clearly that a separate worker is required.
- [ ] Document configuration, worker installation, GPU requirements, recovery behavior, known limitations, migration, and rollback. State exact tested versions and platforms.
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
- [ ] Carry these policies into the plugin's native Jellyfin session integration without restoring playback-triggered cancellation.
- [ ] Test playback beginning/ending during encoding, validation, and replacement; watched versus unrelated files; monitoring failures; and each wait/force setting combination.
- [ ] Verify qBittorrent exact-path matching, completed versus incomplete downloads, stop confirmation, unavailable API, and lock release. Never automatically resume, recheck, or delete torrents or their data.

## 6. Encoding quality and large-resolution presets — M, optional scope

**Goal:** decide and document appropriate presets before advertising high-resolution conversions.

Current baseline: P5, CQ 23, 20 Mbps video ceiling, 8-bit HEVC, original audio copied. Resolution is preserved where supported, but encoding is lossy; HDR/Dolby Vision inputs remain blocked.

- [ ] Decide whether an 8K/source-bit-depth preset belongs in the first release or a later release. This is not yet an approved requirement to add HDR support.
- [ ] If selected, preserve the requested source bit depth, choose appropriate bitrate limits, check GPU capabilities and storage estimates, and evaluate representative samples before allowing permanent replacement.
- [ ] Explain that P4/P5 affect speed/compression tradeoffs and that successful structural/full-decode validation does not prove equivalent visual quality. Do not present the current 20 Mbps/8-bit defaults as a same-quality 8K profile.
- [ ] Validate playback and output quality on the affected clients before advertising the new preset.

## 7. Remuxify naming and branding — S–M

- [x] Choose **Remuxify** to replace Media Forge; the user identified an existing media organiser named MediaForge.
- [ ] Check existing projects, repositories, and Jellyfin plugins for conflicts with Remuxify before publishing. Availability has not been verified.
- [ ] Apply Remuxify to the dashboard, plugin display name/namespace, repository/package names, release manifest, documentation, installer, and service/task labels.
- [ ] Coordinate any directory, task/service, and URL migration with workstream 3, preserving configuration, encrypted credentials, queue history, and recovery journals. Do not rename the deployed installation during active work.

## 8. Git source preparation and publication — S

- [x] Select the private repository `obayrihan/remuxify` and prepare the standalone version-control baseline before plugin development.
- [ ] Select a project license before an open-source release.
- [x] Prepare a clean, privacy-sanitized standalone source checkout with `config.example.json`, setup documentation, tests, and this checklist. Runtime data and credentials are excluded.
- [ ] Maintain exclusions for `.dpapi` files and temporary credential files, databases, logs, media, local configuration, backups, staging, and build artifacts.
- [x] Review the initial staged baseline for personal data, API tokens, credentials, cookies, local configuration, and generated runtime files. Repeat this review for future commits; `.gitignore` does not remove previously tracked files or secrets from history.
- [ ] Commit reviewed changes with clear milestones. If publishing a standalone checkpoint before the plugin is finished, label it as the standalone baseline.
- [ ] After compatibility, packaging, and release checks pass, set the selected remote and push. Do not advertise an unimplemented plugin as available.

## Working baseline and historical verification

- [x] The existing SYSTEM scheduled task starts the standalone dashboard on boot; Jellyfin links to it under `/forge/`.
- [x] Media locations come from Jellyfin's `/Library/VirtualFolders` API, with library XML fallback. Roots refresh every 30 seconds, with recursive watching and periodic reconciliation. Media paths are not hardcoded.
- [x] NVDEC/NVENC, timestamp precision, explicitly discarded preroll accounting, audio preservation, full output decoding, exact playable-frame checks, durable hash receipts, and recoverable replacement work.
- [x] CPU/disk telemetry is fixed and active.
- [x] Windows machine DPAPI and explicit restricted file permissions protect locally stored qBittorrent credentials. Runtime data, credentials, host configuration, backups, and staging are excluded from Git; ignore behavior was checked.
- [x] The qBittorrent update passed all 61 tests; its evidence is in `data/diagnostics/qbittorrent-tests.log`. Later playback changes passed 13 targeted tests, and the force-start option passed 17 targeted tests. These are separate historical runs, not a claim that a new full suite has run today.
- [x] Production replacement was reported working and completed jobs were observed. Validate client playback and current job status again before migration.

Queue state, installed software versions, settings, and running HandBrake work must be checked again before deployment actions. Check `data/restart-result.json` for loaded-source hashes. No plugin migration, Git push, or production Jellyfin upgrade was performed while reorganizing this document.

## References and next step

- Latest stable release (recheck before pinning): https://api.github.com/repos/jellyfin/jellyfin/releases/latest
- Release inspected on 9 November: https://github.com/jellyfin/jellyfin/releases/tag/v12.4
- Inspected target framework/package version: https://github.com/jellyfin/jellyfin/blob/v12.4/MediaBrowser.Controller/MediaBrowser.Controller.csproj
- Official plugin template: https://github.com/jellyfin/jellyfin-plugin-template
- Plugin installation: https://jellyfin.org/docs/general/server/plugins/

Resume prompt: **Read `TOMORROW_CHECKLIST.md` in `<deployment directory>`, complete the safety/dependency checks, then begin workstream 1's latest-stable compatibility spike. Follow the chosen Remuxify name and preserve the existing conversion and playback safeguards.**
