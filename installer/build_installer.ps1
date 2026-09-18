# Compiles installer\setup.iss into installer\MotionDrive-Setup.exe.
# Installs Inno Setup via winget if the ISCC compiler isn't already present.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($cand in $candidates) {
        if (Test-Path $cand) {
            $iscc = $cand
            break
        }
    }
}
if (-not $iscc) {
    Write-Host "Inno Setup not found -- installing via winget..." -ForegroundColor Yellow
    winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($cand in $candidates) {
        if (Test-Path $cand) {
            $iscc = $cand
            break
        }
    }
}
if (-not $iscc) {
    throw "Inno Setup (ISCC.exe) still not found. Install it manually from https://jrsoftware.org/isinfo.php"
}

$distApp = Join-Path $root "build\dist\MotionDrive"
if (-not (Test-Path $distApp)) {
    throw "build\dist\MotionDrive not found -- run build\build.ps1 first."
}

$redist = Join-Path $root "installer\redist\ViGEmBusSetup_x64.exe"
if (-not (Test-Path $redist)) {
    Write-Host "ViGEmBus redistributable not found; the installer will still build" -ForegroundColor Yellow
    Write-Host "but won't bundle the virtual-controller driver. Run download_vigembus.ps1 first for a full build." -ForegroundColor Yellow
}

Write-Host "Compiling installer..." -ForegroundColor Cyan
& "$iscc" (Join-Path $root "installer\setup.iss")
Write-Host "Done: installer\MotionDrive-Setup.exe" -ForegroundColor Green
