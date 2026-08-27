#define MyAppName "FX.palette"
#define MyAppPublisher "Paulo Edits"
#define MyAppExeName "FX.palette.exe"

#ifndef MyAppVersion
#define MyAppVersion "0.52.0"
#endif

#ifndef SourceDir
#define SourceDir "..\..\release\staging\FXPalette"
#endif

#ifndef CcxFile
#define CcxFile "..\dist\FXPalette.ccx"
#endif

[Setup]
; Different AppId from EffectPalette's own (a different, independently installable product now
; that Premiere loads the UXP plugin itself - the two can coexist during the migration).
AppId={{6E6C9B6F-8D3B-4C2E-9E77-2E7C6C1F9A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Without this, Inno Setup runs 32-bit and {commoncf}/{pf} silently redirect to the WOW64
; "Program Files (x86)" tree - which is NOT where Creative Cloud Desktop installs UPIA on a
; 64-bit machine, so the UpiaAvailable() check below would always report "not found" even with
; Creative Cloud Desktop genuinely installed (host-confirmed: this was the real cause of a false
; "Creative Cloud Desktop not found" failure during a real install attempt).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={localappdata}\FX.palette
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
AppMutex=Local\FX.palette.Application
CloseApplications=yes
RestartApplications=no
OutputDir=..\..\release
OutputBaseFilename=FX.palette_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Area de Trabalho"; GroupDescription: "Atalhos:"; Flags: checkedonce
Name: "startupicon"; Description: "Iniciar FX.palette junto com o Windows (recomendado para atalhos globais e Stream Deck)"; GroupDescription: "Inicializacao:"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\scripts\configure_premiere_shortcuts.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#CcxFile}"; DestDir: "{app}\plugin"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\FX.palette"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\FX.palette"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\FX.palette"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: startupicon

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\configure_premiere_shortcuts.ps1"" -LogDir ""{app}\data"""; StatusMsg: "Configurando atalhos do Premiere..."; Flags: runhidden waituntilterminated
Filename: "{code:GetUpiaPath}"; Parameters: "/install ""{app}\plugin\{code:GetCcxFileName}"""; StatusMsg: "Instalando o plugin UXP no Creative Cloud..."; Flags: runhidden waituntilterminated; Check: UpiaAvailable
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir FX.palette"; Flags: nowait postinstall skipifsilent

[Code]
function GetUpiaPath(Param: String): String;
begin
  Result := ExpandConstant('{commoncf}\Adobe\Adobe Desktop Common\RemoteComponents\UPI\UnifiedPluginInstallerAgent\UnifiedPluginInstallerAgent.exe');
end;

function GetCcxFileName(Param: String): String;
begin
  Result := ExtractFileName(ExpandConstant('{#CcxFile}'));
end;

function UpiaAvailable(): Boolean;
begin
  Result := FileExists(GetUpiaPath(''));
end;

function IsPremiereRunning(): Boolean;
var
  ResultCode: Integer;
begin
  Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -Command "if (Get-Process -Name ''Adobe Premiere Pro'' -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );
  Result := ResultCode = 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if IsPremiereRunning() then
    Result := 'Feche o Adobe Premiere Pro antes de continuar. Isso permite configurar com seguranca o perfil de atalhos e instalar o plugin corretamente.';
  if (Result = '') and (not UpiaAvailable()) then
    Result := 'Nao foi possivel encontrar o Creative Cloud Desktop (necessario para instalar o plugin UXP). Instale o Creative Cloud Desktop e tente novamente.';
end;
