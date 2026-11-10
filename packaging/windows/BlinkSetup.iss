; Inno Setup Script for Blink Face ID on Windows 10/11
; Compiles into a standalone one-click Blink-Setup-x64.exe installer

#define MyAppName "Blink"
#define MyAppVersion "2.1.0"
#define MyAppPublisher "Samay"
#define MyAppURL "https://github.com/realsamay/Blink"
#define MyAppExeName "blink.exe"

[Setup]
AppId={{D37F8E80-99E6-4A73-A3B0-835B5E6B7788}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=Blink-Setup-x64
OutputDir=..\..\dist
SetupIconFile=..\..\blink\resources\appicon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
UsePreviousAppDir=yes
DirExistsWarning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupdaemon"; Description: "Automatically start Face ID unlock service on Windows logon"; GroupDescription: "Background Service:"; Flags: checkedonce

[Files]
; Dist folder produced by PyInstaller
Source: "..\..\dist\Blink\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu launcher
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "settings"; IconFilename: "{app}\blink\resources\appicon.ico"
; Desktop launcher
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "settings"; IconFilename: "{app}\blink\resources\appicon.ico"; Tasks: desktopicon
; Background Daemon Autostart on Logon
Name: "{autostartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "daemon"; IconFilename: "{app}\blink\resources\appicon.ico"; Tasks: startupdaemon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "settings"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Parameters: "daemon"; Flags: nowait runminimized
