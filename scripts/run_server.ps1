<#
.SYNOPSIS
  AHCC backend one-click launcher. Kills any leftover uvicorn processes first,
  then starts EXACTLY ONE instance.

.DESCRIPTION
  Root cause this prevents: when multiple uvicorn processes are bound to port 8000,
  whichever bound first serves requests and every other --reload is a no-op -- so
  "I edited the code but nothing changed" and the checker keeps running stale code.
  This script always clears the field before starting, so no stale process survives.

  NOTE: ASCII-only on purpose. Windows PowerShell 5.1 reads .ps1 as the system ANSI
  codepage (GBK on zh-CN Windows), which corrupts non-ASCII string literals and breaks
  parsing. Keep this file ASCII.

.PARAMETER Port
  Listen port, default 8000.

.PARAMETER NoReload
  Disable --reload (more stable for demo; editing files will not trigger a reload).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_server.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_server.ps1 -Port 8000 -NoReload
#>
param(
    [int]$Port = 8000,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

# Decode the Python subprocess's UTF-8 output correctly so Chinese log lines are not mangled.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

# Project root = parent dir of this script
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot
Write-Host "[AHCC] project root: $ProjectRoot"

# 1) Kill all leftover uvicorn / ahcc processes (including --reload multiprocessing children)
Write-Host "[AHCC] cleaning leftover uvicorn processes..."
$killed = @()
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object {
        $_.CommandLine -and (
            $_.CommandLine -match 'uvicorn' -or
            $_.CommandLine -match 'ahcc\.api\.main' -or
            $_.CommandLine -match 'spawn_main'
        )
    } |
    ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $killed += $_.ProcessId } catch {}
    }
if ($killed.Count -gt 0) { Write-Host "[AHCC] killed PIDs: $($killed -join ', ')" } else { Write-Host "[AHCC] no leftover process" }

# 2) Confirm the port is released (wait up to 5s)
for ($i = 0; $i -lt 10; $i++) {
    if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $owners = ($busy.OwningProcess | Sort-Object -Unique) -join ','
    Write-Warning "[AHCC] port $Port still held by PID $owners -- please check manually."
}

# 3) Pick Python interpreter: prefer the known install (deps live there), fall back to PATH
$Python = $null
$candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"
if (Test-Path $candidate) {
    $Python = $candidate
} else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source }
}
if (-not $Python) { throw "python interpreter not found; set an absolute path in step 3" }
Write-Host "[AHCC] interpreter: $Python"

# 4) Start a single instance, tee console + log file (Ctrl+C to stop)
$logDir = Join-Path $ProjectRoot "storage\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("server_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$env:PYTHONIOENCODING = "utf-8"

$uvArgs = @("-m", "uvicorn", "ahcc.api.main:app", "--host", "127.0.0.1", "--port", "$Port")
if (-not $NoReload) { $uvArgs += @("--reload", "--reload-dir", "ahcc", "--reload-dir", "ui") }

Write-Host "[AHCC] starting backend port=$Port reload=$(-not $NoReload)"
Write-Host "[AHCC] log: $logFile"
Write-Host "[AHCC] open http://127.0.0.1:$Port/ in browser; Ctrl+C to stop."

# uvicorn logs to stderr. Under ErrorActionPreference=Stop, PowerShell would turn that
# stderr into a terminating NativeCommandError and kill the server. Relax it here.
$ErrorActionPreference = "Continue"
# Stream to console AND a UTF-8 log file. (Tee-Object in PS 5.1 writes UTF-16, which
# breaks grep/tail on the log; ForEach + Add-Content -Encoding UTF8 keeps it readable.)
& $Python @uvArgs 2>&1 | ForEach-Object {
    $line = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
    Write-Host $line
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}
