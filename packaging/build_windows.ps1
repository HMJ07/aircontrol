# Genera dist\AirControl\ (carpeta), dist\AirControl-Windows.zip y, si Inno Setup está instalado,
# dist\AirControl-Setup.exe. Ejecutar desde la raíz del proyecto en PowerShell.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

New-Item -ItemType Directory -Force models | Out-Null
if (-not (Test-Path models\hand_landmarker.task)) {
    Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task" -OutFile models\hand_landmarker.task
}
if (-not (Test-Path models\face_landmarker.task)) {
    Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task" -OutFile models\face_landmarker.task
}

python -m PyInstaller --noconfirm --clean packaging/aircontrol.spec
Compress-Archive -Path "dist\AirControl" -DestinationPath dist\AirControl-Windows.zip -Force
Write-Host "OK dist\AirControl-Windows.zip"

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) { $iscc = Get-Item "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" -ErrorAction SilentlyContinue }
if ($iscc) {
    & $iscc.Source packaging\installer.iss
    Write-Host "OK dist\AirControl-Setup.exe"
} else {
    Write-Host "Inno Setup no encontrado: solo se generó el .zip (instálalo con 'choco install innosetup' para el instalador)."
}
