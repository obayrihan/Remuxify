# Native plugin migration — development preview

## Agreed scope

The first port uses a **C# Jellyfin plugin plus the existing Python worker**, with separate builds for **Jellyfin 10.11.8** and **12.4**. These packaging/compatibility choices were approved by the user. Production Jellyfin does not need to be upgraded to develop or test the plugin.

Keep encoding settings, audio preservation, metadata rules, resource checks, replacement safety, qBittorrent behavior, and playback policies unchanged. Ask before introducing fundamental behavior or deployment changes. Optional manual pause/resume, recovery-stage redesign, HDR support, new quality presets, and other platforms are outside this first port.

This is a development preview, not a production rollout or catalog release. A DLL compiling successfully is not proof that every client/platform combination is supported.

## Components and authentication

1. Jellyfin registers Remuxify as a plugin and serves its embedded administrator page.
2. The page uses Jellyfin's own `ApiClient` and current native session. It hosts the existing dashboard inside the native plugin page; its data and actions go through the plugin's administrator-only API.
3. The plugin forwards only an explicit allowlist of worker operations. It never forwards the user's Jellyfin token, cookies, arbitrary headers, login endpoints, or arbitrary filesystem routes to the worker.
4. The plugin and worker share a generated local pairing file. The browser receives its path for one-time setup, never its contents. It is outside plugin XML configuration and excluded from Git. Windows permissions allow only the file owner, SYSTEM, and Administrators; Unix mode is owner read/write.
5. Plugin → worker requests use a private bearer credential. Worker → plugin context/refresh requests use `X-Remuxify-Key` so the service credential is never confused with a Jellyfin user token. Both connections require loopback endpoints, disable redirects/proxies, and authenticate each request.
6. The plugin obtains library roots and playback paths directly from Jellyfin services. Plugin-mode workers never read Jellyfin's database for API credentials and do not fall back to XML/database access if the bridge fails.

Jellyfin 12 disables older authentication headers; using the native `ApiClient` avoids hardcoding a legacy header into the dashboard. The service bridge is separately authenticated and does not accept a browser admin session as a substitute for its service credential.

## Versioned protocol

Protocol version **1** has these private callback operations:

| Request | Response/behavior |
| --- | --- |
| `GET Remuxify/Bridge/Context` | `protocolVersion`, `roots: [{library, path}]`, `playbackPaths: [path]` |
| `POST Remuxify/Bridge/Refresh` | Queue a native Jellyfin library refresh; conversion completion does not wait for the scan. |

Context JSON field names are explicitly fixed, independent of Jellyfin's serializer naming policy. Missing paths, unknown protocol versions, invalid secrets, redirects, and unavailable services fail closed. The existing file watches, root polling, and settling logic remain in the worker.

`Remuxify/Api/...`, status, and pairing setup require Jellyfin's `RequiresElevation` administrator policy. Static UI assets are public source-only resources; no library, account, queue, settings, credential, or log data is embedded in them. The asset controller serves only four known resource names.

## Build and test

Use the .NET 10 SDK pinned by `global.json`. Running the net9.0 test build also requires the .NET 9 and ASP.NET Core 9 shared runtimes. Build dependencies are obtained from NuGet, not from a production Jellyfin installation.

```powershell
dotnet build plugin/Remuxify.Plugin/Remuxify.Plugin.csproj -c Release
dotnet test plugin/Remuxify.Plugin.Tests/Remuxify.Plugin.Tests.csproj -c Release
./build-plugin.ps1
```

Each ZIP contains the plugin DLL and metadata for its target ABI. Outputs and checksums are under ignored `dist/`. These are local preview artifacts; the script does not install anything, restart any service, publish a GitHub release, or register a plugin repository.

| Target | Framework | Jellyfin API packages |
| --- | --- | --- |
| 10.11.8 | net9.0 | 10.11.8 |
| 12.4 | net10.0 | 12.4.0 |

## One-time pairing in a disposable setup

