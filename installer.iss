; Inno Setup script for Maestro Converter
; Put this file in your maestro-app folder (next to the "dist" folder),
; then open it in Inno Setup and press F9.

[Setup]
AppName=Maestro Converter
AppVersion=1.0
DefaultDirName={autopf}\Maestro Converter
DefaultGroupName=Maestro Converter
OutputDir=installer_output
OutputBaseFilename=MaestroConverter-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\MaestroConverter.exe
SetupIconFile=icon.ico

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "dist\MaestroConverter\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Maestro Converter"; Filename: "{app}\MaestroConverter.exe"
Name: "{autodesktop}\Maestro Converter"; Filename: "{app}\MaestroConverter.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MaestroConverter.exe"; Description: "Launch Maestro Converter"; Flags: nowait postinstall skipifsilent
