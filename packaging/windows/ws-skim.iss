; WS Skim installer for Windows (Inno Setup 6).
;
; Built by packaging/windows/build-installer.ps1; do not compile by hand
; unless you pass the same defines:
;   iscc /DAppVersion=0.1.4 /DSourceDir=<staged tree> /DOutputDir=<dist> ws-skim.iss
;
; Per-user install (no administrator rights) into
; %LOCALAPPDATA%\Programs\WS-Skim.  After copying files it runs
; scripts\windows\install.ps1 to build the Python environment, then adds a
; Start-menu entry and (by default) a desktop icon.

#ifndef AppVersion
  #error AppVersion must be defined (/DAppVersion=...)
#endif
#ifndef SourceDir
  #error SourceDir must be defined (/DSourceDir=...)
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

#define AppName "WS Skim"
#define AppIco "scripts\windows\assets\ws-skim.ico"
; pythonw.exe: no console window.  Settings and logs live in the user's
; profile (see map144_app/user_paths.py); stdout/stderr go to console.log.
#define PyW "{app}\.venv\Scripts\pythonw.exe"

[Setup]
; Never change AppId: it is how upgrades find the existing install.
AppId={{6F3C2B7E-9A41-4D5B-8E2F-1C7A0B9D4E63}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Jeff Millar, WA1HCO
AppPublisherURL=https://github.com/wa1hco/WS-Skim
AppSupportURL=https://github.com/wa1hco/WS-Skim/issues
AppUpdatesURL=https://github.com/wa1hco/WS-Skim/releases
DefaultDirName={localappdata}\Programs\WS-Skim
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
LicenseFile={#SourceDir}\LICENSE
SetupIconFile={#SourceDir}\{#AppIco}
UninstallDisplayIcon={app}\{#AppIco}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
; No version in the name, so the README can link to
; releases/latest/download/WS-Skim-Setup.exe.
OutputBaseFilename=WS-Skim-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Put a WS Skim icon on the desktop"
Name: "uhd"; Description: "Install the Ettus UHD driver for a USRP B210 (asks for administrator rights)"; Flags: unchecked

[InstallDelete]
; Upgrades: clear old program files so renamed or removed modules do not
; linger.  .venv, env (B210 kit) and MSK144 (recordings) are kept.
Type: filesandordirs; Name: "{app}\map144_app"
Type: filesandordirs; Name: "{app}\flexclient"
Type: filesandordirs; Name: "{app}\experiments"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\vendor"
Type: filesandordirs; Name: "{app}\fortran_bp_test"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{#PyW}"; Parameters: """{app}\map144.py"""; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIco}"; Comment: "Wideband MSK144 skimmer"
Name: "{autodesktop}\{#AppName}"; Filename: "{#PyW}"; Parameters: """{app}\map144.py"""; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIco}"; Comment: "Wideband MSK144 skimmer"; Tasks: desktopicon

[Run]
; Build the Python environment.  Interactive installs show the console and
; pause on failure; silent installs (CI) run hidden.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\windows\install.ps1"" -SkipUhd -PauseOnError"; WorkingDir: "{app}"; StatusMsg: "Setting up Python packages (this can take a few minutes)..."; Flags: waituntilterminated; Check: not WizardSilent
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\windows\install.ps1"" -SkipUhd"; WorkingDir: "{app}"; StatusMsg: "Setting up Python packages..."; Flags: runhidden waituntilterminated; Check: WizardSilent
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\Install-UhdWindows.ps1"""; WorkingDir: "{app}"; StatusMsg: "Installing the Ettus UHD driver..."; Flags: waituntilterminated; Tasks: uhd
Filename: "{#PyW}"; Parameters: """{app}\map144.py"""; WorkingDir: "{app}"; Description: "Start WS Skim"; Flags: postinstall nowait skipifsilent; Check: VenvReady

[UninstallDelete]
; Files created after install (Python environment, bytecode caches).
; MSK144 (recordings) and the user's settings and logs are left in place.
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\map144_app"
Type: filesandordirs; Name: "{app}\flexclient"
Type: filesandordirs; Name: "{app}\experiments"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\vendor"
Type: filesandordirs; Name: "{app}\fortran_bp_test"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
const
  PythonUrl = 'https://www.python.org/downloads/';

function HasPythonCoreKey(Root: Integer; Minor: Integer): Boolean;
var
  Key: String;
begin
  Key := 'Software\Python\PythonCore\3.' + IntToStr(Minor) + '\InstallPath';
  Result := RegKeyExists(Root, Key);
end;

{ python.org installers register under PEP 514 keys.  Accept 3.10-3.14;
  install.ps1 picks the best one and falls back if 3.14 lacks wheels. }
function PythonRegistered: Boolean;
var
  Minor: Integer;
begin
  Result := False;
  for Minor := 10 to 14 do
  begin
    if HasPythonCoreKey(HKCU, Minor) or HasPythonCoreKey(HKLM32, Minor) or
       (IsWin64 and HasPythonCoreKey(HKLM64, Minor)) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

{ Fallback for Pythons that do not register (e.g. CI toolcache): the py
  launcher or python.exe on PATH, ignoring the Microsoft Store stub. }
function PythonOnPath: Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'),
    '/c (where py.exe || where python.exe) | findstr /v /i WindowsApps >nul',
    '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function VenvReady: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\.venv\Scripts\pythonw.exe'));
end;

{ No usable Python: install.ps1 installs CPython 3.12 for this user
  (winget, else python.org).  Tell interactive users before downloading. }
function InitializeSetup: Boolean;
var
  Answer: Integer;
  Ignored: Integer;
begin
  Result := True;
  if PythonRegistered or PythonOnPath then
    Exit;
  if WizardSilent then
  begin
    Log('Python not found; install.ps1 will install Python 3.12 for this user.');
    Exit;
  end;
  Answer := MsgBox('WS Skim needs Python, and none was found on this computer.' + #13#10#13#10 +
    'Setup can download and install Python 3.12 from python.org for you ' +
    '(about 26 MB, this user only, no administrator rights).' + #13#10#13#10 +
    'Yes: install Python automatically.' + #13#10 +
    'No: open the Python download page and stop.',
    mbConfirmation, MB_YESNOCANCEL);
  if Answer = IDYES then
    Exit;
  Result := False;
  if Answer = IDNO then
    ShellExec('open', PythonUrl, '', '', SW_SHOWNORMAL, ewNoWait, Ignored);
end;
