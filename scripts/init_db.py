"""建库建表 + 初始化基础数据。

用法：
    python scripts/init_db.py            # 建库建表
    python scripts/init_db.py --seed     # 建表并写入演示基础数据
    python scripts/init_db.py --drop     # 先删表再建（危险，仅开发用）

默认使用 SQLite（无需安装数据库）；设置 DB_URL 环境变量可切到 MySQL。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"), override=False)


def create_database():
    """MySQL 需要先建库；SQLite 首次连接即自动创建文件。

    连接参数以 database 模块解析出的 DB_URL 为准（而非单独读 DB_* 变量），
    这样用户在 .env 里只写一行 DB_URL 也能正确建库。
    """
    from database import DB_URL, IS_SQLITE

    if IS_SQLITE:
        print("[ok] 使用 SQLite，无需单独建库（数据库文件首次连接时自动创建）")
        return

    import pymysql
    from sqlalchemy.engine import make_url

    url = make_url(DB_URL)
    name = url.database
    if not name:
        raise SystemExit("[错误] DB_URL 中缺少数据库名，例如 .../@127.0.0.1:3306/baijiao_aquaculture")
    conn = pymysql.connect(host=url.host or "127.0.0.1",
                           port=url.port or 3306,
                           user=url.username,
                           password=url.password or "",
                           charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{name}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
        print(f"[ok] database `{name}` ready")
    finally:
        conn.close()


def create_tables(drop=False):
    import models  # noqa: F401  确保模型已注册
    from database import Base, engine
    if drop:
        Base.metadata.drop_all(engine)
        print("[ok] tables dropped")
    Base.metadata.create_all(engine)
    print(f"[ok] {len(Base.metadata.tables)} tables created")
    return sorted(Base.metadata.tables)


def seed():
    """写入演示基础数据：用户、鱼塘、批次、设备、终端、配置。"""
    from database import SessionLocal
    from models import (Batch, Device, Pond, PondGrant, SysConfig, Terminal, User)
    from datetime import datetime

    db = SessionLocal()
    try:
        if db.query(User).count():
            print("[skip] 基础数据已存在，未重复写入")
            return

        owner = User(username="owner", display_name="李塘主", role="owner", phone="13800000001")
        admin = User(username="admin", display_name="张管理员", role="admin", phone="13800000002")
        op1 = User(username="wang", display_name="王师傅", role="operator", phone="13800000003")
        op2 = User(username="liu", display_name="刘师傅", role="operator", phone="13800000004")
        db.add_all([owner, admin, op1, op2]); db.flush()

        p1 = Pond(code="A01", name="A01 号塘", area=8.0, area_unit="亩", owner_id=owner.id,
                  location="白蕉镇东片")
        p2 = Pond(code="A02", name="A02 号塘", area=10.0, area_unit="亩", owner_id=owner.id,
                  location="白蕉镇东片")
        db.add_all([p1, p2]); db.flush()

        b1 = Batch(pond_id=p1.id, code="A01-2026春", species="海鲈",
                   stock_date=datetime(2026, 1, 10), fish_number=54000,
                   initial_weight=51.3, target_weight=800.0, weigh_cycle_days=30,
                   plan_harvest_date=datetime(2027, 1, 20), src_note="课堂模拟数据")
        b2 = Batch(pond_id=p2.id, code="A02-2026春", species="海鲈",
                   stock_date=datetime(2026, 2, 1), fish_number=62000,
                   initial_weight=50.0, target_weight=800.0, weigh_cycle_days=30,
                   plan_harvest_date=datetime(2027, 2, 10), src_note="课堂模拟数据")
        db.add_all([b1, b2]); db.flush()

        # 授权：owner/admin 全部；op1 管 A01 且可控制、可核查；op2 管 A02 不可核查
        for u in (owner, admin):
            for p in (p1, p2):
                db.add(PondGrant(user_id=u.id, pond_id=p.id, can_control=True, can_review=True))
        db.add(PondGrant(user_id=op1.id, pond_id=p1.id, can_control=True, can_review=True))
        db.add(PondGrant(user_id=op2.id, pond_id=p2.id, can_control=True, can_review=False))
        db.flush()

        # 设备
        devices = [
            Device(code="S-A01-TEMP", pond_id=p1.id, kind="sensor", metric="temperature", unit="℃"),
            Device(code="S-A01-OXY", pond_id=p1.id, kind="sensor", metric="oxygen", unit="mg/L"),
            Device(code="S-A01-PH", pond_id=p1.id, kind="sensor", metric="ph", unit=""),
            Device(code="CAM-A01-TOP", pond_id=p1.id, kind="camera", location="水上"),
            Device(code="CAM-A01-SUB", pond_id=p1.id, kind="camera", location="水下"),
            Device(code="FEED-A01", pond_id=p1.id, kind="feeder", unit="kg"),
            Device(code="S-A02-TEMP", pond_id=p2.id, kind="sensor", metric="temperature", unit="℃"),
            Device(code="FEED-A02", pond_id=p2.id, kind="feeder", unit="kg"),
        ]
        db.add_all(devices); db.flush()

        # 终端
        db.add(Terminal(code="TERM-A01", pond_id=p1.id, status="offline"))
        db.add(Terminal(code="TERM-A02", pond_id=p2.id, status="offline"))

        # 配置
        cfgs = [
            ("sample_interval_sec", "600", "秒", "环境采集周期"),
            ("feed_suggest_valid_min", "30", "分钟", "投喂建议有效期"),
            ("task_timeout_sec", "7", "秒", "任务回执超时（演示口径：等待回执超过该时长转待核查）"),
            ("max_feed_per_task", "1200", "kg", "单次投喂上限"),
            ("min_feed_per_task", "0.1", "kg", "单次投喂下限"),
            ("rule_version", "rule-v1.0", "", "当前投喂规则版本"),
            ("temp_valid_range", "0,40", "℃", "水温有效量程"),
            ("oxygen_valid_range", "0,20", "mg/L", "溶氧有效量程"),
            ("ph_valid_range", "0,14", "", "pH 有效量程"),
            ("measurement_expire_min", "180", "分钟", "环境数据有效期"),
        ]
        for k, v, u, n in cfgs:
            db.add(SysConfig(key=k, value=v, unit=u, note=n))

        db.commit()
        print("[ok] 演示基础数据已写入")
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="写入演示基础数据")
    ap.add_argument("--drop", action="store_true", help="先删表再建")
    ap.add_argument("--quiet", action="store_true", help="精简输出（供一键启动调用）")
    args = ap.parse_args()

    from database import db_kind
    if not args.quiet:
        print(f"数据库类型：{db_kind()}")

    create_database()
    tables = create_tables(drop=args.drop)
    if args.quiet:
        print(f"  · 已建立 {len(tables)} 张表")
    else:
        for name in tables:
            print("    -", name)
    if args.seed:
        seed()


if __name__ == "__main__":
    main()
