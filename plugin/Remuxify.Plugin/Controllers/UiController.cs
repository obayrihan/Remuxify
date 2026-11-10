using System.Reflection;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace Remuxify.Plugin.Controllers;

[ApiController]
[AllowAnonymous] // These are source-only assets; all data/mutations require native admin authorization.
public sealed class UiController : ControllerBase
{
    [HttpGet("Remuxify/Ui/{asset}")]
    public ActionResult Asset(string asset)
    {
        var contentType = asset switch
        {
            "index.html" => "text/html; charset=utf-8", "app.js" => "application/javascript; charset=utf-8",
            "style.css" => "text/css; charset=utf-8", "favicon.svg" => "image/svg+xml", _ => null
        };
        if (contentType is null) return NotFound();
        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("Remuxify.Plugin.Dashboard." + asset);
        if (stream is null) return NotFound();
        using var reader = new StreamReader(stream);
        var content = reader.ReadToEnd();
        if (asset == "index.html") content = content.Replace("/forge/static/", "", StringComparison.Ordinal).Replace("Media Forge", "Remuxify", StringComparison.Ordinal);
        Response.Headers.CacheControl = "no-store";
        Response.Headers["X-Content-Type-Options"] = "nosniff";
        Response.Headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'";
        return Content(content, contentType);
    }
}
