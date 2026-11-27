; Straty Kuchnia — установщик Inno Setup
; Программа и база живут в одной папке (по умолчанию C:\StratyKuchnia).
; Деинсталляция НЕ удаляет kuchnia.db — данные кухни остаются.

#define MyAppName "Straty Kuchnia"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "HTS — Hurshee Technology Solutions"
#define MyAppExeName "StratyKuchnia.exe"
; откуда брать собранную программу; CI и пробные сборки могут подменить
#ifndef SourceDir
  #define SourceDir "dist\StratyKuchnia"
#endif

[Setup]
AppId={{4C1E7A93-8D62-4B17-9E30-2F5A6C81D7B2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={sd}\StratyKuchnia
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=StratyKuchnia-Setup-{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Excludes — не украшение: в dist\StratyKuchnia\ оказывается kuchnia.db,
; если запустить собранный exe прямо оттуда (программа держит базу рядом
; с собой). Без этого исключения ignoreversion затёр бы базу клиента
; случайным состоянием с машины разработчика. В Beer Count это уже
; стоило порчи данных — повторять не будем.
Source: "{#SourceDir}\*";DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "kuchnia.db,kuchnia.db-journal,kuchnia.db-wal,kuchnia.db-shm"

[InstallDelete]
; свежий _internal при каждом обновлении — старые библиотеки не мешаются
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Flags: nowait postinstall skipifsilent
; тихое обновление из программы (/VERYSILENT): программа должна встать сама
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: WizardSilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  { спасение данных: база старой версии могла лежать в _internal —
    копируем её в папку программы ДО того, как _internal будет удалён }
  if CurStep = ssInstall then
  begin
    if FileExists(ExpandConstant('{app}\_internal\kuchnia.db')) and
       (not FileExists(ExpandConstant('{app}\kuchnia.db'))) then
      FileCopy(ExpandConstant('{app}\_internal\kuchnia.db'),
               ExpandConstant('{app}\kuchnia.db'), False);
  end;
end;
