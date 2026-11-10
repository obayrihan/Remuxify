using System.Net;
using System.Net.Http.Headers;
using System.Security.Claims;
using System.Text;
using System.Text.Encodings.Web;
using MediaBrowser.Common.Api;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Remuxify.Plugin.Controllers;
using Xunit;

namespace Remuxify.Plugin.Tests;

public sealed class PluginApiTests
{
    private sealed class State : IPluginState
    {
        public bool Enabled => true;
        public Uri WorkerUri => new("http://127.0.0.1:5057/");
        public string SecretFile => "private-pairing-file";
        public string? ReadSecret() => new('A', 64);
        public void EnsureSecret() { }
    }

    private sealed class Context : IJellyfinContext
    {
        public BridgeContext Read() => new(1, [new("Test library", "C:\\disposable-library")], []);
        public Task RefreshAsync(CancellationToken cancellationToken) => Task.CompletedTask;
    }

    private sealed class WorkerHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Assert.Equal("127.0.0.1", request.RequestUri!.Host);
            Assert.Equal("Bearer", request.Headers.Authorization?.Scheme);
            Assert.Equal(new string('A', 64), request.Headers.Authorization?.Parameter);
            Assert.False(request.Headers.Contains("X-Emby-Token"));
            if (request.RequestUri.AbsolutePath.EndsWith("/settings", StringComparison.Ordinal))
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.Unauthorized));
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{\"ok\":true}", Encoding.UTF8, "application/json") });
        }
    }

    private sealed class Authentication(IOptionsMonitor<AuthenticationSchemeOptions> options, ILoggerFactory logger, UrlEncoder encoder)
        : AuthenticationHandler<AuthenticationSchemeOptions>(options, logger, encoder)
    {
        protected override Task<AuthenticateResult> HandleAuthenticateAsync()
        {
            if (!Request.Headers.TryGetValue("X-Test-User", out var role)) return Task.FromResult(AuthenticateResult.NoResult());
            var claims = new[] { new Claim(ClaimTypes.Name, "test-user"), new Claim("admin", role.ToString()) };
            return Task.FromResult(AuthenticateResult.Success(new AuthenticationTicket(new ClaimsPrincipal(new ClaimsIdentity(claims, Scheme.Name)), Scheme.Name)));
        }
    }

    private static IHost Host() => new HostBuilder().ConfigureWebHost(web => web.UseTestServer().ConfigureServices(services =>
    {
        services.AddRouting();
        services.AddLogging();
        services.AddAuthentication("Test").AddScheme<AuthenticationSchemeOptions, Authentication>("Test", _ => { });
        services.AddAuthorization(options => options.AddPolicy(Policies.RequiresElevation, policy => policy.RequireAuthenticatedUser().RequireClaim("admin", "yes")));
        services.AddSingleton<IPluginState, State>();
        services.AddSingleton<IJellyfinContext, Context>();
        services.AddSingleton(provider => new WorkerClient(new HttpClient(new WorkerHandler()), provider.GetRequiredService<IPluginState>()));
        services.AddControllers().AddApplicationPart(typeof(AdminController).Assembly);
    }).Configure(app =>
    {
        app.Use(async (context, next) =>
        {
            context.Connection.RemoteIpAddress = context.Request.Headers.ContainsKey("X-Test-Remote") ? IPAddress.Parse("203.0.113.5") : IPAddress.Loopback;
            await next(context);
        });
        app.UseRouting(); app.UseAuthentication(); app.UseAuthorization(); app.UseEndpoints(endpoints => endpoints.MapControllers());
    })).Start();

    [Theory]
    [InlineData("Remuxify/Status")]
    [InlineData("Remuxify/Api/state")]
    public async Task AnonymousAndViewerCannotReadAdminData(string path)
    {
        using var host = Host(); using var client = host.GetTestClient();
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync(path)).StatusCode);
        client.DefaultRequestHeaders.Add("X-Test-User", "no");
        Assert.Equal(HttpStatusCode.Forbidden, (await client.GetAsync(path)).StatusCode);
    }

    [Theory]
    [InlineData("Remuxify/Pair")]
    [InlineData("Remuxify/Api/scan")]
    [InlineData("Remuxify/Api/jobs")]
    public async Task AnonymousAndViewerCannotMutate(string path)
    {
        using var host = Host(); using var client = host.GetTestClient();
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.PostAsync(path, new StringContent("{}"))).StatusCode);
        client.DefaultRequestHeaders.Add("X-Test-User", "no");
        Assert.Equal(HttpStatusCode.Forbidden, (await client.PostAsync(path, new StringContent("{}"))).StatusCode);
    }

    [Fact]
    public async Task AdminProxyUsesPrivateCredentialWithoutDisclosingIt()
    {
        using var host = Host(); using var client = host.GetTestClient();
        client.DefaultRequestHeaders.Add("X-Test-User", "yes");
        client.DefaultRequestHeaders.Add("X-Emby-Token", "test-native-user-token");
        var response = await client.GetAsync("Remuxify/Api/state");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.DoesNotContain(new string('A', 64), await response.Content.ReadAsStringAsync());
        Assert.False(response.Headers.Contains("Set-Cookie"));
        Assert.Equal(HttpStatusCode.OK, (await client.GetAsync("Remuxify/Api/media?library=Test&flagged=false&offset=0&limit=10&q=fixture")).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync("Remuxify/Api/login")).StatusCode);
        Assert.Equal(HttpStatusCode.BadRequest, (await client.GetAsync("Remuxify/Api/state?api_key=not-forwarded")).StatusCode);
    }

    [Fact]
    public async Task BridgeRequiresTheServiceSecretAndLoopbackNotAnAdminBrowserSession()
    {
        using var host = Host(); using var client = host.GetTestClient();
        client.DefaultRequestHeaders.Add("X-Test-User", "yes");
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("Remuxify/Bridge/Context")).StatusCode);
        client.DefaultRequestHeaders.Add("X-Remuxify-Key", new string('A', 64));
        var response = await client.GetAsync("Remuxify/Bridge/Context");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains("disposable-library", await response.Content.ReadAsStringAsync());
        client.DefaultRequestHeaders.Add("X-Test-Remote", "1");
        Assert.Equal(HttpStatusCode.Unauthorized, (await client.GetAsync("Remuxify/Bridge/Context")).StatusCode);
    }

    [Fact]
    public async Task PublicAssetsContainNoUserDataAndOnlyKnownResourcesAreServed()
    {
        using var host = Host(); using var client = host.GetTestClient();
        var response = await client.GetAsync("Remuxify/Ui/index.html");
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var html = await response.Content.ReadAsStringAsync();
        Assert.DoesNotContain("/forge/static/", html);
        Assert.DoesNotContain(new string('A', 64), html);
        Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync("Remuxify/Ui/config.json")).StatusCode);
    }

    [Fact]
    public async Task WorkerPairingFailureDoesNotInvalidateTheNativeUserSession()
    {
        using var host = Host(); using var client = host.GetTestClient();
        client.DefaultRequestHeaders.Add("X-Test-User", "yes");
        var response = await client.GetAsync("Remuxify/Api/settings");
        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.Contains("pairing", await response.Content.ReadAsStringAsync());
    }

    [Fact]
    public void BridgeContractIsStableRegardlessOfTheHostsJsonNamingPolicy()
    {
        var json = System.Text.Json.JsonSerializer.Serialize(new BridgeContext(1, [new("Test", "disposable")], []));
        Assert.Contains("\"protocolVersion\"", json);
        Assert.Contains("\"library\"", json);
        Assert.DoesNotContain("\"ProtocolVersion\"", json);
    }

    [Theory]
    [InlineData("http://example.com/")]
    [InlineData("http://127.0.0.1:5056/arbitrary")]
    [InlineData("http://user:password@127.0.0.1/")]
    [InlineData("http://127.0.0.1/?redirect=1")]
    public void NonLocalOrAmbiguousWorkerTargetsAreRejected(string value) => Assert.Throws<InvalidOperationException>(() => LocalEndpoint.Parse(value));

    [Theory]
    [InlineData("../config.json", "GET")]
    [InlineData("media/1\n", "GET")]
    [InlineData("jobs/not-a-job/log", "GET")]
    [InlineData("settings", "DELETE")]
    public void ProxyIsNotAnArbitraryRouteGateway(string path, string method) => Assert.False(WorkerClient.Allows(path, method));

    [Fact]
    public void PairingFileIsPrivateAtomicAndNeverRotatedImplicitly()
    {
        var directory = Path.Combine(Path.GetTempPath(), "remuxify-pairing-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        try
        {
            var path = Path.Combine(directory, "worker.secret");
            var file = new PairingFile(path);
            file.Ensure();
            var secret = file.Read();
            Assert.True(Secret.IsValid(secret!));
            file.Ensure();
            Assert.Equal(secret, file.Read());
            Assert.Single(Directory.GetFiles(directory));
            if (OperatingSystem.IsWindows())
            {
                var acl = System.IO.FileSystemAclExtensions.GetAccessControl(new FileInfo(path));
                Assert.True(acl.AreAccessRulesProtected);
                Assert.Equal(3, acl.GetAccessRules(true, false, typeof(System.Security.Principal.SecurityIdentifier)).Count);
            }
            else Assert.Equal(UnixFileMode.UserRead | UnixFileMode.UserWrite, File.GetUnixFileMode(path));
        }
        finally { Directory.Delete(directory, true); }
    }
}
