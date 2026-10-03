; Inno Setup script for the Depot app. Mirrors
; pos_app\installer\pos_app_installer.iss - see that file's header for
; prerequisites and the compile command (swap the path to this file).
;
; TODO once branding exists: set MyAppPublisher, and point [Icons]/[Setup]
; at a real .ico.

#define MyAppName "Depot"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "TODO: your company name"
#define MyAppExeName "DepotApp.exe"

[Setup]
; Fixed, unique per app - different from pos_app's and admin_app's
; AppIds, and never changed once this app has shipped.
AppId={{19FCE1C2-FFA1-4A0B-9B4F-DA7C0E5E1865}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\installer_output
OutputBaseFilename=Depot-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\DepotApp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
