param([switch]$WithoutLocalModel, [ValidateRange(0,99)][int]$GpuLayers = 99)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $python = (Resolve-Path '.venv/Scripts/python.exe').Path
    $appArgs = @('scripts/local_services.py', 'start', '--gpu-layers', $GpuLayers)
    if ($WithoutLocalModel) { $appArgs += '--without-local-model' }
    & $python @appArgs
    if ($LASTEXITCODE) { throw 'A service failed to start. Inspect logs in data/run.' }
} finally { Pop-Location }
