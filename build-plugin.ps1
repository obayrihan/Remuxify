param([string]$DotnetExecutable = 'dotnet')
$ErrorActionPreference = 'Stop'
$remuxifyRoot = $PSScriptRoot
$remuxifyDotnet = (Get-Command -Name $DotnetExecutable -ErrorAction Stop).Source
& $remuxifyDotnet build (Join-Path $remuxifyRoot 'plugin\Remuxify.Plugin\Remuxify.Plugin.csproj') -c Release --nologo
if ($LASTEXITCODE -ne 0) { throw 'Plugin build failed; no package was created.' }
$remuxifyDist = Join-Path $remuxifyRoot 'dist'
New-Item -ItemType Directory -Path $remuxifyDist -Force | Out-Null
$remuxifyTargets = @(@{ Framework = 'net9.0'; Abi = '10.11.8.0'; Server = '10.11.8' }, @{ Framework = 'net10.0'; Abi = '12.4.0.0'; Server = '12.4' })
foreach ($remuxifyTarget in $remuxifyTargets) {
    $remuxifyStage = Join-Path $remuxifyDist ('package-' + $remuxifyTarget.Server)
    New-Item -ItemType Directory -Path $remuxifyStage -Force | Out-Null
    $remuxifyDll = Join-Path $remuxifyRoot ('plugin\Remuxify.Plugin\bin\Release\' + $remuxifyTarget.Framework + '\Remuxify.Plugin.dll')
    Copy-Item -LiteralPath $remuxifyDll -Destination (Join-Path $remuxifyStage 'Remuxify.Plugin.dll') -Force
    @{
        name = 'Remuxify'; guid = '79783b04-89a6-4da2-a395-1218189eb072'; version = '0.2.0.0'
        description = 'Development plugin with a separate local Python conversion worker.'
        owner = 'Remuxify contributors'; targetAbi = $remuxifyTarget.Abi; status = 'Active'; autoUpdate = $false
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $remuxifyStage 'meta.json') -Encoding utf8
    $remuxifyZip = Join-Path $remuxifyDist ('remuxify-0.2.0-jellyfin-' + $remuxifyTarget.Server + '.zip')
    Compress-Archive -LiteralPath @((Join-Path $remuxifyStage 'Remuxify.Plugin.dll'), (Join-Path $remuxifyStage 'meta.json')) -DestinationPath $remuxifyZip -Force
    $remuxifyHash = (Get-FileHash -LiteralPath $remuxifyZip -Algorithm SHA256).Hash.ToLowerInvariant()
    ($remuxifyHash + '  ' + [IO.Path]::GetFileName($remuxifyZip)) | Set-Content -LiteralPath ($remuxifyZip + '.sha256') -Encoding ascii
    Write-Output ([IO.Path]::GetFileName($remuxifyZip) + '  SHA256 ' + $remuxifyHash)
}
