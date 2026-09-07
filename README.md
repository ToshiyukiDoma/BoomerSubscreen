# Ojii-san Subscreen

A touchscreen companion for **SOUND VOLTEX and beatmania IIDX** through SpiceAPI.
Support for other bemani games may come. But that will take some time. idk

<img width="480" alt="sdvx-home-preview" src="https://github.com/user-attachments/assets/3f0ee9a7-1094-467c-966a-8c50f4222f11" />
<img width="480" alt="iidx-home-preview" src="https://github.com/user-attachments/assets/3cf3505e-5ab1-4c48-bce1-d122b479f287" />
<img width="480" alt="iidx-sound-fx-preview" src="https://github.com/user-attachments/assets/c3e9e493-dce4-41c9-8c39-070a815c9fbe" />


## Features

- Keypads that can be hidden, operator controls, and selectable image/video backgrounds.
- Concentration Mode with adjustable idle timeout and dimming.
- IIDX: separate P1/P2 keypads, five Sound FX sliders, and a nine-character LED ticker.
- Scalable 1080p layout, including 720p support; opens on monitor 2 by default.
- Optional DLL launcher that starts the app with your game and closes it afterward.

## Setup

1. Extract the Windows bundle. Keep `OjiisanSubscreen.exe` and `_internal` together. **No Python installation needed.**
2. Enable SpiceAPI with `-api 1337` in your Spice2x arguments. Leave the API password unset.
3. In the application's `config.json`, set `game_mode` to `iidx` or `sdvx`.
4. Run `OjiisanSubscreen.exe`. Check the connection status below Home.

### Automatic launch

Copy the DLL and config from `Hooks/x86` for a **32-bit game**, or `Hooks/x64` for a **64-bit game**, into your game folder. Edit the config beside the DLL:

```text
{
  "game mode": "auto",
  "subscreen application directory": "E:\OjiisanSubscreen"
}
```

Point the directory to the folder containing the EXE, then add `-k ojiisansubscreen.dll` to Spice2x. The hook accepts pasted single-backslash paths. `auto` uses the application's game mode; `iidx` or `sdvx` overrides it.

**The hook and application configs are separate.** Preserve any existing game config; alternatively, keep the DLL/config in a subfolder and pass its DLL path to `-k`.

## Settings

Edit the config **beside the EXE**:

| Setting | Default / options |
| --- | --- |
| `monitor` | `2` |
| `game_mode` | `iidx` or `sdvx` |
| `application_resolution` | `{"width": 0, "height": 0}` uses monitor size; use `1280` / `720` for 720p |
| `spiceapi` | `127.0.0.1`, port `1337` |
| `background_directory` | `backgrounds` |

Keep the reference resolution at 1920 × 1080. Add PNG/JPG/JPEG/WEBP images or MP4/WEBM videos before launching, then select them in Backgrounds. Placeholder image/video defaults are included. Use forward slashes or doubled backslashes for paths in the application config.

## Notes

- Requires modern **64-bit Windows**, even with a 32-bit game. SDVX コナステ and API passwords are unsupported.
- Static backgrounds use fewer resources than video. Current fixes reduce animation, ticker, and hidden-page overhead; live performance depends on your setup.
- Startup trouble? Check `subscreen-launch.log` beside the EXE and `ojiisansubscreen.log` beside the DLL.
- Both hook architectures passed packaged launch/shutdown tests; target-game testing is still needed.

## Build from source

Install 64-bit Python 3.12 and run `BUILD_WINDOWS.bat` from the application source folder. Distribute the entire `dist\OjiisanSubscreen` folder. The supplied spec includes the icon, IIDX modules, assets, and Qt runtime. Hook build commands are included in the source archive.

---

Made purely with Codex at this point. Graphics were taken from the thing and made some edits for it. You know what I mean.

#### Uhe~
