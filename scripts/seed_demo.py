"""生成一套完整的演示场景数据，便于看板展示与答辩演示。

内容：近 30 天环境曲线、若干已完成投喂任务、一个待核查任务、一条卡料告警、
      一条疑似死鱼事件、若干运营记录（计划/称重/用药/成本）。

用法：
    python scripts/seed_demo.py
"""
import os
import random
import sys
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from database import SessionLocal
from models import (Alert, Batch, CostRecord, FeedingFeedback, FeedingTask,
                    FishEvent, MedicineRecord, Measurement, PlanTask, Pond,
                    Receipt, Review, Suggestion, Terminal, WeighRecord)
from services.common import next_code

rng = random.Random(20260912)
NOW = datetime.now()


def seed_env(db, pond_id, batch_id, terminal_id, days=30):
    n = 0
    for d in range(days, -1, -1):
        t0 = NOW - timedelta(days=d)
        for metric, base, amp, unit in (
            ("temperature", 24.0, 4.0, "℃"),
            ("oxygen", 6.4, 1.0, "mg/L"),
            ("ph", 7.08, 0.12, ""),
        ):
            for h in (8, 14, 20):
                ts = t0.replace(hour=h, minute=rng.randint(0, 55), second=0, microsecond=0)
                if ts > NOW:
                    continue
                # 日内正弦 + 噪声
                val = base + amp * ((h - 14) / 14) + rng.uniform(-0.4, 0.4)
                if metric == "ph":
                    val = round(base + rng.uniform(-0.1, 0.1), 2)
                    unit_v = ""
                else:
                    val = round(val, 2)
                    unit_v = unit
                db.add(Measurement(pond_id=pond_id, terminal_id=terminal_id,
                                   batch_id=batch_id, metric=metric, value=val,
                                   unit=unit_v, collected_at=ts, valid=True,
                                   source="sim"))
                n += 1
    return n


def seed_tasks(db, pond_id, batch_id, terminal_id, device_id, user_id, count=8):
    """已完成任务 + 1 个待核查 + 1 条卡料失败。"""
    base = NOW - timedelta(days=count * 2 + 3)
    made = []
    for i in range(count):
        ts = base + timedelta(days=i * 2, hours=rng.randint(0, 4))
        sg = Suggestion(code=next_code(db, Suggestion, "code", "SG"),
                        pond_id=pond_id, batch_id=batch_id, status="confirmed",
                        amount=round(rng.uniform(8, 14), 2), unit="kg",
                        reason="演示规则 rule-v1.0（测试用途，非养殖依据）："
                               "鱼重 × 基础比例 × 温度系数 × 摄食系数",
                        rule_version="rule-v1.0", generated_at=ts,
                        valid_until=ts + timedelta(minutes=30))
        db.add(sg); db.flush()
        amount = sg.amount
        task = FeedingTask(
            task_no=next_code(db, FeedingTask, "task_no", "TK"),
            request_no="REQ-" + uuid.uuid4().hex[:10],
            suggestion_id=sg.id, pond_id=pond_id, batch_id=batch_id,
            device_id=device_id, terminal_id=terminal_id,
            suggested_amount=amount, confirm_amount=amount,
            actual_amount=round(amount * rng.uniform(0.96, 1.0), 2),
            actual_source="measured", unit="kg",
            status="done", approved_by=user_id, approved_at=ts,
            dispatched_at=ts + timedelta(seconds=5),
            started_at=ts + timedelta(seconds=8),
            finished_at=ts + timedelta(seconds=60),
            device_locked=False)
        db.add(task); db.flush()
        db.add(Receipt(task_id=task.id, kind="accept", status="accepted", seq=1,
                       occurred_at=ts + timedelta(seconds=6)))
        db.add(Receipt(task_id=task.id, kind="finish", status="done", seq=2,
                       actual_amount=task.actual_amount,
                       occurred_at=ts + timedelta(seconds=60)))
        made.append(task)

    # 待核查任务（回执超时）
    ts = NOW - timedelta(hours=6)
    sg = Suggestion(code=next_code(db, Suggestion, "code", "SG"), pond_id=pond_id,
                    batch_id=batch_id, status="confirmed", amount=10.5, unit="kg",
                    reason="演示规则 rule-v1.0（测试用途，非养殖依据）",
                    rule_version="rule-v1.0", generated_at=ts,
                    valid_until=ts + timedelta(minutes=30))
    db.add(sg); db.flush()
    t_unknown = FeedingTask(task_no=next_code(db, FeedingTask, "task_no", "TK"),
                            request_no="REQ-" + uuid.uuid4().hex[:10],
                            suggestion_id=sg.id, pond_id=pond_id, batch_id=batch_id,
                            device_id=device_id, terminal_id=terminal_id,
                            suggested_amount=10.5, confirm_amount=10.5, unit="kg",
                            status="unknown", approved_by=user_id, approved_at=ts,
                            dispatched_at=ts + timedelta(seconds=5),
                            device_locked=True)
    db.add(t_unknown); db.flush()
    db.add(Receipt(task_id=t_unknown.id, kind="accept", status="accepted", seq=1,
                   occurred_at=ts + timedelta(seconds=6)))
    db.add(Alert(code=next_code(db, Alert, "code", "AL"), pond_id=pond_id,
                 device_id=device_id, task_id=t_unknown.id, kind="receipt_timeout",
                 level="error", status="open",
                 message=f"任务 {t_unknown.task_no} 回执超时，结果待核实，已暂停追加"))

    # 卡料失败任务
    ts2 = NOW - timedelta(days=2, hours=3)
    sg2 = Suggestion(code=next_code(db, Suggestion, "code", "SG"), pond_id=pond_id,
                     batch_id=batch_id, status="confirmed", amount=9.0, unit="kg",
                     reason="演示规则 rule-v1.0（测试用途，非养殖依据）",
                     rule_version="rule-v1.0", generated_at=ts2,
                     valid_until=ts2 + timedelta(minutes=30))
    db.add(sg2); db.flush()
    t_fail = FeedingTask(task_no=next_code(db, FeedingTask, "task_no", "TK"),
                         request_no="REQ-" + uuid.uuid4().hex[:10],
                         suggestion_id=sg2.id, pond_id=pond_id, batch_id=batch_id,
                         device_id=device_id, terminal_id=terminal_id,
                         suggested_amount=9.0, confirm_amount=9.0, actual_amount=0.0,
                         actual_source="measured", unit="kg", status="failed",
                         approved_by=user_id, approved_at=ts2,
                         dispatched_at=ts2 + timedelta(seconds=5),
                         finished_at=ts2 + timedelta(seconds=40), device_locked=False)
    db.add(t_fail); db.flush()
    db.add(Alert(code=next_code(db, Alert, "code", "AL"), pond_id=pond_id,
                 device_id=device_id, task_id=t_fail.id, kind="feed_fail",
                 level="error", status="open",
                 message=f"任务 {t_fail.task_no} 执行失败：卡料，实际投喂量 0.0"))
    return made


