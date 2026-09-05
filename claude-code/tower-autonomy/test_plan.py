from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from next_task import ready_tasks


class PlanIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest: dict[str, Any] = json.loads((ROOT / "manifest.json").read_text())

    def test_graph_is_complete_and_references_resolve(self) -> None:
        tasks = self.manifest["tasks"]
        task_ids = {task["id"] for task in tasks}
        self.assertEqual(63, len(tasks))
        self.assertEqual(63, len(task_ids))
        self.assertEqual(229, sum(len(task["depends_on"]) for task in tasks))
        for task in tasks:
            self.assertLessEqual(set(task["depends_on"]), task_ids)
            self.assertTrue((ROOT / task["task_file"]).is_file())

    def test_handoff_status_matches_merged_work(self) -> None:
        self.assertEqual(["B01", "B02", "B03", "E01", "O01"], self.manifest["completed_task_ids"])
        self.assertEqual(5, self.manifest["counts"]["completed"])
        self.assertEqual(58, self.manifest["counts"]["remaining"])
        self.assertEqual(["B04"], [task["id"] for task in ready_tasks(self.manifest)])

    def test_topological_waves_cover_every_remaining_task_once(self) -> None:
        flattened = [task_id for wave in self.manifest["topological_waves"] for task_id in wave]
        self.assertEqual(58, len(flattened))
        self.assertEqual(58, len(set(flattened)))
        self.assertFalse(set(flattened) & set(self.manifest["completed_task_ids"]))


if __name__ == "__main__":
    unittest.main()
