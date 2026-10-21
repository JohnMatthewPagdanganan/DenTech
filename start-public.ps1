$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$clinicPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$tunnelPath = Join-Path $PSScriptRoot '.public-demo\cloudflared.exe'
if (-not (Test-Path -LiteralPath $clinicPython)) {
    throw 'Install the project requirements in .venv first. See README.md.'
}
if (-not (Test-Path -LiteralPath $tunnelPath)) {
    New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot '.public-demo') -Force | Out-Null
    Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile $tunnelPath
}
& $clinicPython (Join-Path $PSScriptRoot 'public_demo.py') start --cloudflared $tunnelPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'To stop: .\.venv\Scripts\python.exe public_demo.py stop'
