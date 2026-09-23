; TaxaTag installer, for Inno Setup 6.
;
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\taxatag.iss
;
; or, from the project root:
;
;   python installer\build_installer.py
;
; Inno Setup is a free download from https://jrsoftware.org/isdl.php and is
; not bundled: it is a build tool, not something TaxaTag needs to run.
;
; ---------------------------------------------------------------------------
; What this installs, and what it deliberately does not
;
; It installs the application only - about 430 MB. It does NOT install a
; reference library, which is around 2 GB and is data rather than program.
; Together they would exceed every practical hosting limit, and would force a
; 2.4 GB download on somebody who only wants to see whether the thing runs.
;
; That separation is not a compromise. TaxaTag ships with a self-test using
; four bundled samples whose answer is known, so a new user can confirm the
; installation works *before* downloading anything else. Choosing and fetching
; a library is the second step, from inside the application.
;
; ---------------------------------------------------------------------------
; Where things go
;
;   Program Files\TaxaTag\        the application. Replaced by an update.
;   %APPDATA%\TaxaTag\            settings, reference libraries, caches.
;                                 The user's, and never touched by uninstall.
;
; The application searches %APPDATA%\TaxaTag\reference for libraries, which is
; why the library can arrive at any time, before or after installation, and
; why an uninstall must leave it alone. Somebody who downloaded 2 GB should
; not lose it because they moved to a new version.

#define AppName        "TaxaTag"
#define AppVersion     "1.0.1"
#define AppPublisher   "Rudie Kauhanen"
#define AppURL         "https://github.com/Rudie-K/TaxaTag"
#define AppExeName     "TaxaTag.exe"
#define SourceDir      "..\dist\TaxaTag"

[Setup]
; Permanent. Inno Setup uses this as the uninstall registry key and as how an
; update recognises the copy it is replacing, so changing it would make the
; next version install *alongside* the old one on every machine that has it.
; Kept in step with src/version.py by tests/test_installer_script.py, which
; also checks it is a real GUID: the first value here was not, and Inno
; accepts any string, so it would have compiled and shipped.
AppId={{81659C9A-9140-41E7-9F73-50DAB247E428}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; Per-user by default, so no administrator password is needed. A great many
; users of this program are on a managed university or consultancy laptop and
; do not have one; requiring it would stop them installing at all. Anyone who
; does have it can still choose a machine-wide install from the first dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; The licence has to be read before installing. It is GPL-3, which grants the
; user rights rather than taking them away, and showing it is how they learn
; they have them.
LicenseFile=..\LICENSE
InfoBeforeFile=before-install.txt

OutputDir=..\dist\installer
OutputBaseFilename={#AppName}-{#AppVersion}-Windows-x64-Setup
SetupIconFile=..\resources\taxatag.ico
UninstallDisplayIcon={app}\{#AppExeName}

; LZMA2/max takes several minutes on 430 MB and saves a great deal of it.
; A download happens far more often than a build.
Compression=lzma2/max
SolidCompression=yes

WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 430 MB unpacked, plus room for the archive during install.
ExtraDiskSpaceRequired=52428800

; A running copy holds every library it has loaded, and replacing those files
; leaves an installation that is neither the old version nor the new one. The
; same failure the build script exists to prevent, in the user's hands.
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Shortcuts:"

[Files]
; The whole PyInstaller output: TaxaTag.exe and its _internal folder, which
; holds the interpreter, Qt, and the bundled command-line tools.
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
; The GPL alone would tell a reader that everything here may be
; redistributed, including the mark. It may not - see BRANDING.md.
Source: "..\resources\BRANDING.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\TaxaTag on the web"; Filename: "{#AppURL}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Run]
; Offered rather than automatic: an installer that launches something without
; asking is a small rudeness, and this one takes a moment to open.
Filename: "{app}\{#AppExeName}"; \
    Description: "Start {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller's _internal folder gains __pycache__ directories at run time
; that the installer never wrote, so an uninstall would otherwise leave the
; folder behind. Only what is under {app} - never the user's data.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
{ Warn, once, that removing the program does not remove the library. The user
  may have downloaded two gigabytes; deleting it silently would be rude, and
  deleting it at all would be presumptuous, since a reinstall would find it. }
function InitializeUninstall(): Boolean;
var
  LibraryPath: String;
begin
  Result := True;
  LibraryPath := ExpandConstant('{userappdata}\TaxaTag');
  if DirExists(LibraryPath) then
    MsgBox('Your settings and any reference libraries will be kept, in:'
           + #13#10#13#10 + LibraryPath + #13#10#13#10
           + 'A library can be several gigabytes, so it is left alone. '
           + 'Delete that folder yourself if you want it gone.',
           mbInformation, MB_OK);
end;
