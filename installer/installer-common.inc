; ============================================================
; ArcGIS Service Monitor — Common Installer Logic
; Included by installer-dev.iss / installer-staging.iss / installer-prod.iss
; Requires the following to be defined before #include:
;   AppEnv       e.g. "Production"
;   AppEnvLower  e.g. "prod"
;   AppPort      e.g. "8000"
;   AppId        unique GUID per environment
;   ServiceName  e.g. "ArcGISMonitor_Prod"
; ============================================================

#define AppName       "ArcGIS Service Monitor"
#define AppVersion    "1.0.0"
#define AppPublisher  "Your Organization"
#define ServiceExe    "ArcGISMonitor.exe"
#define AppFullName   AppName + " (" + AppEnv + ")"
#define AppDashboard  "http://localhost:" + AppPort

[Setup]
AppId={{{#AppId}}
AppName={#AppFullName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppDashboard}
AppSupportURL={#AppDashboard}
VersionInfoVersion={#AppVersion}

; Default install path includes env subfolder — user can change during install
DefaultDirName=C:\ArcGISMonitor\{#AppEnvLower}
DisableDirPage=no

DefaultGroupName={#AppName}
DisableProgramGroupPage=yes

OutputDir=output
OutputBaseFilename=ArcGISMonitor-{#AppEnv}-Setup-{#AppVersion}

Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#ServiceExe}
UninstallDisplayName={#AppFullName}
MinVersion=6.1
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; Clarify the dir page
SelectDirDesc=Select the folder where {#AppFullName} will be installed.
SelectDirLabel3=Setup will install {#AppFullName} into the following folder. To install into a different folder, click Browse and select another folder. Click Next to continue.

[Files]
; All files from the PyInstaller --onedir output
Source: "dist\ArcGISMonitor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Example config (for reference)
Source: "..\config.example.json"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\logs"; Permissions: everyone-full

[Icons]
Name: "{group}\{#AppFullName} — Open Dashboard"; Filename: "{app}\open_dashboard.bat"
Name: "{group}\{#AppFullName} — Uninstall";     Filename: "{uninstallexe}"

[Run]
; Register Windows Service with env-specific name and port
Filename: "sc.exe"; \
  Parameters: "create {#ServiceName} binPath= ""{app}\{#ServiceExe} --host 0.0.0.0 --port {#AppPort}"" start= auto DisplayName= ""{#AppFullName}"""; \
  Flags: runhidden; StatusMsg: "Registering Windows Service ({#ServiceName})..."

Filename: "sc.exe"; \
  Parameters: "description {#ServiceName} ""ArcGIS REST Service Health Monitor [{#AppEnv}] — Dashboard: {#AppDashboard}"""; \
  Flags: runhidden

; Allow port through Windows Firewall
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""{#AppFullName}"" dir=in action=allow protocol=TCP localport={#AppPort}"; \
  Flags: runhidden; StatusMsg: "Configuring Windows Firewall (port {#AppPort})..."

; Start the service
Filename: "sc.exe"; Parameters: "start {#ServiceName}"; \
  Flags: runhidden; StatusMsg: "Starting service..."

; Open dashboard (optional)
Filename: "{app}\open_dashboard.bat"; \
  Flags: nowait postinstall skipifsilent shellexec; \
  Description: "Open {#AppEnv} Dashboard ({#AppDashboard})"

[UninstallRun]
Filename: "sc.exe"; Parameters: "stop {#ServiceName}";   Flags: runhidden; RunOnceId: "Stop_{#ServiceName}"
Filename: "sc.exe"; Parameters: "delete {#ServiceName}"; Flags: runhidden; RunOnceId: "Del_{#ServiceName}"
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""{#AppFullName}"""; \
  Flags: runhidden; RunOnceId: "Fw_{#ServiceName}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
// Stop existing service before upgrade (silent — OK if not running)
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
