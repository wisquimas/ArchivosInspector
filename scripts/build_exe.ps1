# Empaqueta Archivos Inspector con PyInstaller (onedir, incluye Python + static/).
# Uso: powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "==> Archivos Inspector — build PyInstaller" -ForegroundColor Cyan
Write-Host "    Creado por Wisquimas"

$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    Write-Host "PyInstaller no está en el PATH. Instalando..." -ForegroundColor Yellow
    python -m pip install --upgrade pyinstaller
}

if (Test-Path "$Root\dist\ArchivosInspector") {
    Remove-Item -Recurse -Force "$Root\dist\ArchivosInspector"
}
if (Test-Path "$Root\build\pyinstaller") {
    Remove-Item -Recurse -Force "$Root\build\pyinstaller"
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath "$Root\dist" `
    --workpath "$Root\build\pyinstaller" `
    "$Root\archivos_inspector.spec"

$exe = Join-Path $Root "dist\ArchivosInspector\ArchivosInspector.exe"
if (-not (Test-Path $exe)) {
    throw "No se generó el ejecutable: $exe"
}

Write-Host ""
Write-Host "OK: $exe" -ForegroundColor Green
Write-Host "Carpeta portable: $(Join-Path $Root 'dist\ArchivosInspector')"