def main():
    db = SessionLocal()
    try:
        pond = db.query(Pond).filter_by(code="A01").first()
        batch = db.query(Batch).filter_by(pond_id=pond.id, status="active").first()
        term = db.query(Terminal).filter_by(code="TERM-A01").first()
        from models import Device, User
        feeder = db.query(Device).filter_by(pond_id=pond.id, kind="feeder").first()
        wang = db.query(User).filter_by(username="wang").first()
        owner = db.query(User).filter_by(username="owner").first()

        # 清掉旧的 sim 数据，避免叠加
        db.query(Measurement).filter_by(source="sim").delete()
        db.commit()

        n_env = seed_env(db, pond.id, batch.id, term.id)
        seed_tasks(db, pond.id, batch.id, term.id, feeder.id, wang.id)

        # 摄食反馈
        for d in range(5):
            db.add(FeedingFeedback(pond_id=pond.id, batch_id=batch.id,
                                   state=rng.choice(["hungry", "normal", "full"]),
                                   source="manual",
                                   observed_at=NOW - timedelta(days=d, hours=2)))

        # 疑似死鱼事件
        db.add(FishEvent(code=next_code(db, FishEvent, "code", "EV"),
                         pond_id=pond.id, kind="suspected_death", status="pending",
                         detected_at=NOW - timedelta(hours=4),
                         position_desc="水面东北角", observation="识别到疑似死鱼对象",
                         source="vision", model_version="vision-sim-v1",
                         confidence=0.86))

        # 运营记录
        db.add(PlanTask(pond_id=pond.id, cycle="daily", content="清晨巡塘、记录水温与摄食情况",
                        assignee_id=wang.id, plan_time=NOW.replace(hour=7, minute=0),
                        due_time=NOW.replace(hour=9, minute=0), completed=True,
                        completed_at=NOW.replace(hour=8, minute=10)))
        db.add(PlanTask(pond_id=pond.id, cycle="weekly", content="抽样称重 30 尾",
                        assignee_id=wang.id, plan_time=NOW, due_time=NOW + timedelta(days=2)))
        db.add(MedicineRecord(pond_id=pond.id, batch_id=batch.id,
                              used_at=NOW - timedelta(days=8), item="生石灰",
                              amount=150, unit="kg", operator_id=wang.id,
                              note="定期水体消毒"))
        for i, w in enumerate((51.3, 180.0, 420.0, 640.0, 780.8)):
            db.add(WeighRecord(pond_id=pond.id, batch_id=batch.id,
                               weighed_at=NOW - timedelta(days=300 - i * 70),
                               avg_weight=w, sample_count=30, operator_id=wang.id,
                               note="定期抽样"))
        db.add(CostRecord(pond_id=pond.id, batch_id=batch.id, kind="actual",
                          item="饲料", amount=42000, unit="元", brand="某品牌海鲈料"))
        db.add(CostRecord(pond_id=pond.id, batch_id=batch.id, kind="plan",
                          item="预计产出", amount=180000, unit="元"))

        db.commit()
        print(f"[ok] 演示数据已生成：环境测量 {n_env} 条 + 投喂任务 10 条 + 告警 2 条 + 事件 1 条")
    finally:
        db.close()


if __name__ == "__main__":
    main()
