; MotionDrive Windows installer (Inno Setup 6)
; Build:  iscc installer\setup.iss
; Produces: installer\MotionDrive-Setup.exe
;
; Prereqs before running iscc:
;   1. build\build.ps1 has produced build\dist\MotionDrive\* (onedir app)
;   2. installer\redist\ViGEmBusSetup_x64.exe is present (see installer\download_vigembus.ps1)

#define MyAppName "MotionDrive"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "MotionDrive"
#define MyAppExeName "MotionDrive.exe"
#define MyAppDist "..\build\dist\MotionDrive"

[Setup]
AppId={{7C7C6C2E-6B0D-4E36-9E7A-8F1D5F0B9A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=.
OutputBaseFilename=MotionDrive-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
CloseApplications=yes
CloseApplicationsFilter=MotionDrive.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 1507+ (build 10240) minimum -- also re-checked at runtime by the app.
MinVersion=10.0.10240

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#MyAppDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "redist\ViGEmBusSetup_x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: VigemRedistPresent

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Silently install the ViGEmBus virtual-gamepad driver (required for
; Virtual Gamepad input mode; Keyboard mode still works without it).
Filename: "{tmp}\ViGEmBusSetup_x64.exe"; Parameters: "/quiet /norestart"; \
    StatusMsg: "Installing virtual controller driver..."; Flags: waituntilterminated skipifsilent; \
    Check: VigemRedistPresent
Filename: "netsh.exe"; Parameters: "advfirewall firewall add rule name=""MotionDrive Controller Ports"" dir=in action=allow protocol=TCP localport=8765,8766 profile=any"; \
    Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""MotionDrive Controller Ports"""; \
    Flags: runhidden waituntilterminated

[Code]
function VigemRedistPresent(): Boolean;
begin
  Result := FileExists(ExpandConstant('{src}\redist\ViGEmBusSetup_x64.exe'));
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then
  begin
    MsgBox('MotionDrive requires 64-bit Windows 10 or 11.', mbCriticalError, MB_OK);
    Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Gracefully stop running instance before removing application files
    Exec('taskkill.exe', '/im MotionDrive.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(500);
    Exec('taskkill.exe', '/f /im MotionDrive.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
