; Inno Setup script — ArcGIS Service Monitor
; Requires: Inno Setup 6  https://jrsoftware.org/isinfo.php
; Build EXE first with build.bat before compiling this script.

#define AppName      "ArcGIS Service Monitor"
#define AppVersion   "1.0.0"
#define AppPublisher "Your Organization"
#define AppPort      "8000"
#define AppDashboard "http://localhost:" + AppPort
#define ServiceName  "ArcGISMonitor"
#define ServiceExe   "ArcGISMonitor.exe"

[Setup]
AppId={{8F3A1B2C-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppDashboard}
VersionInfoVersion={#AppVersion}

DefaultDirName=C:\ArcGISMonitor
DisableDirPage=no

DefaultGroupName={#AppName}
DisableProgramGroupPage=yes

OutputDir=output
OutputBaseFilename=ArcGISMonitor-Setup-{#AppVersion}

Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#ServiceExe}
UninstallDisplayName={#AppName}
MinVersion=6.1
WizardStyle=modern
CloseApplications=yes

[Messages]
SelectDirDesc=Select the folder where {#AppName} will be installed.
SelectDirLabel3=Setup will install {#AppName} into the following folder. You may change it. Click Next to continue.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\ArcGISMonitor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\config.example.json"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\logs"; Permissions: everyone-full

[Icons]
Name: "{group}\{#AppName} — Open Dashboard"; Filename: "{app}\open_dashboard.bat"
Name: "{group}\{#AppName} — Uninstall";     Filename: "{uninstallexe}"

[Run]
Filename: "sc.exe"; \
  Parameters: "create {#ServiceName} binPath= ""{app}\{#ServiceExe} --host 0.0.0.0 --port {#AppPort}"" start= auto DisplayName= ""{#AppName}"""; \
  Flags: runhidden; StatusMsg: "Registering Windows Service..."

Filename: "sc.exe"; \
  Parameters: "description {#ServiceName} ""ArcGIS REST Service Health Monitor — {#AppDashboard}"""; \
  Flags: runhidden

Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""{#AppName}"" dir=in action=allow protocol=TCP localport={#AppPort}"; \
  Flags: runhidden; StatusMsg: "Configuring Windows Firewall (port {#AppPort})..."

Filename: "sc.exe"; Parameters: "start {#ServiceName}"; \
  Flags: runhidden; StatusMsg: "Starting service..."

Filename: "{app}\open_dashboard.bat"; \
  Flags: nowait postinstall skipifsilent shellexec; \
  Description: "Open Dashboard ({#AppDashboard})"

[UninstallRun]
Filename: "sc.exe"; Parameters: "stop {#ServiceName}";   Flags: runhidden; RunOnceId: "StopSvc"
Filename: "sc.exe"; Parameters: "delete {#ServiceName}"; Flags: runhidden; RunOnceId: "DelSvc"
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""{#AppName}"""; \
  Flags: runhidden; RunOnceId: "FwRule"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    Exec('sc.exe', 'stop {#ServiceName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;
end;
