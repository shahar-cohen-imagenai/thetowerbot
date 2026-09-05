from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def ready_tasks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    completed = set(manifest["completed_task_ids"])
    return [
        task
        for task in manifest["tasks"]
        if task["status"] != "completed" and set(task["depends_on"]) <= completed
    ]


def main() -> None:
    manifest_path = Path(__file__).with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text())
    for task in ready_tasks(manifest):
        print(f"{task['id']}\t{task['status']}\t{task['title']}\t{task['task_file']}")


if __name__ == "__main__":
    main()
