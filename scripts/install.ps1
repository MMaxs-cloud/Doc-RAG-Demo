param([switch]$NoDocling)
$ErrorActionPreference = 'Stop'
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $buildTemp = Join-Path (Get-Location) 'data/build-temp'
    New-Item -ItemType Directory -Force -Path $buildTemp | Out-Null
    $env:TEMP = $buildTemp
    $env:TMP = $buildTemp
    py -3.12 -m venv .venv
    if ($LASTEXITCODE) { throw 'Cannot create .venv' }
    .\.venv\Scripts\python.exe -m pip install uv==0.11.8
    if ($LASTEXITCODE) { throw 'Cannot install uv' }
    $extras = @('--extra','inference','--extra','test')
    if (!$NoDocling) { $extras += @('--extra','pdf') }
    $env:UV_PROJECT_ENVIRONMENT = (Resolve-Path .venv).Path
    .\.venv\Scripts\uv.exe sync --project backend --locked --inexact @extras
    if ($LASTEXITCODE) { throw 'Cannot install backend' }
    Push-Location frontend
    try {
        npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE) { throw 'Cannot install frontend' }
    } finally { Pop-Location }
    if (!(Test-Path .env)) { Copy-Item .env.example .env }
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    Pop-Location
}
