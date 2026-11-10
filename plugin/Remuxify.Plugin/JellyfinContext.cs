using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Session;
using System.Text.Json.Serialization;

namespace Remuxify.Plugin;

public record LibraryRoot([property: JsonPropertyName("library")] string Library, [property: JsonPropertyName("path")] string Path);
public record BridgeContext([property: JsonPropertyName("protocolVersion")] int ProtocolVersion,
                           [property: JsonPropertyName("roots")] LibraryRoot[] Roots,
                           [property: JsonPropertyName("playbackPaths")] string[] PlaybackPaths);

public interface IJellyfinContext
{
    BridgeContext Read();
    Task RefreshAsync(CancellationToken cancellationToken);
}

public sealed class JellyfinContext(ILibraryManager libraries, ISessionManager sessions) : IJellyfinContext
{
    public BridgeContext Read()
    {
        var roots = libraries.GetVirtualFolders().SelectMany(folder =>
            (folder.Locations ?? []).Select(path => new LibraryRoot(folder.Name, path))).ToArray();
        // Preserve the existing paused-session policy until the user approves a change.
        var playback = sessions.Sessions.Where(session => session.NowPlayingItem is not null)
            .Select(session => session.FullNowPlayingItem?.Path ?? session.NowPlayingItem.Path).ToArray();
        if (playback.Any(string.IsNullOrWhiteSpace))
            throw new InvalidOperationException("A playback session has no identifiable source path");
        return new BridgeContext(1, roots, playback);
    }

    public Task RefreshAsync(CancellationToken cancellationToken) =>
        libraries.ValidateMediaLibrary(new Progress<double>(), cancellationToken);
}
