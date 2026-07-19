; Inno Setup script for CleanWispr (compile with iscc after scripts/build_windows.py)

#define AppName "CleanWispr"
#define AppVersion "0.2.0"

[Setup]
AppId={{8C1F6E7A-4B2D-4E1C-9A3F-CleanWispr01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=CleanWispr
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\CleanWispr.exe
OutputDir=..\dist
OutputBaseFilename=CleanWispr-setup-win64
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\CleanWispr\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\CleanWispr.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\CleanWispr.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\CleanWispr.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
