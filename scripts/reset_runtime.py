"""重置运行数据（保留基础数据与导入的 CSV）。

用法：
    python scripts/reset_runtime.py          # 清空任务/建议/回执/核查/告警/事件/日志
    python scripts/reset_runtime.py --all    # 连环境测量与称重也清空
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from database import SessionLocal
from models import (Alert, AuditLog, FeedingFeedback, FeedingTask, FishEvent,
                    Measurement, Receipt, Review, Suggestion, WeighRecord)

# 删除顺序必须满足外键约束：先删引用方，再删被引用方。
# Receipt/Review/Alert/AuditLog 引用 FeedingTask；FeedingTask 引用 Suggestion。
RUNTIME = [Receipt, Review, Alert, AuditLog, FeedingTask, Suggestion,
           FeedingFeedback, FishEvent]
ALL = RUNTIME + [Measurement, WeighRecord]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="连环境测量与称重一起清空")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        for model in (ALL if args.all else RUNTIME):
            n = db.query(model).delete()
            print(f"  cleared {model.__tablename__}: {n}")
        # 释放设备占用
        from models import Terminal
        for t in db.query(Terminal).all():
            t.status = "offline"
        db.commit()
        print("[ok] 运行数据已重置")
    finally:
        db.close()


if __name__ == "__main__":
    main()
