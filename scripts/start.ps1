$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
    & $pythonPath -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
Start-Process 'http://127.0.0.1:8766'
& $pythonPath -m intel
