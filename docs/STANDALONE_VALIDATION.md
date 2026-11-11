# Standalone isolated acceptance — step two

Verified 11 November 2026 on a Windows/NVIDIA host using the installed Jellyfin 10.11.8 binaries. A fresh Jellyfin data/config/cache/log tree, standalone app copy, synthetic accounts, generated media, and a mock torrent API were used. Both application servers and the mock API listened only on loopback. No Remuxify plugin was installed in the disposable server.

## Results

| Check | Result |
| --- | --- |
| Rendered standalone login; viewer rejected and administrator admitted | Passed in Chrome |
| Overview, library list/details, detection rules, settings, queue/history and logs | Passed in Chrome |
| Live CPU/disk/GPU/NVENC/NVDEC telemetry and discovered library storage | Displayed in Chrome |
| Settings save and persistence; detection-rule save | Passed through the UI |
| New folder/file discovered by the filesystem watcher | Passed |
| Jellyfin library root added and removed without restarting the app | Passed |
| Anonymous API rejection and origin/request-header enforcement | Passed over HTTP |
| Configured-port Jellyfin SSO bridge | Passed over HTTP |
| Browser back/forward, hard reload and completed history | Passed in Chrome |
| Logout; replay of the logged-out cookie | UI logout passed; HTTP replay rejected |
| Existing session after administrator privileges were revoked | HTTP access rejected after the privilege-check interval |
| Mock torrent connection saved without stopping any torrent | Passed through the UI |
| Queue paused while a generated source was selected and queued | Original unchanged; mock torrents unchanged |
| Conversion started by resuming the queue in the UI | Completed |
| Independent output probe, full decode and durable validation receipt | Passed |
| Only the exact matching mock torrent stopped; unrelated torrent unchanged | Passed; no resume, recheck or delete operation |
| Idle app restart preserves completed receipt and encrypted mock credentials | Passed |

The generated two-second input had 48 HEVC Main 10 video frames and one six-channel AAC audio track. The completed output had 48 HEVC 8-bit frames with the `hvc1` tag and all six audio channels. Its source-decoded, encoded and output-decoded frame counts all matched. The persisted receipt recorded successful structural/full-decode validation and a SHA-256 hash matching the replaced output. The UI showed the completed job and its logs.

The real torrent client, production media, live application configuration, Jellyfin configuration, routing and services were not changed. All disposable services and the test browser window were stopped/closed after the checks. Runtime fixtures, generated account credentials, databases, encrypted mock credentials, raw logs and media remain outside the source checkout.

## Fixes found during acceptance

- The local **Back to Jellyfin** link assumed the default Remuxify and Jellyfin ports. It now obtains the configured Jellyfin web URL for a local browser; remote same-origin proxy navigation keeps its previous behavior.
- The standalone SSO origin allowlist now recognizes the explicitly configured Jellyfin origin, including a non-default port. Administrator authentication still applies and unconfigured origins are rejected.
- Returning to the login screen now clears cached settings/rules load flags so the next session reloads its forms. The remaining login copy uses Remuxify branding.

Regression coverage: all 55 hardware-independent Python checks passed in the disposable app copy, the JavaScript login-state regression passed, and standalone JavaScript syntax passed. The conversion engine and presets were not rewritten.

## Limits and remaining work

This is acceptance of the isolated standalone flow, not certification of every release scenario. Real iPhone/browser client playback, visual quality, representative long-file FPS/ETA, fault injection across replacement boundaries, reboot/logoff, a packaged clean-host installation, reverse-proxy/HTTPS configurations, and other Jellyfin versions remain separate checks. The two-second conversion is too short to establish useful long-running ETA accuracy.

The next checklist step is a reviewed, backed-up, idle-only transition of the live worker from plugin mode. No live transition was performed here. General installer portability and the explicitly configured Jellyfin connection design remain open release tasks.
