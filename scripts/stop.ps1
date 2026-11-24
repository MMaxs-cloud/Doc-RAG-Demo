$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    .\.venv\Scripts\python.exe scripts/local_services.py stop
    if ($LASTEXITCODE) { throw 'Failed to stop services' }
} finally { Pop-Location }
