# The Tower — background ADB bot

Automates the Android game *The Tower* running in the Android Studio emulator.
All input goes through ADB (`input tap`), so the emulator window never needs
focus and your mouse is never taken over.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`adb` must be on your PATH (Android Studio ships it at
`~/Library/Android/sdk/platform-tools`):

```bash
export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools"
adb start-server
adb devices          # should list emulator-5554
```

## Capture templates

```bash
python grab_screen.py screen.png
```

Crop each button out of `screen.png` and save the crop into `templates/`.
Templates must come from the **same emulator resolution** the bot runs at —
`cv2.matchTemplate` is not scale invariant.

## Run

```bash
python tower_bot.py                 # infinite loop, 2s between scans
python tower_bot.py --once          # one scan
python tower_bot.py --debug-scores  # print best match score per template
```

Add buttons in `config.py` → `ACTIONS`; they are evaluated in order on each scan.
