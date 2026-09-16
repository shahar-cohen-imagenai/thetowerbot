from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image as PILImage

import device


@pytest.mark.parametrize("serials", [
    ["emulator-5556", "127.0.0.1:5557"],
    ["127.0.0.1:5555", "127.0.0.1:5555"],
])
def test_connect_refuses_missing_or_duplicate_requested_endpoint(monkeypatch, serials) -> None:
    client = MagicMock()
    client.device_list.return_value = [type("Attached", (), {"serial": serial})() for serial in serials]
    monkeypatch.setattr(device, "AdbClient", lambda **_: client)

    with pytest.raises(device.EmulatorError, match="identity incident"):
        device.connect_device(host="127.0.0.1", port=5555)


def test_capture_screen_converts_rgb_to_bgr() -> None:
    """adbutils returns PIL RGB; OpenCV needs BGR. A red pixel proves the swap."""
    fake = MagicMock()
    fake.screenshot.return_value = PILImage.new("RGB", (4, 4), (255, 0, 0))

    frame = device.capture_screen(fake)

    assert frame.shape == (4, 4, 3)
    # BGR: blue=0, green=0, red=255
    assert tuple(frame[0, 0]) == (0, 0, 255)


def test_capture_screen_demands_errors_not_black_frames() -> None:
    """error_ok=False matters: the default returns a black image on failure,
    which would leave the bot scanning blank frames forever."""
    fake = MagicMock()
    fake.screenshot.return_value = PILImage.new("RGB", (2, 2), (0, 0, 0))

    device.capture_screen(fake)

    fake.screenshot.assert_called_once_with(error_ok=False)


def test_tap_delegates_to_the_device() -> None:
    fake = MagicMock()
    device.tap(fake, 100, 200)
    fake.click.assert_called_once_with(100, 200)
