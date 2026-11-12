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
#define Launcher "scripts\windows\run.bat"

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
; linger.  .venv, env (B210 kit) and MSK144 (recordings, logs) are kept.
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
; run.bat keeps a (minimized) console so startup errors stay readable.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#Launcher}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIco}"; Comment: "Wideband MSK144 skimmer"; Flags: runminimized
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#Launcher}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppIco}"; Comment: "Wideband MSK144 skimmer"; Flags: runminimized; Tasks: desktopicon

[Run]
; Build the Python environment.  Interactive installs show the console and
; pause on failure; silent installs (CI) run hidden.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\windows\install.ps1"" -SkipUhd -PauseOnError"; WorkingDir: "{app}"; StatusMsg: "Setting up Python packages (this can take a few minutes)..."; Flags: waituntilterminated; Check: not WizardSilent
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\windows\install.ps1"" -SkipUhd"; WorkingDir: "{app}"; StatusMsg: "Setting up Python packages..."; Flags: runhidden waituntilterminated; Check: WizardSilent
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\Install-UhdWindows.ps1"""; WorkingDir: "{app}"; StatusMsg: "Installing the Ettus UHD driver..."; Flags: waituntilterminated; Tasks: uhd
Filename: "{app}\{#Launcher}"; WorkingDir: "{app}"; Description: "Start WS Skim"; Flags: postinstall nowait skipifsilent runminimized

[UninstallDelete]
; Files created after install (Python environment, bytecode caches).
; MSK144 (recordings and logs) is left in place.
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

function InitializeSetup: Boolean;
var
  Ignored: Integer;
begin
  Result := True;
  if PythonRegistered or PythonOnPath then
    Exit;
  Result := False;
  if WizardSilent then
  begin
    Log('Python 3.10-3.14 not found; aborting.');
    Exit;
  end;
  if MsgBox('WS Skim needs Python 3.12 (3.10 to 3.13 also work) from python.org.' + #13#10#13#10 +
            'Install it first, ticking "Add python.exe to PATH", then run this installer again.' + #13#10#13#10 +
            'Open the Python download page now?',
            mbConfirmation, MB_YESNO) = IDYES then
    ShellExec('open', PythonUrl, '', '', SW_SHOWNORMAL, ewNoWait, Ignored);
end;
