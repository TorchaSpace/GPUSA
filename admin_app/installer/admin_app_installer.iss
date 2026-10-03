; Inno Setup script for the Admin Dashboard. Mirrors
; pos_app\installer\pos_app_installer.iss - see that file's header for
; prerequisites and the compile command (swap the path to this file).
;
; TODO once branding exists: set MyAppPublisher, and point [Icons]/[Setup]
; at a real .ico.

#define MyAppName "Admin Dashboard"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "TODO: your company name"
#define MyAppExeName "AdminDashboard.exe"

[Setup]
; Fixed, unique per app - different from pos_app's AppId, and never
; changed once this app has shipped (see pos_app's .iss for why).
AppId={{1F749E28-A10B-4C22-9581-B778E0AD4019}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\installer_output
OutputBaseFilename=AdminDashboard-Setup-{#MyAppVersion}
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
Source: "..\dist\AdminDashboard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
