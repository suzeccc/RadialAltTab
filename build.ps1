$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = (Get-Command python).Source
$pythonDir = Split-Path -Parent $python
# Use Windows' ICU DLL; unrelated tools on PATH can supply an incompatible copy.
$env:PATH = "$pythonDir;$pythonDir\Scripts;$env:WINDIR\System32;$env:WINDIR"
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name RadialAltTab --icon radial_tab.ico radial_tab.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