1. Use a disposable Jellyfin instance with separate data, config, cache, logs, plugins, and loopback ports. Disable network auto-discovery and UPnP; never point it at production databases or real media for initial integration checks.
2. Install the ZIP matching that server's ABI and restart only the disposable server. Open Dashboard → Plugins → Remuxify as an administrator.
3. Save the local worker URL and enable the plugin connection. Prepare the local pairing file.
4. In a separate worker test directory, create local `config.json` from the example, choose an unused loopback worker port, and stop the worker before pairing. Set its data/configuration paths deliberately; do not copy a production database or credentials into a test.
5. Run the local pairing command using the path shown by the plugin:

```powershell
python pair_worker.py --secret-file '<local plugin pairing file>' --jellyfin-url 'http://127.0.0.1:<test Jellyfin port>'
```

The command verifies the native bridge before atomically updating the local configuration to plugin mode. It takes the worker's instance lock and refuses to edit a running worker. It stores only the pairing file path, not a copied secret, and preserves existing preset/queue settings.

6. Start the worker and open the dashboard from the plugin page. Existing standalone login/SSO endpoints are disabled in plugin mode; use the native Jellyfin session. Standalone mode remains the default for unchanged installations.

For the main Jellyfin menu, configure the local `jellyfin_web` path and run `python integration.py --native-menu` with permissions to edit that web directory. The Remuxify shortcut opens the native plugin page in the current tab. A scoped script in the web shell overrides the usual new-tab behavior only for this exact internal route. No compiled web bundle is changed. Web files are backed up; the existing boot integration can reapply the hook after an update. This is a separately installed navigation hook rather than an automatic effect of copying the plugin DLL. Pairing, administrator authorization, and the separate worker remain unchanged.

The worker account must be able to read the private pairing file. For the current Windows setup, SYSTEM can read a file created by the Jellyfin account. Different service-account arrangements require an explicit permissions/deployment decision rather than making the file generally readable.

## Validation and remaining acceptance

Automated coverage includes native administrator-policy enforcement, private service authentication in both directions, restricted proxy routes, rejected external endpoints/redirects, explicit protocol JSON names, private/idempotent pairing storage, and fallback rejection. Tests use generated secrets and disposable state.

Native HTTP integration checks use isolated Jellyfin servers and disposable worker state. They verify plugin discovery, native authorization, pairing, dashboard resources, API reads/settings writes, dynamic library context, viewer rejection, and immediate rejection after administrator privileges are revoked. Each target also completed a real 2-second synthetic NVDEC/NVENC conversion initiated through the plugin API: all 48 video frames passed independent full decoding, six audio channels were preserved, and replacement occurred only after validation. No production media was processed. See [the validation summary](PLUGIN_VALIDATION.md).

These HTTP checks do not substitute for visual browser QA or a conversion initiated using the rendered native UI.

Before production installation:

- Complete visual checks in both web clients, including session expiry/revocation, base-URL handling, navigation, and the embedded dashboard.
- Run a disposable-media conversion through each complete plugin/worker deployment and confirm playback, replacement, recovery, and qBittorrent behavior.
- Review update/uninstall/rollback and migration of existing journals/credentials. Do not rename or abandon live data directories as part of installing a preview.
- Back up production and obtain approval for its restart/cutover. No production upgrade, tunnel change, hotlink removal, credential migration, or service restart is implied by this development work.
- Select a project license and prepare a verified plugin repository manifest before a public release.

## Primary API references

- Jellyfin plugin template: https://github.com/jellyfin/jellyfin-plugin-template
- Latest stable release: https://api.github.com/repos/jellyfin/jellyfin/releases/latest
- 12.4 library interfaces: https://github.com/jellyfin/jellyfin/blob/v12.4/MediaBrowser.Controller/Library/ILibraryManager.cs
- 12.4 session interfaces: https://github.com/jellyfin/jellyfin/blob/v12.4/MediaBrowser.Controller/Session/ISessionManager.cs
- 12.0 upgrade considerations: https://jellyfin.org/posts/jellyfin-release-12.0/
