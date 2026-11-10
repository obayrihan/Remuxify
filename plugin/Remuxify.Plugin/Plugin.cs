using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;

namespace Remuxify.Plugin;

public sealed class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
{
    public static readonly Guid PluginId = new("79783b04-89a6-4da2-a395-1218189eb072");
    public static Plugin? Instance { get; private set; }

    public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
        : base(applicationPaths, xmlSerializer) => Instance = this;

    public override string Name => "Remuxify";
    public override Guid Id => PluginId;
    public override string Description => "Administrator media conversion dashboard with a private NVENC worker.";

    public IEnumerable<PluginPageInfo> GetPages() =>
    [
        new PluginPageInfo
        {
            Name = "Remuxify",
            DisplayName = "Remuxify",
            EmbeddedResourcePath = "Remuxify.Plugin.Web.configPage.html"
        }
    ];
}

public sealed class PluginConfiguration : BasePluginConfiguration
{
    public bool Enabled { get; set; }
    public string WorkerUrl { get; set; } = "http://127.0.0.1:5056/";
}
