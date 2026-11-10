# Remuxify — standalone release checklist

Updated: 11 November 2026.

## Release decision and current state

**Release the standalone Remuxify web app with no Jellyfin plugin dependency.** Keep the existing dashboard and conversion behavior. A Jellyfin menu link may be an optional shortcut to the app; native plugin routes, DLL installation, pairing, and the .NET runtime are not release requirements.

The live deployment was paired in plugin mode on 10 November. It has not been reverted by this planning update. Preserve its settings, database, recovery journals, credentials, conversion engine, and independent HandBrake work. Public access was restored and the protected private hostname remains available. Machine-specific configuration and backups stay outside Git.

The standalone baseline is on `main`; later work is on `feature/jellyfin-plugin`. Do not merge the whole plugin branch into the standalone release. The previous checklist is retained in [docs/PLUGIN_CHECKLIST_ARCHIVE.md](docs/PLUGIN_CHECKLIST_ARCHIVE.md).

## Immediate next steps — execution order

1. Prepare a standalone release branch from the standalone baseline, carrying over reviewed, applicable fixes and Remuxify branding. Remove any mandatory plugin/pairing dependency from its startup, API, and browser flows.
2. Verify this build in an isolated installation with Jellyfin admin login, API-based library discovery, telemetry, settings, queue/history, and optional qBittorrent coordination. Do not use production media for conversion tests.
3. Back up and safely return the existing worker to standalone mode while idle. Preserve the current data and credentials, disable the native-navigation marker, and replace the native plugin shortcut with the agreed optional standalone link. Verify standalone access before disabling/removing the installed connector. Do not upgrade or restart Jellyfin implicitly.
4. Fix the installer so another Windows/NVIDIA host can install and start Remuxify without Cloudflare or this host's paths, hostname, ports, or account details.
5. Complete disposable end-to-end safety and playback checks, documentation, naming/license decisions, and release packaging before publishing.

Discuss changes to authentication, library discovery, media replacement, presets, or deployment behavior with the user before implementation. No conversion-engine rewrite or UI redesign is part of this release decision.

## 1. Standalone installation, startup, and upgrades — L

- [ ] Establish the standalone release branch and review which post-baseline changes belong in it. Preserve the parked plugin work as history.
- [ ] Provide a clean installation that does not require a DLL, plugin secret, native callback, or .NET SDK/runtime.
- [ ] Separate app installation and startup from optional Jellyfin-menu and reverse-proxy/tunnel integration. Cloudflare must not be mandatory.
- [ ] Finish configurable dependency discovery and installation paths. Some scripts already accept Python/Cloudflare executable parameters; fixed listener ports, task labels, health endpoints, and optional-integration assumptions still need review.
- [ ] Verify startup, health reporting, and recovery across reboot, logoff, and RDP disconnection on the advertised Windows/NVIDIA setup.
- [ ] Preserve settings, queue/history, recovery journals, encrypted credentials, and job-owned files through upgrade and rollback. Do not rename the live data directory or task during active work.
- [ ] Provide tested upgrade, uninstall, and rollback instructions that refuse to interrupt encoding, validation, or replacement.
- [ ] State the supported OS/GPU scope. Cross-platform support is not an initial release claim.

## 2. Conversion safety and complete-flow acceptance — L

- [x] Existing implementation uses full decoding, playable-frame checks, source fingerprints, durable hashes/receipts, and recoverable replacement to protect originals.
- [x] Existing implementation retains verified output during file-lock or watched-source replacement waits.
- [ ] Run a conversion on a disposable media copy through the standalone UI; verify FPS/ETA, phases, output playback, all retained audio tracks/surround, and replacement only after successful validation.
- [ ] Recheck incomplete downloads, changed sources/outputs, invalid or truncated output, low storage, CPU/GPU/disk headroom, cancellation, process failure, reboot, and interrupted commit.
- [ ] Confirm that failed or ambiguous conversions retain the original and that completed outputs do not get automatically queued again.
- [ ] Test playback on the originally affected iPhone/browser clients. Structural validation alone does not establish visual quality or client compatibility.
- [ ] Verify overview, library discovery/removal/filtering, settings, rules, queue/history, logs, and telemetry together in a clean installation.
- [ ] Document current restart limitations honestly: an interruption during validation may still require another encode. Enhanced retained-encode recovery is deferred below.

