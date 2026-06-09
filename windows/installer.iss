; Botflow Companion — Windows installer (Inno Setup 6.1+)
; Installs the self-contained app, sets it to auto-start, and — if Apple's Mobile
; Device Support driver is missing — fetches Apple's official installer from
; apple.com and silently installs just the driver component (no iTunes app, no
; redistribution of Apple binaries).

#define MyAppName "Botflow Companion"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Botflow"
#define MyAppExeName "BotflowCompanion.exe"
#define ITunesUrl "https://www.apple.com/itunes/download/win64"

[Setup]
AppId={{8B0F70C0-9A11-4E55-9E2D-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\BotflowCompanion
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=Output
OutputBaseFilename=BotflowCompanionSetup
SetupIconFile=BotflowCompanion.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
; Portable Python runtime + app source + bundled tools (no PyInstaller freeze).
Source: "..\portable\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\portable\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "BotflowCompanion.ico"; DestDir: "{app}"; Flags: ignoreversion
; Bundled 7-Zip (extract the AMDS MSI out of Apple's installer). dontcopy = staged to {tmp} on demand.
Source: "7z.exe"; Flags: dontcopy
Source: "7z.dll"; Flags: dontcopy

[Icons]
; The tray app = runtime\pythonw.exe running app\app.py, with the brand icon.
Name: "{group}\Botflow Companion"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "app.py"; WorkingDir: "{app}\app"; IconFilename: "{app}\BotflowCompanion.ico"
Name: "{userstartup}\Botflow Companion"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "app.py"; WorkingDir: "{app}\app"; IconFilename: "{app}\BotflowCompanion.ico"

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "app.py"; WorkingDir: "{app}\app"; Description: "Launch Botflow Companion now"; Flags: nowait postinstall skipifsilent

[Code]
var
  DownloadPage: TDownloadWizardPage;

function AmdsInstalled(): Boolean;
begin
  Result := DirExists(ExpandConstant('{commonpf}\Common Files\Apple\Mobile Device Support'))
         or DirExists(ExpandConstant('{commonpf32}\Common Files\Apple\Mobile Device Support'));
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard();
begin
  DownloadPage := CreateDownloadPage(
    'Apple device driver',
    'Botflow needs Apple''s Mobile Device Support to talk to your iPhone over USB.',
    @OnDownloadProgress);
end;

procedure InstallAmds();
var
  tmp, itunes, msiDir, msi, sevenZ: String;
  rc: Integer;
begin
  if AmdsInstalled() then
    exit;

  tmp := ExpandConstant('{tmp}');
  ExtractTemporaryFile('7z.exe');
  ExtractTemporaryFile('7z.dll');
  sevenZ := tmp + '\7z.exe';

  // Fetch Apple's official installer (apple.com 301-redirects to the .exe).
  DownloadPage.Clear;
  DownloadPage.Add('{#ITunesUrl}', 'iTunes64Setup.exe', '');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
    except
      DownloadPage.Hide;
      MsgBox('Could not download the Apple device driver (no internet?). You can ' +
             'install it later — the companion will prompt you.', mbInformation, MB_OK);
      exit;
    end;
  finally
    DownloadPage.Hide;
  end;

  itunes := tmp + '\iTunes64Setup.exe';
  msiDir := tmp + '\amds';
  // Extract just the driver MSI from Apple's installer bundle.
  Exec(sevenZ, 'e "' + itunes + '" -o"' + msiDir + '" AppleMobileDeviceSupport64.msi -y',
       '', SW_HIDE, ewWaitUntilTerminated, rc);
  msi := msiDir + '\AppleMobileDeviceSupport64.msi';
  if FileExists(msi) then
    Exec('msiexec.exe', '/i "' + msi + '" /qn /norestart', '', SW_HIDE, ewWaitUntilTerminated, rc)
  else
    MsgBox('Could not prepare the Apple device driver. The companion will guide you ' +
           'to install it.', mbInformation, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    InstallAmds();
end;
