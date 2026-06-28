<#
.SYNOPSIS
  Start the AHCC competition demo locally at http://127.0.0.1:8001/app#/cockpit.

.DESCRIPTION
  This is a local fallback for rehearsals and on-site demos. It is not a judge-facing
  public deployment because 127.0.0.1 only points to the viewer's own machine.
  Use Docker/Render/Railway/Fly for the official competition URL.
#>
param(
    [int]$Port = 8001,
    [switch]$NoBuild,
    [switch]$RebuildFrontend,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$UiDir = Join-Path $ProjectRoot "ui-new"
$FrontendIndex = Join-Path $UiDir "dist\index.html"
$CockpitUrl = "http://127.0.0.1:$Port/app#/cockpit"
$HealthUrl = "http://127.0.0.1:$Port/health"

Set-Location $ProjectRoot
Write-Host "[AHCC] competition cockpit: $CockpitUrl"

if ($RebuildFrontend -or ((-not $NoBuild) -and (-not (Test-Path $FrontendIndex)))) {
    Write-Host "[AHCC] building React frontend..."
    Push-Location $UiDir
    try {
        if (-not (Test-Path "node_modules")) {
            npm ci
        }
        npm run build
    } finally {
        Pop-Location
    }
}

try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
    if ($health.StatusCode -eq 200) {
        Write-Host "[AHCC] backend already responding on port $Port"
        if (-not $NoOpen) { Start-Process $CockpitUrl }
        exit 0
    }
} catch {
    Write-Host "[AHCC] no backend on port $Port; starting one now..."
}

$Python = $null
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd) { $Python = $cmd.Source }
if (-not $Python) { throw "python interpreter not found on PATH" }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
if (-not $env:APP_ENV) { $env:APP_ENV = "demo" }

if (-not $NoOpen) {
    Start-Job -ScriptBlock {
        param($Url, $Health)
        for ($i = 0; $i -lt 30; $i++) {
            try {
                $response = Invoke-WebRequest -UseBasicParsing -Uri $Health -TimeoutSec 2
                if ($response.StatusCode -eq 200) {
                    Start-Process $Url
                    return
                }
            } catch {}
            Start-Sleep -Seconds 1
        }
    } -ArgumentList $CockpitUrl, $HealthUrl | Out-Null
}

$uvArgs = @("-m", "uvicorn", "ahcc.api.main:app", "--host", "127.0.0.1", "--port", "$Port")
Write-Host "[AHCC] running: python $($uvArgs -join ' ')"
Write-Host "[AHCC] press Ctrl+C to stop."
& $Python @uvArgs
