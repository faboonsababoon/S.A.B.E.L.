from pathlib import Path
import stat
import tempfile
import unittest

from sabel.browser_models import BrowserTaskStatus
from sabel.browser_tasks import BrowserAuditLog, BrowserTaskManager


class BrowserTaskTests(unittest.TestCase):
    def test_task_scope_is_python_owned_and_only_direct_user_expansion_changes_it(self):
        manager = BrowserTaskManager(max_steps=8, step_extension=5)
        task = manager.create(
            "Open Taz Skylar's YouTube channel",
            "Open Taz Skylar's YouTube channel",
            "personal",
            {"youtube.com"},
        )
        self.assertEqual(task.profile_id, "personal")
        self.assertEqual(task.allowed_domains, {"youtube.com"})
        self.assertEqual(task.allowed_profiles, {"personal"})

        expansion = manager.expand_from_user(
            task,
            "Open my calendar too so I can compare times.",
            domains={"calendar.google.com"},
            profiles={"nyu"},
        )
        self.assertIn("calendar.google.com", task.allowed_domains)
        self.assertIn("nyu", task.allowed_profiles)
        self.assertIn("Additional direct user instruction", task.objective)
        self.assertIn("Open my calendar too", task.objective)
        self.assertEqual(task.user_approved_expansions, [expansion])

    def test_step_limit_stops_loops_and_user_can_extend_five_steps(self):
        manager = BrowserTaskManager(max_steps=2, step_extension=5)
        task = manager.create("request", "objective", "personal", {"youtube.com"})
        self.assertTrue(manager.record_step(task))
        self.assertTrue(manager.record_step(task))
        self.assertFalse(manager.record_step(task))
        self.assertEqual(task.status, BrowserTaskStatus.STEP_LIMIT_REACHED)
        manager.extend_steps_from_user(task)
        self.assertEqual(task.max_steps, 7)
        self.assertTrue(manager.record_step(task))

    def test_stop_cancels_authoritative_task(self):
        manager = BrowserTaskManager()
        task = manager.create("request", "objective", "personal", {"youtube.com"})
        manager.stop()
        self.assertEqual(task.status, BrowserTaskStatus.CANCELLED)
        self.assertIsNone(manager.current_task)

    def test_audit_log_is_bounded_restrictive_and_contains_no_sensitive_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".sabel" / "browser-actions.json"
            audit = BrowserAuditLog(limit=2, path=path)
            for index in range(3):
                audit.record(
                    task_id=f"task-{index}",
                    profile_id="personal",
                    domain="youtube.com",
                    action="browser_click",
                    result="verified",
                    confirmation_status="not_required",
                    policy_decision="allow",
                    timestamp=float(index),
                )
            self.assertEqual(len(audit.recent(10)), 2)
            self.assertNotIn("password", path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            summary = audit.concise_summary()
            self.assertIn("Recent browser actions", summary)
            self.assertNotIn("task-0", summary)
            self.assertNotIn("browser_click", summary)
            self.assertIn("click", summary)


if __name__ == "__main__":
    unittest.main()
