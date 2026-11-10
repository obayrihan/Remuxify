using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Remuxify.Plugin.Controllers;

[ApiController]
[AllowAnonymous] // A private service credential is checked on every action below.
public sealed class BridgeController(IPluginState state, IJellyfinContext context, IHostApplicationLifetime lifetime, ILogger<BridgeController> logger) : ControllerBase
{
    private bool Authorized()
    {
        if (!state.Enabled || HttpContext.Connection.RemoteIpAddress is not { } address || !System.Net.IPAddress.IsLoopback(address)) return false;
        // Do not send the service credential through Jellyfin's user/JWT handler.
        var credential = Request.Headers["X-Remuxify-Key"].ToString();
        try { return Secret.Matches(state.ReadSecret(), credential); }
        catch (Exception exception) when (exception is IOException or InvalidOperationException or UnauthorizedAccessException) { return false; }
    }

    [HttpGet("Remuxify/Bridge/Context")]
    public ActionResult Context()
    {
        if (!Authorized()) return Unauthorized();
        try { return Ok(context.Read()); }
        catch (InvalidOperationException) { return StatusCode(503, new { detail = "Cannot confirm native Jellyfin library/playback context" }); }
    }

    [HttpPost("Remuxify/Bridge/Refresh")]
    public ActionResult Refresh()
    {
        if (!Authorized()) return Unauthorized();
        // As with the previous library-refresh request, conversion completion is
        // independent of the subsequent Jellyfin scan. Never block HTTP threads.
        _ = Task.Run(async () =>
        {
            try { await context.RefreshAsync(lifetime.ApplicationStopping).ConfigureAwait(false); }
            catch (Exception exception) { logger.LogWarning(exception, "Jellyfin library refresh was deferred"); }
        }, lifetime.ApplicationStopping);
        return Accepted(new { queued = true });
    }
}
