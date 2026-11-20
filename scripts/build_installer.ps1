# Genera el Setup.exe con Inno Setup a partir del build de PyInstaller.
# Uso: powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Version = "1.3.0"
$ExeDir = Join-Path $Root "dist\ArchivosInspector"
$ExePath = Join-Path $ExeDir "ArchivosInspector.exe"
$Iss = Join-Path $Root "installer\archivos_inspector.iss"
$ReleaseDir = Join-Path $Root "release"

Write-Host "==> Archivos Inspector — instalador Windows" -ForegroundColor Cyan
Write-Host "    Creado por Wisquimas | v$Version"

if (-not (Test-Path $ExePath)) {
    Write-Host "No hay build de PyInstaller. Ejecutando build_exe.ps1..." -ForegroundColor Yellow
    & "$Root\scripts\build_exe.ps1"
}

function Find-ISCC {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$iscc = Find-ISCC
if (-not $iscc) {
    Write-Host "Inno Setup no encontrado. Intentando instalar con winget..." -ForegroundColor Yellow
    winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
    $iscc = Find-ISCC
}
if (-not $iscc) {
    throw "No se encontró ISCC.exe. Instalá Inno Setup 6+ y volvé a ejecutar este script."
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

Write-Host "Compilando con: $iscc"
& $iscc $Iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup falló con código $LASTEXITCODE"
}

$setup = Join-Path $ReleaseDir "ArchivosInspector-Setup-$Version.exe"
if (-not (Test-Path $setup)) {
    throw "No se generó el instalador: $setup"
}

# Zip portable opcional del onedir
$portableZip = Join-Path $ReleaseDir "ArchivosInspector-Portable-$Version.zip"
if (Test-Path $portableZip) { Remove-Item -Force $portableZip }
Compress-Archive -Path "$ExeDir\*" -DestinationPath $portableZip -CompressionLevel Optimal

Write-Host ""
Write-Host "OK instalador: $setup" -ForegroundColor Green
Write-Host "OK portable:    $portableZip" -ForegroundColor Green
Write-Host "Tamaño setup:   $([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB"
Write-Host "Tamaño zip:     $([math]::Round((Get-Item $portableZip).Length / 1MB, 1)) MB"
