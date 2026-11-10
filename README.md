# Remuxify

Standalone Jellyfin administrator dashboard for NVDEC/NVENC video conversion, compatibility indexing, and validated replacement of media files.

**Release direction, 11 November 2026:** ship standalone Remuxify with no plugin dependency. The plugin preview below is parked historical work. Follow [the standalone release checklist](TOMORROW_CHECKLIST.md) for the active plan; the live plugin-mode deployment has not yet been reverted.

The standalone baseline is preserved on `main`. The `feature/jellyfin-plugin` branch adds a **development-preview native plugin**, with separate Jellyfin 10.11.8 and 12.4 builds and the existing Python worker. Read [the migration guide](docs/PLUGIN_MIGRATION.md) before testing or installing it. The existing 10.11.8 deployment has been paired with the plugin after a user-approved, backed-up worker cutover. Its existing task and data directory remain in use; wider acceptance testing is still pending.

## Features

- Discover configured media folders from Jellyfin's library API, with library XML configuration fallback. Media directories are not hardcoded. Recursive filesystem notifications and periodic reconciliation update the inventory.
- Configurable metadata rules identify compatibility candidates rather than proving playback faults.
- HEVC NVENC encoding with optional NVDEC decoding, GPU bit-depth conversion, CPU fallback for unsupported profiles, and independent full software validation.
- Preserve original audio tracks and surround sound by default; configurable output container, depth, CQ, bitrate ceiling, and preset.
- Durable queue, progress/FPS/ETA, resource/storage preflight, CPU/GPU/NVENC/NVDEC/disk telemetry, cancellation, and recovery journals.
- qBittorrent exact-path coordination: stop matching completed torrents and retain their stopped state after conversion. Unrelated torrents remain running. Incomplete downloads wait.
- Playback does not interrupt active encoding or validation. Configurable start waits and force-start mode coexist with protection against replacing a watched source.
- Require successful stream/duration/frame checks, full video/audio decoding, and a durable SHA-256 receipt before replacement. Windows atomic replacement and recovery protect against interrupted promotion.

## Scope and limitations

The current deployment integration targets Windows and NVIDIA hardware. Windows PDH supplies disk activity, DPAPI encrypts local qBittorrent credentials, and Task Scheduler supplies boot startup. Cross-platform support and plugin compatibility are not yet verified.

Conversion re-encodes the video and is lossy; it is more than container-only remuxing. The baseline preset is HEVC 8-bit, MP4/hvc1, P5, CQ 23, and a 20 Mbps video ceiling. Structural validation cannot prove equivalent visual quality. HDR/Dolby Vision, interlaced inputs, multiple timed video streams, and unsupported stream layouts are blocked. The current default is not an archival-quality 8K guarantee.

Successful conversion intentionally replaces/removes the original. Begin with disposable media and keep your own backups. Unvalidated temporary outputs can be removed on interruption; interrupted validation currently may require encoding again. Fully validated pending replacements are retained during lock/playback waits. Manual in-memory pause/resume is not implemented.

## Local setup

Use Python 3.10 or newer and an FFmpeg build with the necessary NVIDIA encoders/decoders and CUDA filters. Install dependencies in your preferred virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

Edit the ignored `config.json` for your own Jellyfin URL/data directory, FFmpeg/FFprobe paths, and intended public URL. Values in the example are placeholders or conventional installation paths, not a description of any actual server. Jellyfin background requests currently use an existing server API key read from its local database in read-only mode; plugin migration will remove this dependency. Configure an API key in Jellyfin if none exists. Library roots come from Jellyfin and do not need to be listed in this file.

```powershell
.\.venv\Scripts\python.exe run.py
```

Open `http://localhost:5056/forge/` and sign in as a Jellyfin administrator. Automatic queueing defaults off. Do not expose the loopback worker port directly. Configure HTTPS/reverse-proxy access deliberately if remote access is needed.

For an additional public hostname, add its exact origin to `public_aliases` in the ignored configuration, for example `["https://admin.example.com"]`, and restart the worker while idle. This extends the allowed hosts and request origins; administrator login and request protection remain required. Configure DNS, proxy routing, and any additional access gate separately. A hostname is not an authentication secret.

