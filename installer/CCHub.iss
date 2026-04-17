; Case Clicker Hub — Inno Setup script
; Build with:  iscc installer\CCHub.iss
; Output: installer\Output\CCHub-Setup-<version>.exe

#define AppName "Case Clicker Hub"
#define AppShort "CCHub"
#define AppVersion "1.0.0"
#define AppPublisher "Mikmail"
#define AppURL "https://github.com/Mikmail02/Alt-manager"
#define AppExeName "CCHub.exe"

[Setup]
AppId={{B6B3F2C9-5CA2-4A91-9F20-CCHUB-000000001}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; Install under user-writable LocalAppData so we never need admin rights
; and silent auto-updates work without UAC prompts.
DefaultDirName={localappdata}\{#AppShort}
DefaultGroupName={#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir=Output
OutputBaseFilename=CCHub-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=yes

[Languages]
Name: "norwegian"; MessagesFile: "compiler:Languages\Norwegian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Lag snarvei pa skrivebordet"; GroupDescription: "Snarveier"; Flags: unchecked
Name: "autostart"; Description: "Start {#AppName} ved innlogging"; GroupDescription: "Oppstart"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#AppShort}"; ValueData: """{app}\{#AppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; \
    Description: "Start {#AppName} nå"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the local root CA we installed on first run.
Filename: "certutil.exe"; Parameters: "-user -delstore Root ""Case Clicker Hub Local CA"""; \
    Flags: runhidden; RunOnceId: "RemoveCCHubCA"

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\CCHub"
