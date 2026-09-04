"""Record ocr.read() output for a fixture, so tiles tests need no engine.

    uv run tools/record_ocr.py tests/fixtures/menu_workshop_attack.png
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ocr  # noqa: E402


def main(argv: list[str]) -> int:
    for name in argv:
        source = Path(name)
        screen = cv2.imread(str(source))
        boxes = [asdict(box) for box in ocr.read(screen)]
        out = source.parent / "ocr" / f"{source.stem}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(boxes, indent=2) + "\n")
        print(f"{out}: {len(boxes)} boxes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
