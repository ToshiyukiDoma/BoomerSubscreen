# Ojii-san Subscreen
## Wanna try older sound voltex games with a touch screen monitor that has no subscreen support?

<img width="640" height="720" alt="preview" src="https://github.com/user-attachments/assets/4addd6b5-72b0-439b-9113-51a3ac098b1b" />

## Features:
- You can hide the system buttons which are the Test, Service, and Coin buttons.
- Multiple Custom Backgrounds (Image and/or Video are supported)
- Interchangeable backgrounds while the app is running
- GPU-backed Qt video rendering without per-frame CPU image conversion
- Sends keypad and operator controls directly through SpiceAPI
- Windows no-activate mode for testing with exclusive fullscreen
- Customizable buttons. You can also relocate them anywhere on the screen by editing the config.json
- Concentration Mode: The UI elements will disappear except for the background and will dim depending on your timeout and dim preference.

## Usage:
- Add `-api 1337` to the Spice2x options. Leave API Password empty for this first test build.
- Open and modify config.json to
    - Configure your background directory (Set by default at backgrounds)
    - Set which monitor it will open to (Set by default at 1)
    - Set `logging.show_status_overlay` and `logging.print_api_errors` to control logs
- Double-click `QUICK_START.bat`
- Run Sound Voltex through Spice2x
- Press a subscreen button once and confirm the status changes to `SpiceAPI connected`
- Test exclusive fullscreen. Touching keypad/Test/Service/Coin should not activate this window.
- Enjoy gaem
- Nice one

## Issues:
- It will not work with konasute
- SpiceAPI passwords are not supported in this initial test build.
- The background selector is rendered inside the subscreen window and can be used during exclusive fullscreen.


## DIY Build
```
pip install -r requirements.txt
pyinstaller --noconsole --onefile --name "OjiisanSubscreen" --icon="icon.ico" --collect-all PySide6 OjiisanSubscreen.py
```


##
###### - Made with ChatGPT and Google Gemini </br> - Button graphics were made from scratch

#### Uhe~
