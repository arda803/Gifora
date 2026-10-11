$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python 3.10+ was not found on PATH.' }
& $python.Source -c 'import PyInstaller, PIL, tkinterdnd2' *> $null
if ($LASTEXITCODE -ne 0) { throw 'Build requirements are missing. Run: python -m pip install -r requirements-build.txt' }
$dist = Join-Path $root 'dist'
$build = Join-Path $root 'build\pyinstaller'
New-Item -ItemType Directory -Force -Path $dist, $build | Out-Null
& $python.Source -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name Gifora --icon (Join-Path $root 'src\gifora.ico') `
    --add-data ((Join-Path $root 'src\gifora.ico') + ';.') `
    --collect-all tkinterdnd2 --distpath $dist --workpath $build `
    --specpath $build (Join-Path $root 'src\gifora.py')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
$stage = Join-Path $build 'release'
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item (Join-Path $dist 'Gifora.exe') (Join-Path $stage 'Gifora.exe') -Force
Copy-Item (Join-Path $root 'README.md') (Join-Path $stage 'README.md') -Force
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath (Join-Path $dist 'Gifora-Windows-v1.4.0.zip') -Force
Write-Host "Build complete: $dist"
