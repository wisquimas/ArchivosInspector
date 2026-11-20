; Instalador de Archivos Inspector — Creado por Wisquimas
; Requiere Inno Setup 6+ y el build de PyInstaller en dist\ArchivosInspector\

#define MyAppName "Archivos Inspector"
#define MyAppVersion "1.4.0"
#define MyAppPublisher "Wisquimas"
#define MyAppURL "https://github.com/wisquimas/ArchivosInspector"
#define MyAppExeName "ArchivosInspector.exe"
[Setup]
; GUID fijo del producto (no cambiar entre releases)
AppId={{8F3A2B1C-9D4E-4F6A-B7C8-1E2D3A4B5C6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE
OutputDir=..\release
OutputBaseFilename=ArchivosInspector-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} — inspector de disco local
VersionInfoCopyright=Copyright (c) 2026 Wisquimas
VersionInfoProductName={#MyAppName}
DisableProgramGroupPage=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\ArchivosInspector\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Inspector de disco 2D/3D — Creado por Wisquimas"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "Creado por Wisquimas"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard();
begin
  WizardForm.WelcomeLabel2.Caption :=
    'Este asistente instalará {#MyAppName} {#MyAppVersion} en su equipo.' + #13#10 + #13#10 +
    'No necesita tener Python instalado: el programa incluye todo lo necesario.' + #13#10 + #13#10 +
    'Creado por Wisquimas. Licencia MIT.';
end;
