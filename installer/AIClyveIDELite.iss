; Inno Setup script - AI Clyve IDE Lite
; Build: ISCC.exe /DAppVersion=1.1.0 installer\AIClyveIDELite.iss
; Hasil: dist\AIClyveIDELite-Setup-<versi>.exe  (per-user, tanpa admin)
;
; Kenapa per-user (PrivilegesRequired=lowest):
;   - tidak memicu UAC, bisa dipasang user tanpa hak admin
;   - update senyap dari dalam app bisa jalan tanpa prompt elevasi
;   - uninstaller terdaftar di Apps & Features per user

#define AppName "AI Clyve IDE Lite"
#define AppExe "AIClyveIDELite.exe"
#define AppPublisher "Clyve"
#define AppURL "https://ai.clyve.id"

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\AIClyveIDELite"
#endif

[Setup]
AppId={{7C4F1E2A-9D3B-4A62-8E11-5B7C0F2A6D91}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
DefaultDirName={localappdata}\Programs\AIClyveIDELite
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=AIClyveIDELite-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Update dari dalam app: tutup proses lama lewat Restart Manager, jangan restart otomatis
CloseApplications=force
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Buat shortcut di Desktop"; GroupDescription: "Shortcut:"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Instalasi normal: tawarkan buka app. Update senyap: langsung buka lagi app-nya.
Filename: "{app}\{#AppExe}"; Description: "Jalankan {#AppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
; Cache update yang terunduh di temp dibiarkan (OS yang bersihkan).
; Settings user (%APPDATA%\clyve-ide) sengaja TIDAK dihapus: API key + riwayat chat aman saat reinstall.
