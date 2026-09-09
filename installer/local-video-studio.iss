#define MyAppName "Local Video Studio"
#define MyAppVersion "0.1.0"
#define MyAppExeName "LocalVideoStudio.exe"

[Setup]
AppId={{E4140B7F-9C98-4CB0-BA8C-349996849CEE}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Local Video Studio
DefaultGroupName={#MyAppName}
OutputBaseFilename=LocalVideoStudio-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\LocalVideoStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

