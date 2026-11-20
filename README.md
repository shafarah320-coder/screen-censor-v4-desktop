# Screen Censor 4.0 — Desktop Overlay

## What's new
- Desktop overlay is drawn above other Windows applications, not only inside the app preview.
- Face detection remains enabled for all detected faces.
- NudeNet scans overlapping screen tiles for `FEMALE_BREAST_EXPOSED` with a lower confidence threshold (0.28) and larger censor padding. This can improve recall but may create more false positives.
- Censor patches use Gaussian blur, a red border, and the label `losser`.
- Overlay redraw target is 30 FPS. AI detection is not guaranteed to run at 30 FPS; actual detection speed depends on CPU/GPU, screen resolution, and the number of tile scans.
- Ctrl+Shift+F12 is registered as a Windows global emergency stop.

## Important safety notes
This build uses a Windows transparent, click-through topmost overlay and requests `WDA_EXCLUDEFROMCAPTURE` to prevent recursive screen capture. This is intended to avoid the old feedback/black-screen behavior, but it has not been validated on every Windows graphics driver or multi-monitor setup. Test first with non-sensitive content and keep Task Manager available. If the display behaves incorrectly, press Ctrl+Shift+F12 or end `ScreenCensor.exe` in Task Manager.

This release targets the primary monitor only. Detection is probabilistic and can miss or incorrectly flag content. The `FEMALE_BREAST_EXPOSED` class does not guarantee detection of every chest image, especially at small sizes, motion blur, compression, or unusual angles. A threshold of 0.28 favors recall over precision.

## Build
Upload the contents of this folder to a NEW GitHub repository, preserving `.github/workflows/build-windows.yml`.
Then open Actions and wait for a green Success. Download artifact `ScreenCensor-4-DesktopOverlay-Installer`, extract it, and run `ScreenCensor_4_Desktop_Setup.exe`.

Do not overwrite older Screen Censor repositories or reuse older installers.
