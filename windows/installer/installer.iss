; Inno Setup script — ArcGIS Service Monitor
; Requires: Inno Setup 6  https://jrsoftware.org/isinfo.php
; Build EXE first with build.bat before compiling this script.

#define AppName      "ArcGIS Service Monitor"
#define AppVersion   "1.0.4"
#define AppPublisher "Your Organization"
#define ServiceName  "ArcGISMonitor"
#define ServiceExe   "ArcGISMonitor.exe"

[Setup]
AppId={{8F3A1B2C-4D5E-6F7A-8B9C-0D1E2F3A4B5C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=http://localhost:8000
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
Source: "nssm.exe"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\logs"; Permissions: everyone-full

[Icons]
Name: "{group}\{#AppName} — Open Dashboard"; Filename: "{app}\open_dashboard.bat"
Name: "{group}\{#AppName} — Uninstall";     Filename: "{uninstallexe}"

[Run]
; monitor_backend.py is a plain console app (no SCM integration) — "sc.exe create"
; pointed straight at it fails with error 1053 (never signals SERVICE_RUNNING).
; NSSM wraps it as a real service instead.
Filename: "{app}\nssm.exe"; \
  Parameters: "install {#ServiceName} ""{app}\{#ServiceExe}"" ""--host 0.0.0.0 --port {code:GetPort}"""; \
  Flags: runhidden; StatusMsg: "Registering Windows Service..."

Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppDirectory ""{app}"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} DisplayName ""{#AppName}"""; Flags: runhidden
Filename: "{app}\nssm.exe"; \
  Parameters: "set {#ServiceName} Description ""ArcGIS REST Service Health Monitor — http://localhost:{code:GetPort}"""; \
  Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} Start SERVICE_AUTO_START"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppStdout ""{app}\logs\app.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppStderr ""{app}\logs\app.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppRotateFiles 1"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set {#ServiceName} AppRotateBytes 10485760"; Flags: runhidden

Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""{#AppName}"" dir=in action=allow protocol=TCP localport={code:GetPort}"; \
  Flags: runhidden; StatusMsg: "Configuring Windows Firewall..."

Filename: "{app}\nssm.exe"; Parameters: "start {#ServiceName}"; \
  Flags: runhidden; StatusMsg: "Starting service..."

Filename: "{app}\open_dashboard.bat"; \
  Flags: nowait postinstall skipifsilent shellexec; \
  Description: "Open Dashboard (http://localhost:{code:GetPort})"

[UninstallRun]
Filename: "{app}\nssm.exe"; Parameters: "stop {#ServiceName}";           Flags: runhidden; RunOnceId: "StopSvc"
Filename: "{app}\nssm.exe"; Parameters: "remove {#ServiceName} confirm"; Flags: runhidden; RunOnceId: "DelSvc"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""{#AppName}"" dir=in protocol=TCP localport=80";   Flags: runhidden; RunOnceId: "FwRule80"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""{#AppName}"" dir=in protocol=TCP localport=8000"; Flags: runhidden; RunOnceId: "FwRule8000"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
var
  PortPage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  PortPage := CreateInputOptionPage(
    wpSelectDir,
    'Select Port',
    'Choose the port that the ArcGIS Service Monitor will listen on.',
    'Select a port:',
    True, False
  );
  PortPage.Add('Port 80  —  Access at http://<server>/  (no IIS or ARR required)');
  PortPage.Add('Port 8000  —  Access at http://<server>:8000/  (default)');
  PortPage.SelectedValueIndex := 1;
end;

function GetPort(Param: String): String;
begin
  if PortPage.SelectedValueIndex = 0 then
    Result := '80'
  else
    Result := '8000';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Port: String;
  DashboardUrl: String;
begin
  if CurStep = ssInstall then
  begin
    Exec('sc.exe', 'stop {#ServiceName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;

  if CurStep = ssDone then
  begin
    Port := GetPort('');
    DashboardUrl := 'http://localhost:' + Port;
    SaveStringToFile(ExpandConstant('{app}\open_dashboard.bat'),
      '@start "" ' + DashboardUrl + #13#10, False);
  end;
end;
