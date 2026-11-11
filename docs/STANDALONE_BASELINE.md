# Standalone release baseline

The release target is the existing standalone Python dashboard, with no Jellyfin plugin dependency. `release/standalone` starts from the sanitized `main` baseline (`7ddfacc`), rather than merging the plugin branch.

## Selected changes

| Change | Decision |
| --- | --- |
| Explicit public hostname aliases and origin checks | Carry over the standalone-compatible fix and tests from `b0ed1ed`. No aliases are enabled in the example configuration. |
| Secret/temporary-file and private development-directory exclusions | Carry over the ignore rules so parked local development files cannot be accidentally staged. |
| Remuxify display branding | Apply to the app title, dashboard, standalone launcher, menu label, and startup messages. Preserve the current layout. |
| Native plugin controllers, callback, pairing, .NET builds and packages | Exclude from this release branch. |
| Native Jellyfin navigation hook and marker | Exclude. The optional standalone shortcut uses the existing launcher and administrator login flow. |
| Conversion engine, telemetry, qBittorrent, presets and playback/replacement policies | Preserve the standalone baseline unchanged. |
| Existing health service identifier, task/data names and route | Preserve for compatibility. Rename/migration work belongs to the installer stage. |
| Configuration copied from the paired live worker | Reject plugin mode clearly at startup. Migrate settings deliberately before a later live transition. |

## Scope of this step

Prepare a source candidate and run hardware-independent checks in a disposable directory. Do not start or stop production services, use production configuration or credentials, run a media conversion, or disable the installed connector.

Further acceptance still requires a separate isolated running installation and UI-driven conversion checks. Portable installation and the standalone Jellyfin connection setup remain open tasks. The existing installer still couples startup, menu, and Cloudflare operations; do not treat it as a completed portable installer.

The parked plugin branch retains its implementation, validation records and historical checklist. No plugin code or build artifact is required to run this candidate.

## Step-one verification

- 53 hardware-independent checks passed against a disposable source copy: standalone/API authentication and revoked privileges, configured origins, native-route absence, configuration rejection, inventory watching, file permissions, DPAPI/torrent coordination, playback/resource policies, restart guards, and Windows telemetry.
- Standalone JavaScript syntax checked successfully.
- The candidate's tracked files exclude plugin source, pairing helpers, .NET build files, local configuration, credentials, databases, media and build artifacts.
- The conversion engine, telemetry, torrent coordinator, FFmpeg dependencies and browser data/action logic match the standalone baseline. The entry-point difference is its displayed single-instance message.
- No production configuration, services, media, or credentials were used or changed. GPU conversions, a running installation, and browser acceptance belong to the next step.
