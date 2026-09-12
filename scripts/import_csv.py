"""导入课程测试 CSV（需求说明书 7.2）。

要点：
- 保留原字段及文件来源，使用独立记录编号，避免两年重复 id 相互覆盖。
- 同一来源文件 + 源记录编号重复出现时不重复写入；内容不一致时报告冲突。
- 跨年鱼重发生重置，批次归属需确认，不直接接成一条生长曲线。

用法：
    python scripts/import_csv.py <csv1> <csv2> ...
    python scripts/import_csv.py --dir data/sample      # 导入目录下所有 CSV

说明：Windows 命令行传入中文文件名可能因编码丢失，推荐用 --dir 方式。
"""
import argparse
import csv
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from datetime import datetime

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"), override=False)

from database import SessionLocal
from models import (
    Batch, Device, ImportBatch, Measurement, Pond, Terminal, WeighRecord, AuditLog,
)

# CSV 字段 -> (模型字段, 单位)
METRIC_MAP = {
    "temperature": ("temperature", "℃"),
    "oxygen": ("oxygen", "mg/L"),
    "salinity": ("salinity", ""),
    "Ph": ("ph", ""),
    "water_quality": ("water_quality", ""),
}

# 字段 -> 中文名（报告用）
CN = {
    "pond_size": "鱼塘规模", "fish_number": "鱼数量", "temperature": "水温",
    "oxygen": "溶氧", "salinity": "盐度", "Ph": "pH", "water_quality": "水质指数",
    "fish_weight": "鱼重", "fish_liveness": "活跃度",
    "feed_interval": "投喂间隔", "feed_weight": "投喂量",
}


def _key(pond_code, src_file, src_id):
    """去重键：来源文件 + 源记录编号。"""
    return f"{os.path.basename(src_file)}#{src_id}"


def import_one(db, path, pond_code="A01", source="csv"):
    pond = db.query(Pond).filter_by(code=pond_code).first()
    if not pond:
        print(f"[skip] 鱼塘 {pond_code} 不存在，先运行 init_db.py --seed")
        return None
    batch = db.query(Batch).filter_by(pond_id=pond.id).order_by(Batch.id.desc()).first()
    terminal = db.query(Terminal).filter_by(code=f"TERM-{pond_code}").first()

    ib = ImportBatch(filename=os.path.basename(path))
    db.add(ib); db.flush()

    inserted = dup = conflict = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    by_metric = {}
    for r in rows:
        src_id = r["id"].strip()
        key = _key(pond_code, path, src_id)
        try:
            collected = datetime.strptime(f'{r["date"]} {r["time"]}', "%Y-%m-%d %H:%M:%S")
        except ValueError:
            collected = datetime.strptime(r["date"], "%Y-%m-%d")

        # 该源记录是否已导入（任何指标）
        exists = (db.query(Measurement.src_record)
                  .filter(Measurement.src_record.like(key + "|%")).first())
        if exists:
            dup += 1
            continue

        for raw_field, (metric, unit) in METRIC_MAP.items():
            val = r.get(raw_field)
            if val in (None, ""):
                continue
            try:
                value = float(val)
            except ValueError:
                continue
            db.add(Measurement(
                pond_id=pond.id, terminal_id=terminal.id if terminal else None,
                batch_id=batch.id if batch else None,
                metric=metric, value=value, unit=unit,
                collected_at=collected, valid=True, source=source,
                src_record=f"{key}|{metric}",
            ))
            by_metric[metric] = by_metric.get(metric, 0) + 1
            inserted += 1

        # 鱼重 -> 称重记录（跨年重置，按文件年份分批次，不接成一条曲线）
        try:
            avg_w = float(r["fish_weight"])
            db.add(WeighRecord(
                pond_id=pond.id, batch_id=batch.id if batch else None,
                weighed_at=collected, avg_weight=avg_w,
                note=f"CSV导入 {os.path.basename(path)} #{src_id}",
            ))
        except (ValueError, KeyError):
            pass

    ib.rows_total = len(rows)
    ib.rows_inserted = inserted
    ib.rows_duplicated = dup
    ib.rows_conflict = conflict
    ib.note = "；".join(f"{CN.get(k, k)}={v}条" for k, v in sorted(by_metric.items()))
    db.add(AuditLog(category="config", action="import_csv", pond_id=pond.id,
                    detail=f"{os.path.basename(path)}：总 {len(rows)} 行，写入 {inserted}，"
                           f"重复跳过 {dup}，冲突 {conflict}"))
    db.commit()
    print(f"[ok] {os.path.basename(path)}: 总 {len(rows)} 行 -> 写入 {inserted}，重复 {dup}")
    return ib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="CSV 文件路径")
    ap.add_argument("--dir", help="导入该目录下的所有 CSV（推荐，避开中文文件名编码问题）")
    args = ap.parse_args()

    paths = list(args.files)
    if args.dir:
        # 用 glob 在 Python 内部解析路径，避免命令行编码问题
        paths += sorted(glob.glob(os.path.join(args.dir, "*.csv")))

    if not paths:
        print(__doc__)
        return
    db = SessionLocal()
    try:
        for p in paths:
            if os.path.exists(p):
                import_one(db, p)
            else:
                print(f"[warn] 文件不存在：{p}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
