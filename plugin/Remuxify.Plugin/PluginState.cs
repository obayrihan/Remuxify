using System.Net;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;

namespace Remuxify.Plugin;

public interface IPluginState
{
    bool Enabled { get; }
    Uri WorkerUri { get; }
    string SecretFile { get; }
    string? ReadSecret();
    void EnsureSecret();
}

public sealed class PluginState : IPluginState
{
    private readonly object _gate = new();
    private static Plugin Current => Plugin.Instance ?? throw new InvalidOperationException("Plugin is not loaded");
    public bool Enabled => Current.Configuration.Enabled;
    public Uri WorkerUri => LocalEndpoint.Parse(Current.Configuration.WorkerUrl);
    public string SecretFile => Path.Combine(Current.DataFolderPath, "worker-pairing.secret");

    public string? ReadSecret()
    {
        lock (_gate) return new PairingFile(SecretFile).Read();
    }

    public void EnsureSecret()
    {
        lock (_gate) new PairingFile(SecretFile).Ensure();
    }
}

public sealed class PairingFile(string path)
{
    public string? Read()
    {
        if (!File.Exists(path)) return null;
        var secret = File.ReadAllText(path).Trim();
        return Secret.IsValid(secret) ? secret : throw new InvalidOperationException("Pairing file is invalid");
    }

    public void Ensure()
    {
        if (Read() is not null) return; // Do not rotate an existing connection.
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var pending = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var file = new FileStream(pending, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                if (OperatingSystem.IsWindows())
                {
                    var acl = new FileSecurity();
                    acl.SetAccessRuleProtection(true, false);
                    var owner = WindowsIdentity.GetCurrent().User ?? throw new InvalidOperationException("Missing Windows identity");
                    foreach (var sid in new[] { owner, new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null) })
                        acl.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, AccessControlType.Allow));
                    new FileInfo(pending).SetAccessControl(acl);
                }
                else
                {
                    File.SetUnixFileMode(pending, UnixFileMode.UserRead | UnixFileMode.UserWrite);
                }
                var bytes = System.Text.Encoding.ASCII.GetBytes(Convert.ToHexString(RandomNumberGenerator.GetBytes(32)));
                file.Write(bytes);
                file.Flush(true);
            }
            File.Move(pending, path, false);
        }
        finally { if (File.Exists(pending)) File.Delete(pending); }
    }
}

public static class Secret
{
    public static bool IsValid(string value) => value.Length == 64 && value.All(Uri.IsHexDigit);

    public static bool Matches(string? expected, string? supplied) =>
        expected is not null && supplied is not null && IsValid(expected) && IsValid(supplied)
        && CryptographicOperations.FixedTimeEquals(System.Text.Encoding.ASCII.GetBytes(expected), System.Text.Encoding.ASCII.GetBytes(supplied));
}

public static class LocalEndpoint
{
    public static Uri Parse(string value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https") || uri.UserInfo.Length != 0
            || uri.Query.Length != 0 || uri.Fragment.Length != 0 || uri.AbsolutePath != "/"
            || !(uri.Host.Equals("localhost", StringComparison.OrdinalIgnoreCase)
                 || IPAddress.TryParse(uri.Host.Trim('[', ']'), out var ip) && IPAddress.IsLoopback(ip)))
            throw new InvalidOperationException("The worker must be a loopback HTTP(S) endpoint without a path, query, or credentials");
        return uri;
    }
}
