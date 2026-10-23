# Portable Windows build: PyInstaller --onedir, two exes, one zip.
#
#   powershell -ExecutionPolicy Bypass -File tools\build_portable.ps1
#   powershell -ExecutionPolicy Bypass -File tools\build_portable.ps1 -SkipTests
#
# Idempotent: wipes dist\P3R-Save-Transfer and the previous zip, rebuilds both
# executables, copies README/LICENSE/version.txt and produces
#   dist\P3R-Save-Transfer-<version>-win64-portable.zip
# PyInstaller does not cross-compile: this must run on Windows.

[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($env:OS -ne "Windows_NT") {
    throw "Сборка возможна только на Windows: PyInstaller не кросс-компилирует."
}

# "python" on PATH (GitHub Actions), otherwise the py launcher.
$pythonExe = "python"
$pythonArgs = @()
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    $pythonExe = "py"
    $pythonArgs = @("-3")
}

function Invoke-Python {
    & $pythonExe @pythonArgs @args
    if ($LASTEXITCODE -ne 0) {
        throw "python $args завершился с кодом $LASTEXITCODE"
    }
}

$version = (& $pythonExe @pythonArgs tools\version.py).Trim()
if (-not $version) { throw "Не удалось определить версию (tools\version.py)" }
Write-Host "== P3R Save Transfer ${version}: portable build" -ForegroundColor Cyan

if (-not $SkipTests) {
    Write-Host "== pytest / ruff"
    Invoke-Python -m pytest -q
    Invoke-Python -m ruff check .
}

Write-Host "== PyInstaller"
Invoke-Python -m pip install --upgrade --quiet pyinstaller

New-Item -ItemType Directory -Force build | Out-Null
Invoke-Python tools\make_version_info.py $version build\version_info.txt

$distDir = Join-Path $root "dist\P3R-Save-Transfer"
$zipPath = Join-Path $root "dist\P3R-Save-Transfer-$version-win64-portable.zip"
Remove-Item -Recurse -Force $distDir -ErrorAction SilentlyContinue
Remove-Item -Force $zipPath -ErrorAction SilentlyContinue

Invoke-Python -m PyInstaller --noconfirm --clean p3r_portable.spec

Write-Host "== packaging"
Copy-Item LICENSE (Join-Path $distDir "LICENSE") -Force
Copy-Item tools\README-portable.txt (Join-Path $distDir "README.txt") -Force
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
$pythonVersion = (& $pythonExe @pythonArgs -c "import platform; print(platform.python_version())").Trim()
@(
    "P3R Save Transfer $version (win64, portable)",
    "built: $stamp",
    "python: $pythonVersion",
    "engine: p3r_transfer $version"
) | Set-Content -Encoding UTF8 (Join-Path $distDir "version.txt")

# Compress-Archive writes backslash entry names; make_zip.py keeps the archive conformant.
Invoke-Python tools\make_zip.py $distDir $zipPath

$zipMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
$exeMb = [math]::Round((Get-Item (Join-Path $distDir "P3R-Save-Transfer.exe")).Length / 1MB, 1)
Write-Host ""
Write-Host "Готово:" -ForegroundColor Green
Write-Host "  ${zipPath}  ($zipMb MB)"
Write-Host "  exe: ${exeMb} MB"
Write-Host "  распакуй и запусти P3R-Save-Transfer.exe (SmartScreen: Подробнее -> Выполнить)"
