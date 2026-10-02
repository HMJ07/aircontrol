; Inno Setup: instalador de Windows (doble clic -> Siguiente -> Instalar), sin permisos de administrador.
[Setup]
AppName=AirControl
AppVersion=0.1.2
DefaultDirName={autopf}\AirControl
DefaultGroupName=AirControl
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=AirControl-Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayName=AirControl

[Files]
Source: "..\dist\AirControl\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AirControl"; Filename: "{app}\AirControl.exe"
Name: "{autodesktop}\AirControl"; Filename: "{app}\AirControl.exe"

[Run]
Filename: "{app}\AirControl.exe"; Description: "Iniciar AirControl"; Flags: nowait postinstall skipifsilent
