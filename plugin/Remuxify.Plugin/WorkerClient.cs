using System.Net.Http.Headers;
using System.Text.RegularExpressions;

namespace Remuxify.Plugin;

public sealed partial class WorkerClient(HttpClient client, IPluginState state)
{
    private static readonly Dictionary<string, string[]> Routes = new()
    {
        ["state"] = ["GET"], ["media"] = ["GET"], ["jobs"] = ["GET", "POST"],
        ["settings"] = ["GET", "PUT"], ["events"] = ["GET"], ["scan"] = ["POST"],
        ["encoder-test"] = ["POST"], ["qbittorrent"] = ["GET", "PUT"]
    };

    [GeneratedRegex(@"\Amedia/[1-9][0-9]*\z")]
    private static partial Regex MediaRoute();
    [GeneratedRegex(@"\Ajobs/[a-f0-9]{32}/(log|cancel|retry)\z")]
    private static partial Regex JobRoute();

    public static bool Allows(string path, string method)
    {
        if (Routes.TryGetValue(path, out var methods)) return methods.Contains(method);
        if (MediaRoute().IsMatch(path)) return method == "GET";
        var match = JobRoute().Match(path);
        return match.Success && method == (match.Groups[1].Value == "log" ? "GET" : "POST");
    }

    public async Task<HttpResponseMessage> SendAsync(string path, string query, HttpMethod method, byte[]? body, CancellationToken cancellationToken)
    {
        if (!Allows(path, method.Method)) throw new ArgumentException("Unsupported worker operation");
        if (!state.Enabled) throw new InvalidOperationException("Enable Remuxify in its plugin configuration first");
        var secret = state.ReadSecret() ?? throw new InvalidOperationException("Create the local pairing file first");
        using var request = new HttpRequestMessage(method, new Uri(state.WorkerUri, "forge/api/" + path + query));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", secret);
        request.Headers.Add("X-Forge-Request", "1");
        if (body is not null)
        {
            request.Content = new ByteArrayContent(body);
            request.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
        }
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        return await client.SendAsync(request, HttpCompletionOption.ResponseContentRead, timeout.Token).ConfigureAwait(false);
    }
}
