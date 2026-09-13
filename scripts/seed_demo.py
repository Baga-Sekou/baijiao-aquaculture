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


def seed_recent_hours(db, pond_id, batch_id, terminal_id, hours=26):
    """补一段最近逐小时样本（供水质趋势模型训练用，≥12 个小时桶）。"""
    n = 0
    for h in range(hours, -1, -1):
        ts = (NOW - timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
        if ts > NOW:
            continue
        for metric, base, amp, unit in (
            ("temperature", 25.5, 1.6, "℃"),
            ("oxygen", 6.6, 0.6, "mg/L"),
        ):
            val = round(base + amp * ((h % 12) / 12.0) + rng.uniform(-0.2, 0.2), 2)
            db.add(Measurement(pond_id=pond_id, terminal_id=terminal_id,
                               batch_id=batch_id, metric=metric, value=val,
                               unit=unit, collected_at=ts, valid=True,
                               source="sim"))
            n += 1
    return n


def seed_tasks(db, pond_id, batch_id, terminal_id, device_id, user_id, count=50):
    """已完成任务 + 1 个待核查 + 1 条卡料失败。

    投喂量按课堂 CSV 的真实量级（77~1066 kg/次）设定，单次约 970 kg；
    已完成任务均匀分布在最近 250 天内，与最后两次称重的区间重合，
    使 FCR 落在真实范围（约 1.5，即 1.5kg 饲料长 1kg 鱼）。
    """
    made = []
    for i in range(count):
        ts = NOW - timedelta(days=250 - i * 5, hours=rng.randint(0, 4))
        sg = Suggestion(code=next_code(db, Suggestion, "code", "SG"),
                        pond_id=pond_id, batch_id=batch_id, status="confirmed",
                        amount=round(rng.uniform(910, 1030), 2), unit="kg",
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
            created_at=ts, updated_at=ts + timedelta(seconds=60),
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


def seed_pond(db, pond_code, wang, owner, with_tasks=True):
    """给指定鱼塘灌演示数据（环境曲线 + 可选投喂任务/事件/运营记录）。

    课堂 CSV 只有 A01，A02 没有任何真实读数；这里为 A02 生成 source=sim
    的演示曲线，使多塘比较有可比对象。数据来源已在界面上标为仿真。

    幂等：本函数会先清除该塘此前由本脚本生成的数据（signature 标记），
    反复运行不会叠加任务与告警。
    """
    pond = db.query(Pond).filter_by(code=pond_code).first()
    if not pond:
        print(f"[skip] 鱼塘 {pond_code} 不存在")
        return 0, 0
    _clear_pond(db, pond.id)

    batch = db.query(Batch).filter_by(pond_id=pond.id, status="active").first()
    from models import Device, Terminal
    term = db.query(Terminal).filter_by(pond_id=pond.id).first()
    feeder = db.query(Device).filter_by(pond_id=pond.id, kind="feeder").first()

    n_env = seed_env(db, pond.id, batch.id, term.id if term else None)
    n_env += seed_recent_hours(db, pond.id, batch.id, term.id if term else None)

    if not with_tasks:
        # 次要鱼塘只给环境曲线，避免与主塘的任务/告警重复刷屏
        return n_env, 0

    seed_tasks(db, pond.id, batch.id, term.id, feeder.id, wang.id)

    for d in range(5):
        db.add(FeedingFeedback(pond_id=pond.id, batch_id=batch.id,
                               state=rng.choice(["hungry", "normal", "full"]),
                               source="manual",
                               observed_at=NOW - timedelta(days=d, hours=2)))

    db.add(FishEvent(code=next_code(db, FishEvent, "code", "EV"),
                     pond_id=pond.id, kind="suspected_death", status="pending",
                     detected_at=NOW - timedelta(hours=4),
                     position_desc="水面东北角", observation="识别到疑似死鱼对象",
                     source="vision", model_version="vision-sim-v1",
                     confidence=0.86))

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
    # 称重记录必须落在批次窗口内（投苗日之后），否则会被 growth 模型
    # 与 FCR 统计正确排除；本批次投苗日为 2026-01-10，故取 235 天内。
    # FCR 口径为「首尾两次称重」之间的区间（235 天前 180g → 12 天前 780.8g）：
    #   增重 = (780.8-180) × 54000 / 1000 ≈ 32,443 kg
    #   区间用料 ≈ 该区间内已完成的投喂任务（约 41,800 kg）
    #   FCR ≈ 1.29，落在真实鲈鱼饲料系数范围（约 1.2~1.8）
    for d, w in ((235, 180.0), (160, 400.0), (85, 563.0), (12, 780.8)):
        db.add(WeighRecord(pond_id=pond.id, batch_id=batch.id,
                           weighed_at=NOW - timedelta(days=d),
                           avg_weight=w, sample_count=30, operator_id=wang.id,
                           note="定期抽样"))
    db.add(CostRecord(pond_id=pond.id, batch_id=batch.id, kind="actual",
                      item="饲料", amount=42000, unit="元", brand="某品牌海鲈料"))
    db.add(CostRecord(pond_id=pond.id, batch_id=batch.id, kind="plan",
                      item="预计产出", amount=180000, unit="元"))
    return n_env, 1


def _clear_pond(db, pond_id):
    """清除本脚本此前为该塘生成的数据，保证 seed 幂等。

    只删由 seed_demo 产生的记录：任务/建议按其 request_no/task_no 前缀
    无法区分，故按「该塘 + 关联对象」整体清理运行数据，再重建。
    导入的 CSV 测量（source='csv'）保留。
    """
    from models import (Alert, CostRecord, FeedingFeedback, FeedingTask,
                        FishEvent, Measurement, MedicineRecord, PlanTask,
                        Receipt, Review, Suggestion, WeighRecord)

    task_ids = [t.id for t in db.query(FeedingTask).filter_by(pond_id=pond_id).all()]
    if task_ids:
        db.query(Receipt).filter(Receipt.task_id.in_(task_ids)).delete(synchronize_session=False)
        db.query(Review).filter(Review.task_id.in_(task_ids)).delete(synchronize_session=False)
    db.query(Alert).filter(Alert.pond_id == pond_id).delete(synchronize_session=False)
    db.query(FeedingTask).filter(FeedingTask.pond_id == pond_id).delete(synchronize_session=False)
    db.query(Suggestion).filter(Suggestion.pond_id == pond_id).delete(synchronize_session=False)
    db.query(FeedingFeedback).filter(FeedingFeedback.pond_id == pond_id).delete(synchronize_session=False)
    db.query(FishEvent).filter(FishEvent.pond_id == pond_id).delete(synchronize_session=False)
    db.query(PlanTask).filter(PlanTask.pond_id == pond_id).delete(synchronize_session=False)
    db.query(MedicineRecord).filter(MedicineRecord.pond_id == pond_id).delete(synchronize_session=False)
    db.query(CostRecord).filter(CostRecord.pond_id == pond_id).delete(synchronize_session=False)
    # 保留 CSV 导入的真实数据，只重建仿真曲线与抽样称重
    db.query(Measurement).filter(Measurement.pond_id == pond_id,
                                 Measurement.source == "sim").delete(synchronize_session=False)
    db.query(WeighRecord).filter(WeighRecord.pond_id == pond_id,
                                 WeighRecord.note == "定期抽样").delete(synchronize_session=False)
    db.commit()


def main():
    db = SessionLocal()
    try:
        from models import User
        wang = db.query(User).filter_by(username="wang").first()
        owner = db.query(User).filter_by(username="owner").first()

        # seed_pond 内部会先清理该塘的演示数据，反复运行不会叠加
        # A01 为主塘（含 CSV 历史数据），给完整演示集；
        # A02 只给环境曲线，使多塘比较有可比对象
        n1, t1 = seed_pond(db, "A01", wang, owner, with_tasks=True)
        n2, t2 = seed_pond(db, "A02", wang, owner, with_tasks=False)

        db.commit()
        print("[ok] 演示数据已生成（幂等，可重复运行）：")
        print(f"     A01 环境测量 {n1} 条 + 投喂任务 10 条 + 告警 + 事件 + 运营记录")
        print(f"     A02 环境测量 {n2} 条（仅环境曲线，供多塘比较）")
        print("     来源均标为 sim；导入的 CSV 历史数据（source=csv）保留")
    finally:
        db.close()


if __name__ == "__main__":
    main()
