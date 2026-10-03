; Inno Setup script for Branch POS.
;
; Prerequisite: Inno Setup (https://jrsoftware.org/isinfo.php) - a free
; Windows tool, not something pip installs. Build the app first
; (pos_app\build_exe.bat), THEN compile this script:
;   iscc pos_app\installer\pos_app_installer.iss
; (or open it in the Inno Setup IDE and press Compile). Output lands in
; pos_app\installer_output\.
;
; TODO once branding exists: set MyAppPublisher, and point [Icons]/[Setup]
; at a real .ico (currently unset, so Windows uses a generic icon).

#define MyAppName "Branch POS"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "TODO: your company name"
#define MyAppExeName "BranchPOS.exe"

[Setup]
; Fixed, unique per app - do not reuse this GUID for admin_app's
; installer, and do not change it later (it's how Inno Setup recognizes
; "this is an upgrade of the same app" across versions).
AppId={{6BBC6B06-9B2B-4B86-8DEE-E6927500FABB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\installer_output
OutputBaseFilename=BranchPOS-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; No UAC prompt at every register - installs to the current user's
; profile if not run as admin. Matches "seamless install" for a cashier
; workstation where nobody wants to stop and enter admin credentials.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Pulls in the whole PyInstaller onedir output built by build_exe.bat.
Source: "..\dist\BranchPOS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offers to launch the app right after install finishes.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
