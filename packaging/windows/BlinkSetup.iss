; Inno Setup Script for Blink Face ID on Windows 10/11
; Compiles into a standalone one-click Blink-Setup-x64.exe installer

#define MyAppName "Blink"
#define MyAppVersion "2.1.0"
#define MyAppPublisher "Blink Biometrics"
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
Compression=lzma2/max
SolidCompression=no
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsePreviousAppDir=yes
DirExistsWarning=no

; Version Information
VersionInfoVersion=2.1.0.0
VersionInfoCompany=Blink Biometrics
VersionInfoDescription=Blink Face ID Biometric Authentication
VersionInfoCopyright=Copyright (C) 2026 Blink Biometrics. Released under GPLv3.
VersionInfoProductName=Blink
VersionInfoProductVersion=2.1.0.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupdaemon"; Description: "Automatically start Face ID unlock service on Windows logon"; GroupDescription: "Background Service:"; Flags: checkedonce

[Files]
; Dist folder produced by PyInstaller
Source: "..\..\dist\Blink\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\models\face_detection_yunet.onnx"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "..\..\models\w600k_mbf.onnx"; DestDir: "{app}\models"; Flags: ignoreversion
Source: "..\..\models\face_detection_yunet.onnx"; DestDir: "{app}\_internal\models"; Flags: ignoreversion
Source: "..\..\models\w600k_mbf.onnx"; DestDir: "{app}\_internal\models"; Flags: ignoreversion
Source: "..\..\models\face_detection_yunet.onnx"; DestDir: "{app}\_internal\blink\models"; Flags: ignoreversion
Source: "..\..\models\w600k_mbf.onnx"; DestDir: "{app}\_internal\blink\models"; Flags: ignoreversion

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
