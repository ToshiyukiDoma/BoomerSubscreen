# OjiisanSubscreen v20: build and configuration

## Build a Windows application

1. Install 64-bit Python 3.12 or newer on the build PC, with the Windows `py` launcher.
2. Open the application source folder (E:\OjiisanSubscreen on this PC).
3. Double-click BUILD_WINDOWS.bat. It creates an isolated .build-venv, installs the
   build dependencies and invokes the included PyInstaller specification.
4. Your application is dist\OjiisanSubscreen\OjiisanSubscreen.exe.
5. Copy the ENTIRE dist\OjiisanSubscreen folder, including _internal, wherever you
   want to run it. The destination does not need a Python installation.
6. Point the game-folder hook config at this output folder.

The EXE includes icon.ico as its Windows icon. Python modules (OjiisanSubscreen.py,
iidx_sfx.py, app_paths.py), Qt dependencies, and all ui PNGs are bundled automatically.
Keep _internal with the EXE. Optional external ui artwork can override the bundled
ui directory. Put custom backgrounds into the external backgrounds folder.
The build script preserves an existing output config.json; edit it if you need
new settings after rebuilding. This is a one-folder build for quicker startup.

Manual commands, from the source folder:

    py -3 -m venv .build-venv
    .build-venv\Scripts\python.exe -m pip install -r build-requirements.txt
    .build-venv\Scripts\python.exe build_windows.py

On this PC, if py reports no installed Python, the existing runtime can create
that environment instead (PowerShell):

    & "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .build-venv

Then run BUILD_WINDOWS.bat. A source/spec is supplied; a full frozen EXE build is
not claimed as tested in this release.

## 32-bit and 64-bit games

Use game-hook/x86/ojiisansubscreen.dll for a 32-bit Spice/game process and
 game-hook/x64/ojiisansubscreen.dll for a 64-bit Spice/game process. Copy ONE variant
as ojiisansubscreen.dll alongside the hook config. Do not load both into one game.

    -k ojiisansubscreen.dll

The launcher DLL must match the GAME PROCESS, not the resolution. The UI remains
64-bit and runs separately: a 32-bit game can launch it on 64-bit Windows. This does
not add 32-bit Windows OS support. Both hooks prefer OjiisanSubscreen.exe; if absent,
they use hook_start.py and the existing Python lookup. Keep SpiceAPI enabled using
your current host/port settings. No game memory or display resolution is changed.
If config.json already exists in the game directory, preserve it and merge these
fields, or put the DLL/config together in a dedicated subfolder and pass its DLL
path to -k.

## Easy directory paths (hook config beside DLL)

You may paste a Windows path directly:

    {
      "game mode": "iidx",
      "subscreen application directory": "E:\OjiisanSubscreen"
    }

Single backslashes are accepted by THIS hook's directory-field parser. Standard
JSON validators may reject this convenience syntax. Doubled backslashes and
forward slashes are accepted too. Use a colon after the drive letter (C:, not C;).
Use literal Unicode characters for folder names, rather than JSON \u escapes.
Paths with quotes are not valid Windows folder names.

## App mode fallback

The app config.json includes "game_mode": "iidx". A hook/command-line iidx or sdvx
override takes priority. Set hook "game mode" to "auto", or omit it, to use the app
fallback. Standalone launch also uses the app fallback. This selects the existing
IIDX/SDVX modes; it does not implement controls for other games automatically.

## Resolution (app config.json)

The layout always uses a 1920x1080 reference canvas. Leave reference_width and
reference_height at 1920 and 1080. The complete canvas, labels, buttons, sliders,
keypads and input coordinates scale together to the output window.

    "monitor": 2,
    "game_mode": "iidx",
    "application_resolution": { "width": 1280, "height": 720 }

Use 1920 and 1080 for 1080p, or 0 and 0 to use the selected monitor's size (default).
Other aspect ratios preserve proportions with black margins. Dimensions refer to
Qt window coordinates; Windows display scaling can affect their physical pixel size.
Monitor 2 falls back to the first available display if it is disconnected. This
setting controls the subscreen window, independently of the game's resolution.

The IIDX source sliders are 720p assets: their 1.5x size is retained on the 1080p
canvas and the entire canvas scales by 2/3 at 720p, restoring their original size.

## Validation / limitations

Offscreen checks cover 1080p, 720p and 4:3 scaling, transformed slider clicks, and
mode fallback/override. Both native hook architectures are tested in isolated
Windows host processes. Actual older IIDX builds and their video backgrounds still
need live testing. Source/build instructions are included for the frozen EXE.

References:
https://pyinstaller.org/en/stable/runtime-information.html
https://pyinstaller.org/en/stable/spec-files.html
https://doc.qt.io/qt-6/qgraphicsproxywidget.html
