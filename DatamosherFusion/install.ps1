# Datamosh for Fusion - Windows installer
# Install:   install.bat
# Uninstall: install.bat -Uninstall
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"

$fusion  = Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion"
$engine  = Join-Path $fusion "DatamoshFusion"
$scripts = Join-Path $fusion "Scripts\Comp"
$macros  = Join-Path $fusion "Macros"
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Uninstall) {
    Remove-Item -Recurse -Force $engine -ErrorAction SilentlyContinue
    Remove-Item -Force (Join-Path $scripts "Datamosher.lua") -ErrorAction SilentlyContinue
    Remove-Item -Force (Join-Path $macros "Datamosh.setting") -ErrorAction SilentlyContinue
    Write-Host "Datamosh removed." -ForegroundColor Green
    exit 0
}

function Test-Python($cmd, $argList) {
    try {
        $out = & $cmd @argList -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path ($out | Select-Object -First 1))) { return ($out | Select-Object -First 1) }
    } catch {}
    return $null
}

$python = Test-Python "py" @("-3")
if (-not $python) { $python = Test-Python "python" @() }
if (-not $python) { $python = Test-Python "python3" @() }
if (-not $python) {
    Write-Host "Python 3 not found." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ (check 'Add python.exe to PATH') and run this installer again."
    exit 1
}
Write-Host "Python: $python"

$ffmpeg = $null
$cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($cmd) { $ffmpeg = $cmd.Source }
if (-not $ffmpeg) {
    Write-Host "ffmpeg not found." -ForegroundColor Yellow
    $ans = Read-Host "Install it now with 'winget install Gyan.FFmpeg'? (y/N)"
    if ($ans -match '^[yY]') {
        winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
        if ($cmd) { $ffmpeg = $cmd.Source }
    }
}
if (-not $ffmpeg) {
    Write-Host "Continuing without ffmpeg. Install it later (winget install Gyan.FFmpeg) and run this installer again." -ForegroundColor Yellow
} else {
    Write-Host "ffmpeg: $ffmpeg"
}

New-Item -ItemType Directory -Force -Path $engine, $scripts, $macros | Out-Null
Copy-Item (Join-Path $here "datamosh_core.py") $engine -Force
Copy-Item (Join-Path $here "DatamoshRun.lua")  $engine -Force
Copy-Item (Join-Path $here "Datamosher.lua")   $scripts -Force
Copy-Item (Join-Path $here "Datamosh.setting") $macros -Force
Set-Content -Path (Join-Path $engine "config.txt") -Value @($python, $ffmpeg) -Encoding ASCII

Write-Host ""
Write-Host "Installed." -ForegroundColor Green
Write-Host "Restart DaVinci Resolve. Fusion page: Effects > Macros > Datamosh (or Workspace > Scripts > Datamosher)."
