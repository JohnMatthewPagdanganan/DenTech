$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$clinicPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $clinicPython)) {
    Write-Host 'Install dependencies first using the steps in README.md.'
    exit 1
}
& $clinicPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'DenTech: http://127.0.0.1:8000. Press Ctrl+C to stop.'
& $clinicPython manage.py runserver 127.0.0.1:8000