The legacy `install.ps1` integrates a Jellyfin web-menu link, startup task, and optional Cloudflare path routing. It requires administrator access and explicit local `jellyfin_web`/`cloudflare_config` configuration. Review its staged changes before applying; it can restart the configured tunnel service. `-PythonExecutable` accepts the absolute interpreter path, and `-CloudflaredExecutable` accepts the tunnel program path. `start-local.ps1` and `restart-forge.ps1` also accept `-PythonExecutable`. These scripts are included for the standalone baseline; a portable plugin installer is still planned. No server or tunnel is required merely to inspect this source repository.

After installing the native plugin, `python integration.py --native-menu` replaces the old shortcut with **Remuxify**, pointing to Jellyfin's internal `#/configurationpage?name=Remuxify` route. Jellyfin's custom links ordinarily open a new tab; a small same-origin script changes only this shortcut to use the current tab. The installer backs up the affected web files and adds an idempotent script tag to `index.html`; it does not rebuild Jellyfin, restart services, or change access checks. Refresh the web client after installation. A local data marker lets `--ensure-menu` reapply the navigation after web-client updates. Plugin mode also selects native navigation automatically. The plugin/worker connection still needs separate pairing.

## Playback and queue behavior

- **Wait to start while Jellyfin is playing** delays new jobs. When disabled, unrelated playback does not block starting work.
- **Force transcode during playback** overrides playback start waits, including for the watched source. Resource checks remain active.
- A fresh playback check always protects replacement of an in-use source. The verified output waits until replacement is safe.
- **Pause queue** stops new starts without terminating active conversion. Before maintenance, wait for encoding/validation/commit to finish. `restart-forge.ps1 -AllowPausedQueue` preserves queued/waiting jobs only after the queue is paused and no active operation is found.

## Credentials and excluded files

Enter qBittorrent credentials through Settings. They are encrypted using Windows machine DPAPI in `data/qbittorrent-credentials.dpapi`, with an explicit DACL for the application-folder owner, SYSTEM, and Administrators. The browser never receives the saved password. A local administrator can still decrypt it on that host. Credentials must be entered again on another machine.

This source snapshot excludes local configuration, environment files, virtual environments, credentials, media, logs, runtime databases, backups, staged deployment files, and operational receipts. Keep those exclusions in place. Git ignore rules do not erase files already committed or secrets from history.

## Tests

For plugin builds and API-host tests:

```powershell
dotnet build plugin/Remuxify.Plugin/Remuxify.Plugin.csproj -c Release
dotnet test plugin/Remuxify.Plugin.Tests/Remuxify.Plugin.Tests.csproj -c Release
./build-plugin.ps1
```

Use the .NET SDK pinned by `global.json` and the .NET 9/ASP.NET Core 9 runtimes for the net9.0 test target. Preview ZIPs and checksums are generated locally under ignored `dist/`; building does not install or publish them.

Tests generate disposable media and use temporary databases/directories. Some tests require Windows, actual FFmpeg binaries, and NVIDIA hardware. Do not run hardware integration tests against production media.

```powershell
python -B -m unittest discover -s tests -p 'test_resources.py' -v
python -B -m unittest discover -s tests -p 'test_playback*.py' -v
python -B -m unittest discover -s tests -p 'test_restart_guard.py' -v
# Full suite requires the Windows/NVIDIA environment described above:
python -B -m unittest discover -s tests -v
```

For isolated test runs, copy the source and `config.example.json` into a temporary test checkout as `config.json`. Adjust only disposable test configuration for the available FFmpeg installation. Do not copy a production database or credentials into tests.

## Next milestone

See [TOMORROW_CHECKLIST.md](TOMORROW_CHECKLIST.md) for the workstreams ordered by estimated effort: native Jellyfin plugin, durable processing, portable installation, release verification, playback/torrent policies, optional quality presets, branding, and publication.

Project licensing remains to be selected before an open-source release. This checkpoint does not add an open-source license or claim official Jellyfin affiliation.
