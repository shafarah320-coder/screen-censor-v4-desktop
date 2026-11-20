[Setup]
AppId={{D7C20A12-1B57-4E9A-91F4-4D6A8B8C4404}
AppName=Screen Censor 4.0 Desktop Overlay
AppVersion=4.0.0
DefaultDirName={autopf}\Screen Censor 4.0
DefaultGroupName=Screen Censor 4.0
OutputDir=Output
OutputBaseFilename=ScreenCensor_4_Desktop_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "dist\ScreenCensor\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Screen Censor 4.0 Desktop Overlay"; Filename: "{app}\ScreenCensor.exe"
Name: "{autodesktop}\Screen Censor 4.0 Desktop Overlay"; Filename: "{app}\ScreenCensor.exe"
