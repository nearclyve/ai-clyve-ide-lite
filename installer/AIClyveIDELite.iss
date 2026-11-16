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
; File aplikasi dihapus otomatis. Data user (%APPDATA%\clyve-ide) TIDAK dihapus
; default supaya reinstall/upgrade tidak kehilangan chat & API key; uninstaller
; menanyakan apakah mau dihapus juga (lihat [Code] di bawah).

[Code]
// Saat uninstall selesai, tawarkan hapus data lokal: settings.json (API key,
// workspace), chats/, checkpoints/, uploads/. Default tombol = "No" supaya
// tidak terhapus karena salah pencet. Update aplikasi tidak lewat uninstall,
// jadi data tetap aman saat update.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\clyve-ide');
    // Uninstall senyap (otomatis) tidak boleh menggantung menunggu jawaban:
    // data dibiarkan, user bisa hapus manual.
    if (not UninstallSilent) and DirExists(DataDir) then
    begin
      if MsgBox('Hapus juga data lokal AI Clyve IDE Lite?' + #13#10 + #13#10 +
                'Termasuk: API key, riwayat chat, checkpoint, dan lampiran.' + #13#10 +
                'Lokasi: ' + DataDir + #13#10 + #13#10 +
                'Pilih No kalau mau install ulang tanpa kehilangan data.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