## 3. Authentication, configuration, and privacy — M

- [ ] Agree the standalone Jellyfin connection setup. The legacy app discovers a background API key from Jellyfin's local database; review a portable, explicitly configured connection rather than treating the plugin bridge as a dependency. Consult the user before changing the authentication/discovery design.
- [ ] Verify Jellyfin administrator login, viewer/anonymous rejection, privilege revocation, session expiry/logout, cookies, request-origin protection, and access checks on every data/action endpoint in the release build.
- [ ] Verify that credentials and tokens never appear in browser responses, URLs, logs, examples, packaged files, or Git.
- [x] Runtime configuration, databases, media, logs, encrypted credentials, backups, pairing files, and generated artifacts are excluded from the existing source checkout. Recheck the actual release contents and history before publication.
- [ ] Test local credential permissions and the chosen service account. Explain that machine-DPAPI credentials must be re-entered on another host.
- [ ] Document optional HTTPS/reverse-proxy setup independently of this host's DNS, tunnel, and private access gate.

## 4. Playback, queue controls, and torrent protection — M

Preserve the following existing behaviors:

- [x] Playback does not cancel or restart an active conversion or validation.
- [x] The normal playback wait gates new work only; disabling it permits work during unrelated playback.
- [x] Force-transcode can override playback start waits while resource checks remain enabled.
- [x] Replacement always waits if the exact source is watched or playback status cannot be checked; fully validated output is retained.
- [x] qBittorrent coordination stops only completed torrents containing the exact source. A matching multi-file torrent stops as a whole; unrelated torrents remain running. Changed torrents are not automatically resumed, rechecked, or deleted.

Release checks:

- [ ] Test watched versus unrelated media, playback beginning/ending mid-job, monitoring failure, and each wait/force combination in standalone mode.
- [ ] Test completed/incomplete torrents, exact-path matching, stop confirmation, unavailable API, and lock release with disposable content.
- [ ] Make queue pause, active cancellation, playback waits, and replacement waits clear in the UI and documentation. Do not imply that queue pause preserves an active encoder's in-memory progress.

## 5. Branding, documentation, and distribution — S–M

- [x] Select the name Remuxify and preserve the current dashboard design.
- [ ] Check naming conflicts before publishing; availability has not been verified.
- [ ] Finish consistent app/page/logo/installer branding. Service/task/directory renames require a migration plan and are not necessary just to change the displayed name.
- [ ] Document clean setup, Jellyfin connection, NVDEC/NVENC requirements, presets, qBittorrent, startup, backups, updates, uninstall, and rollback.
- [ ] Document the current quality limits: video encoding is lossy, original audio is copied by default, resolution is preserved where supported, and the current 8-bit/20 Mbps defaults do not promise archival-quality 8K output. HDR/Dolby Vision support is outside this release.
- [ ] Select a project license with the user before an open-source release.
- [ ] Add hardware-independent CI; keep GPU integration checks on a suitable trusted test host using disposable media.
- [ ] Produce a reproducible standalone package, version, checksum, release notes, and supported-platform statement. Test installation from the package on a clean supported setup.
- [ ] Review the release diff and package for private data, then publish only after acceptance and user authorization. No plugin catalog is needed.

## Deferred enhancements — not blockers for releasing the current feature set

- [ ] Persist an encoded-awaiting-validation stage and validate source/output identity before reusing a retained encode across restart.
- [ ] Extend interruption recovery across that additional stage without weakening replacement safety.
- [ ] Optional manual pause/resume of active FFmpeg/NVDEC/NVENC work, with watchdog, ETA, cancellation, and shutdown handling.
- [ ] Optional 8K/source-bit-depth presets after explicit quality, hardware, and HDR scope decisions.
- [ ] Additional operating systems and GPU vendors after independent validation.
- [ ] Further native plugin development, ABI-specific packaging, and plugin-catalog publication are parked rather than part of this release.

## Resume prompt

**Release standalone Remuxify with no plugin dependency. Read this checklist first. Prepare and validate a standalone release branch, then plan an idle-only return of the live worker from plugin mode while preserving all settings, credentials, queue/history, and recovery data. Do not repeat the plugin cutover, automatically remove the installed connector before replacement access is verified, upgrade Jellyfin, retry media, or interrupt HandBrake. Keep the existing dashboard and conversion safeguards; consult the user before fundamental behavior changes.**
