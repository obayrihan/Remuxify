using System.Text;
using MediaBrowser.Common.Api;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace Remuxify.Plugin.Controllers;

[ApiController]
[Authorize(Policy = Policies.RequiresElevation)]
public sealed class AdminController(IPluginState state, WorkerClient worker) : ControllerBase
{
    [HttpGet("Remuxify/Status")]
    public ActionResult Status()
    {
        try
        {
            return Ok(new { protocolVersion = 1, enabled = state.Enabled, paired = state.ReadSecret() is not null, workerUrl = state.WorkerUri.AbsoluteUri });
        }
        catch (InvalidOperationException exception) { return StatusCode(503, new { detail = exception.Message }); }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return StatusCode(503, new { detail = "Cannot read the private pairing file. Check the service account's file permissions." });
        }
    }

    [HttpPost("Remuxify/Pair")]
    public ActionResult Pair()
    {
        try
        {
            state.EnsureSecret();
            // The secret itself is never serialized into plugin configuration or an HTTP response.
            return Ok(new { protocolVersion = 1, secretFile = state.SecretFile });
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or InvalidOperationException)
        {
            return StatusCode(503, new { detail = "Could not prepare the private local pairing file. Check Jellyfin's data-folder permissions." });
        }
    }

    [AcceptVerbs("GET", "POST", "PUT")]
    [Route("Remuxify/Api/{**path}")]
    [RequestSizeLimit(150_000)]
    public async Task<ActionResult> Proxy(string path, CancellationToken cancellationToken)
    {
        if (!WorkerClient.Allows(path, Request.Method)) return NotFound();
        if (Request.Query.Any(pair => path != "media" || pair.Key is not ("q" or "flagged" or "library" or "offset" or "limit")))
            return BadRequest(new { detail = "Unsupported query parameter" });
        try
        {
            byte[]? body = null;
            if (Request.Method is "POST" or "PUT")
            {
                using var output = new MemoryStream();
                var buffer = new byte[8192];
                int read;
                while ((read = await Request.Body.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) > 0)
                {
                    if (output.Length + read > 150_000) return StatusCode(413);
                    output.Write(buffer, 0, read);
                }
                body = output.ToArray();
            }
            using var response = await worker.SendAsync(path, Request.QueryString.Value ?? "", new HttpMethod(Request.Method), body, cancellationToken).ConfigureAwait(false);
            if (response.StatusCode is System.Net.HttpStatusCode.Unauthorized or System.Net.HttpStatusCode.Forbidden)
                return StatusCode(503, new { detail = "The worker rejected its private pairing credential. Check plugin mode and pairing; your Jellyfin login is unchanged." });
            var content = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
            return new ContentResult { StatusCode = (int)response.StatusCode, ContentType = "application/json; charset=utf-8", Content = content };
        }
        catch (InvalidOperationException exception) { return StatusCode(503, new { detail = exception.Message }); }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return StatusCode(503, new { detail = "Cannot read the private pairing file. Check the service account's file permissions." });
        }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException)
        {
            return StatusCode(503, new { detail = "The private Remuxify worker is unavailable or timed out. Check its pairing and service status." });
        }
    }
}
