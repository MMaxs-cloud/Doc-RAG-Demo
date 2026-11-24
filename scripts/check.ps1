$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    git --version
    py -3.12 --version
    node --version
    npm.cmd --version
    foreach ($command in @('docker','qdrant','llama-server','soffice','nvidia-smi')) {
        $found = Get-Command $command -ErrorAction SilentlyContinue
        [pscustomobject]@{ Tool = $command; Location = if ($found) { $found.Source } else { 'Not in PATH' } }
    }
    if (Get-Command docker -ErrorAction SilentlyContinue) { docker compose version }
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { nvidia-smi }
    Get-PSDrive (Get-Location).Drive.Name | Select-Object Name,Free
} finally { Pop-Location }
