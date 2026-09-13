"""投喂回归：独立临时库，不连接设备、不改演示库。运行 python tests/test_feeding_safety.py。"""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend"), str(ROOT / "scripts")]
TEMP = tempfile.TemporaryDirectory(prefix="baijiao-safety-")
os.environ["DB_URL"] = "sqlite:///" + (Path(TEMP.name) / "test.db").as_posix()

from app import app
from database import Base, SessionLocal, engine
from models import FeedingTask, WeighRecord
from services.common import now
from simulator.terminal import Terminal
import init_db

RECOVERY = {"terminal_idle": True, "old_command_disabled": True, "device_ready": True}


class FeedingSafety(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with contextlib.redirect_stdout(io.StringIO()):
            init_db.seed()
        self.client = app.test_client()
        self.journal_dir = tempfile.TemporaryDirectory(dir=TEMP.name)
        self.addCleanup(self.journal_dir.cleanup)

    def call(self, method, path, user="owner", data=None):
        return self.client.open("/api" + path, method=method,
                                headers={"X-User": user} if user else {}, json=data)

    def task(self):
        self.call("POST", "/measurements", data={
            "pond_id": 2, "metric": "temperature", "value": 26, "unit": "℃",
            "collected_at": now().strftime("%Y-%m-%d %H:%M:%S"), "source": "sim"})
        sg = self.call("POST", "/ponds/2/suggestions", data={}).json["data"]
        r = self.call("POST", "/ponds/2/tasks", data={
            "request_no": "REQ-" + sg["code"], "suggestion_code": sg["code"],
            "confirm_amount": 41.85, "change_reason": "回归样例"})
        self.assertEqual(r.status_code, 200, r.json)
        return r.json["data"]

    def terminal(self, fault=None):
        t = Terminal("TERM-A02", 2, fault=fault, state_dir=self.journal_dir.name,
                     execution_seconds=0.02)
        t.poll_tasks = lambda: self.call("GET", "/terminals/TERM-A02/tasks").json["data"]
        t.send_receipt = lambda p: self.call("POST", "/receipts", data=p).json["ok"]
        return t

    def finish(self, task, status="stopped", actual=2):
        return self.call("POST", "/receipts", data={
            "task_no": task["task_no"], "kind": "finish", "status": status,
            "actual_amount": actual})

    def test_restart_after_lost_finish_replays_receipt_only(self):
        task = self.task()
        terminal = self.terminal()
        send = terminal.send_receipt
        terminal.send_receipt = lambda p: False if p["kind"] == "finish" else send(p)
        terminal.handle_task(task)
        self.assertEqual(self.call("GET", "/tasks/" + task["task_no"]).json["data"]["status"], "running")
        restarted = self.terminal()
        with patch.object(restarted.rng, "uniform", side_effect=AssertionError("不能再次投料")):
            restarted.handle_task(task)
        result = self.call("GET", "/tasks/" + task["task_no"]).json["data"]
        self.assertEqual(result["status"], "done")
        self.assertFalse(result["device_locked"])
        self.assertEqual(len([r for r in result["receipts"] if r["kind"] == "finish"]), 1)

    def test_incomplete_claim_survives_separate_process(self):
        task = self.task()
        path = self.terminal().journal.path
        script = ("from simulator.task_journal import TaskJournal; import sys; "
                  "assert TaskJournal(sys.argv[1]).claim(sys.argv[2], sys.argv[3])")
        subprocess.run([sys.executable, "-c", script, str(path), task["request_no"],
                        task["task_no"]], cwd=ROOT, check=True)
        restarted = self.terminal()
        with patch.object(restarted, "receipt", side_effect=AssertionError("不得恢复执行")):
            restarted.handle_task(task)
        with SessionLocal() as db:
            row = db.query(FeedingTask).one()
            row.dispatched_at = now() - timedelta(minutes=10)
            db.commit()
        self.call("POST", "/tasks/check-timeouts")
        result = self.call("GET", "/tasks/" + task["task_no"]).json["data"]
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["device_locked"])

    def test_stop_request_waits_for_terminal_and_checks_permission(self):
        task = self.task()
        path = "/tasks/" + task["task_no"] + "/stop"
        self.assertEqual(self.call("POST", path, user="wang", data={"reason": "停止"}).status_code, 403)
        self.assertEqual(self.call("POST", path, data={"reason": " "}).status_code, 400)
        result = self.call("POST", path, data={"reason": "现场发现异常"}).json["data"]
        self.assertEqual(result["status"], "dispatched")
        self.assertTrue(result["device_locked"])
        self.assertTrue(result["stop_requested"])
        terminal = self.terminal()
        # 终端拿到旧快照后，仍须在执行过程中获取新的停止请求。
        terminal.handle_task(task)
        result = self.call("GET", "/tasks/" + task["task_no"]).json["data"]
        self.assertEqual(result["status"], "stopped")
        self.assertIsNone(result["actual_amount"])
        self.assertFalse(result["device_locked"])
        self.assertEqual(self.call("POST", path, data={"reason": "重试"}).status_code, 400)

    def test_unsupported_stop_retains_lock_until_timeout_review(self):
        task = self.task()
        self.call("POST", "/tasks/" + task["task_no"] + "/stop", data={"reason": "异常"})
        self.terminal(fault="slow_stop").handle_task(task)
        result = self.call("GET", "/tasks/" + task["task_no"]).json["data"]
        self.assertEqual(result["status"], "running")
        self.assertTrue(result["device_locked"])
        self.assertFalse(any(r["kind"] in ("stop", "finish") for r in result["receipts"]))

    def test_partial_actual_consumption_consistent_across_endpoints(self):
        task = self.task()
        self.finish(task, actual=2)
        stats = self.call("GET", "/ponds/2/feed-stats").json["data"]
        dashboard = self.call("GET", "/ponds/2/dashboard").json["data"]["today"]
        compare = next(x for x in self.call("GET", "/compare").json["data"] if x["pond"]["id"] == 2)
        rank = next(x for x in self.call("GET", "/leaderboard").json["data"] if x["pond"]["id"] == 2)
        for amount in (stats["feed_today_kg"], dashboard["actual_feed_kg"], compare["feed_kg"], rank["feed_window_kg"]):
            self.assertEqual(amount, 2)
        self.assertEqual(dashboard["confirmed_feed_kg"], 41.85)

    def test_failed_partial_feed_counts_and_unknown_prevents_fcr(self):
        with SessionLocal() as db:
            for days, weight in ((20, 100), (0, 200)):
                db.add(WeighRecord(pond_id=2, weighed_at=now()-timedelta(days=days), avg_weight=weight))
            db.commit()
        self.finish(self.task(), status="failed", actual=3)
        self.finish(self.task(), status="done", actual=None)
        stats = self.call("GET", "/ponds/2/feed-stats").json["data"]
        self.assertEqual(stats["feed_today_kg"], 3)
        self.assertEqual(stats["feed_unknown_tasks"], 1)
        self.assertIsNone(stats["fcr"])
        rank = next(x for x in self.call("GET", "/leaderboard").json["data"] if x["pond"]["id"] == 2)
        self.assertIsNone(rank["rank"])
        self.assertIn("实际量未确定", rank["not_ranked_reason"])

    def test_missing_feeding_records_does_not_rank_as_zero_consumption(self):
        with SessionLocal() as db:
            for days, weight in ((20, 100), (0, 200)):
                db.add(WeighRecord(pond_id=2, weighed_at=now()-timedelta(days=days), avg_weight=weight))
            db.commit()
        self.assertIsNone(self.call("GET", "/ponds/2/feed-stats").json["data"]["fcr"])
        rank = next(x for x in self.call("GET", "/leaderboard").json["data"] if x["pond"]["id"] == 2)
        self.assertIsNone(rank["rank"])
        self.assertIn("没有已结束的投喂记录", rank["not_ranked_reason"])

    def test_active_task_cannot_be_reviewed_or_unlocked(self):
        task = self.task()
        r = self.call("POST", "/tasks/" + task["task_no"] + "/review", data={
            "conclusion": "done", "basis": "现场记录", "device_recovery": "x", "recovery_checks": RECOVERY})
        self.assertEqual(r.status_code, 400)
        result = self.call("GET", "/tasks/" + task["task_no"]).json["data"]
        self.assertEqual(result["status"], "dispatched")
        self.assertTrue(result["device_locked"])
        self.assertEqual(result["reviews"], [])

    def test_review_requires_basis_and_all_recovery_checks(self):
        task = self.task()
        with SessionLocal() as db:
            row = db.query(FeedingTask).one()
            row.status = "unknown"
            db.commit()
        path = "/tasks/" + task["task_no"] + "/review"
        body = {"conclusion": "not_executed", "basis": "查询终端并现场核查",
                "device_recovery": "已停机且清除旧任务", "recovery_checks": RECOVERY}
        for invalid in ({"basis": " "}, {"recovery_checks": {}}, {"conclusion": "unknown"},
                        {"recovery_checks": {**RECOVERY, "device_ready": "true"}}):
            self.assertEqual(self.call("POST", path, data={**body, **invalid}).status_code, 400)
        result = self.call("POST", path, data={**body, "device_recovery": None}).json["data"]
        self.assertTrue(result["device_locked"])
        result = self.call("POST", path, data=body).json["data"]
        self.assertFalse(result["device_locked"])
        self.assertIsNone(result["actual_amount"])
        self.assertEqual(len(result["reviews"]), 2)
        result = self.call("POST", path, data={"conclusion": "unknown", "basis": "发现相矛盾的记录"}).json["data"]
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["device_locked"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        engine.dispose()
        TEMP.cleanup()
