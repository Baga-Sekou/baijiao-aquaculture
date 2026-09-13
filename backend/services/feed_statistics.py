"""已结束任务的实际用料；缺失量不补确认量，失败后的已知投料也计入。"""
import math
from sqlalchemy import func
from models import FeedingTask


def consumption(db, pond_id, start=None, end=None):
    stamp = func.coalesce(FeedingTask.finished_at, FeedingTask.created_at)
    q = db.query(FeedingTask).filter(
        FeedingTask.pond_id == pond_id,
        FeedingTask.status.in_(("done", "stopped", "failed", "unknown")))
    if start is not None:
        q = q.filter(stamp >= start)
    if end is not None:
        q = q.filter(stamp <= end)
    known, missing = 0.0, 0
    tasks = q.all()
    for task in tasks:
        amount = task.actual_amount
        # 结果不明或仍占用设备时，实际量不能当成最终用量。
        if (task.status == "unknown" or task.device_locked or amount is None
                or not math.isfinite(amount) or amount < 0 or task.unit != "kg"):
            missing += 1
        else:
            known += amount
    return {"known_kg": round(known, 3), "unknown_tasks": missing,
            "complete": missing == 0, "task_count": len(tasks)}
