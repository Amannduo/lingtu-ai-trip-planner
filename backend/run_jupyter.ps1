param(
  [string]$PythonPath = "D:\conda_envs\jupyter\python.exe",
  [string]$BindHost = "0.0.0.0",
  [int]$Port = 8000,
  [switch]$Reload,
  [switch]$NoReload
)

try {
  $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
  [Console]::InputEncoding = $utf8NoBom
  [Console]::OutputEncoding = $utf8NoBom
  $OutputEncoding = $utf8NoBom
} catch {
}

if (-not (Test-Path $PythonPath)) {
  Write-Error "Python interpreter not found: $PythonPath"
  exit 1
}

$portUsers = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($portUsers) {
  Write-Error "Port $Port is already in use. Stop the old backend process, or use -Port with a free port."
  $portUsers | Select-Object LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize
  exit 1
}

$args = @(
  "-m", "uvicorn",
  "app.api.main:app",
  "--host", $BindHost,
  "--port", $Port.ToString(),
  "--log-level", "info"
)

if ($Reload -and $NoReload) {
  Write-Error "Use either -Reload or -NoReload, not both."
  exit 1
}

if ($Reload) {
  $args += "--reload"
}

Write-Host "Starting backend with: $PythonPath"
Write-Host "URL: http://$BindHost`:$Port"
Write-Host "Reload: $Reload"

Push-Location $PSScriptRoot
try {
  & $PythonPath @args
} finally {
  Pop-Location
}
