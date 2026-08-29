"""Save one emulator frame to disk so you can crop button templates from it.

    python grab_screen.py screen.png

Crop the button out of the saved PNG (Preview: select -> Cmd+K -> Cmd+S) and
drop the crop into templates/ with the name used in config.ACTIONS.
"""

from __future__ import annotations

import sys

import cv2

import config
from tower_bot import capture_screen, connect_device


def main(argv: list[str]) -> int:
    out = argv[1] if len(argv) > 1 else "screen.png"
    device = connect_device(host=config.DEVICE_HOST, port=config.DEVICE_PORT)
    frame = capture_screen(device)
    cv2.imwrite(out, frame)
    print(f"Saved {out} ({frame.shape[1]}x{frame.shape[0]})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
