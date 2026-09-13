"""执行前持久化任务认领；回执可重发，投料动作不可重放。"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class TaskJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tasks ("
                       "request_no TEXT PRIMARY KEY, task_no TEXT NOT NULL, receipt TEXT)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def claim(self, request_no, task_no):
        with self._connect() as db:
            cur = db.execute("INSERT OR IGNORE INTO tasks VALUES (?, ?, NULL)",
                             (request_no, task_no))
            return cur.rowcount == 1

    def finish(self, request_no, payload):
        with self._connect() as db:
            db.execute("UPDATE tasks SET receipt=? WHERE request_no=?",
                       (json.dumps(payload, ensure_ascii=False), request_no))

    def receipt(self, request_no):
        with self._connect() as db:
            row = db.execute("SELECT receipt FROM tasks WHERE request_no=?",
                             (request_no,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None
