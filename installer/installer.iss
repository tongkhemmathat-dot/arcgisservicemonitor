; Inno Setup script for ArcGIS Service Monitor
; Requires: Inno Setup 6.x  https://jrsoftware.org/isinfo.php
; Build EXE first with build.bat before compiling this script.

#define AppName      "ArcGIS Service Monitor"
#define AppVersion   "1.0.0"
#define AppPublisher "Your Organization"
#define AppURL       "http://localhost:8000"
#define ServiceName  "ArcGISMonitor"
#define ServiceExe   "ArcGISMonitor.exe"

[Setup]
AppId={{8F3A1B2C-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=output
OutputBaseFilename=ArcGISMonitor-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#ServiceExe}
MinVersion=6.1
SetupIconFile=
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Main application (PyInstaller --onedir output)
Source: "dist\ArcGISMonitor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Example config (only if config.json not already present)
Source: "..\config.example.json"; DestDir: "{app}"; DestName: "config.example.json"; Flags: ignoreversion

[Dirs]
Name: "{app}\logs"; Permissions: everyone-full

[Icons]
Name: "{group}\{#AppName} Dashboard"; Filename: "{app}\open_dashboard.bat"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
; Register Windows Service
Filename: "sc.exe"; \
  Parameters: "create {#ServiceName} binPath= ""{app}\{#ServiceExe}"" start= auto DisplayName= ""{#AppName}"""; \
  Flags: runhidden; StatusMsg: "Registering Windows Service..."

Filename: "sc.exe"; \
  Parameters: "description {#ServiceName} ""ArcGIS REST Service Health Monitor — Dashboard: http://localhost:8000"""; \
  Flags: runhidden

; Start the service
Filename: "sc.exe"; Parameters: "start {#ServiceName}"; \
  Flags: runhidden; StatusMsg: "Starting service..."

; Open dashboard in default browser (optional, user can skip)
Filename: "{app}\open_dashboard.bat"; \
  Flags: nowait postinstall skipifsilent shellexec; \
  Description: "Open Dashboard in browser"

[UninstallRun]
Filename: "sc.exe"; Parameters: "stop {#ServiceName}";   Flags: runhidden; RunOnceId: "StopSvc"
Filename: "sc.exe"; Parameters: "delete {#ServiceName}"; Flags: runhidden; RunOnceId: "DeleteSvc"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
// Stop existing service before upgrade
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    Exec('sc.exe', 'stop {#ServiceName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;
end;

// Warn if service failed to start
procedure CurStepChanged2(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if not Exec('sc.exe', 'query {#ServiceName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      MsgBox('Warning: Could not verify service status. Check logs at ' + ExpandConstant('{app}') + '\logs\app.log', mbInformation, MB_OK);
  end;
end;
