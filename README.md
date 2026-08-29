# The Tower — background ADB bot

Automates the Android game *The Tower* running in the Android Studio emulator.
All input goes through ADB (`input tap`), so the emulator window never needs
focus and your mouse is never taken over.

Each scan captures one frame with `device.screenshot()`, matches every
configured template against it with `cv2.matchTemplate`, and sends an
`input tap x y` for each one that scores above its threshold.

## Install

Dependencies are managed with [uv](https://docs.astral.sh/uv/). If you don't
have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, from the repo root:

```bash
uv sync
```

That creates `.venv/`, installs the exact versions locked in `uv.lock`, and
downloads the Python pinned in `.python-version` (3.12) if it isn't already on
your machine. There is nothing to activate — every command below runs through
`uv run`, which re-syncs the environment first.

## Connect to the emulator

`adb` must be on your PATH (Android Studio ships it at
`~/Library/Android/sdk/platform-tools`):

```bash
export PATH="$PATH:$HOME/Library/Android/sdk/platform-tools"
adb start-server
adb devices          # should list emulator-5554
```

`config.py` holds both endpoints: `ADB_HOST`/`ADB_PORT` for the adb *server*
(default `127.0.0.1:5037`) and `DEVICE_HOST`/`DEVICE_PORT` for the emulator
itself (default `127.0.0.1:5555` — an AVD listens on its console port + 1).
`--host` / `--port` override the device endpoint for a single run.

## Capture templates

```bash
uv run grab_screen.py screen.png
```

Crop each button out of `screen.png` and save the crop into `templates/`.
Templates must come from the **same emulator resolution** the bot runs at —
`cv2.matchTemplate` is not scale invariant.

## Run

```bash
uv run tower_bot.py                 # infinite loop, 2s between scans
uv run tower_bot.py --once          # one scan
uv run tower_bot.py --debug-scores  # print best match score per template
uv run tower_bot.py --interval 5    # override the scan interval
```

## Configure

Add buttons in `config.py` → `ACTIONS`, one `Action(name, template, threshold)`
per button. All of them are evaluated top to bottom against the same captured
frame on every scan — matching is not exclusive, so several can fire in one
pass, in list order:

```python
ACTIONS: tuple[Action, ...] = (
    Action(name="Upgrade Health", template="upgrade_health.png", threshold=0.85),
)
```

Use `--debug-scores` to see the best match score for every template and pick a
threshold just below the score of a real match (the default is `0.8`).
`CLICK_COOLDOWN_SECONDS` keeps a slow UI animation from producing a burst of
taps on a button that was already pressed.

### Why there is a brightness check too

`cv2.TM_CCOEFF_NORMED` normalises away mean and variance, so it is **blind to
brightness**: a greyed-out "can't afford it yet" button still scores `1.000`
against a template cropped when the button was lit. On the match score alone the
bot happily taps upgrades you cannot buy, and taps straight into a modal dialog
that has dimmed the whole screen behind it.

So each match is also checked against the template's own grey level, and skipped
when the region is too dark. Tune it per button with `brightness_ratio`
(`DEFAULT_BRIGHTNESS_RATIO`, `0.75`); `0.0` disables the check for that action.
`--debug-scores` prints the measured ratio next to the score, so the way to pick
a value is to read the ratio in both states and set the threshold between them.
For reference, a dimmed-behind-a-modal button measures about `0.28`.

## Dependencies

Use uv rather than editing `pyproject.toml` by hand — it updates the lockfile
and the environment in one step:

```bash
uv add <package>
uv remove <package>
uv lock --upgrade      # refresh the locked versions
```

Commit `pyproject.toml` and `uv.lock` together so everyone resolves to the same
versions.
