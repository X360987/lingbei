$ErrorActionPreference = 'Stop'
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $bundledPython) { & $bundledPython (Join-Path $PSScriptRoot 'app.py') }
else { python (Join-Path $PSScriptRoot 'app.py') }
