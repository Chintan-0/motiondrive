# Downloads the official ViGEmBus redistributable (virtual gamepad driver)
# used by vgamepad's VirtualGamepadBackend. Run once before compiling the
# installer. Source: https://github.com/nefarius/ViGEmBus/releases
$ErrorActionPreference = "Stop"
$dest = Join-Path $PSScriptRoot "redist\ViGEmBusSetup_x64.exe"
New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null

$releaseApi = "https://api.github.com/repos/nefarius/ViGEmBus/releases/latest"
Write-Host "Looking up latest ViGEmBus release..."
$release = Invoke-RestMethod -Uri $releaseApi -Headers @{ "User-Agent" = "MotionDrive-Installer" }
$asset = $release.assets | Where-Object { $_.name -like "*x64*.exe" } | Select-Object -First 1
if (-not $asset) {
    throw "Could not find a ViGEmBus x64 setup asset in the latest release."
}
Write-Host "Downloading $($asset.name)..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $dest
Write-Host "Saved to $dest"
