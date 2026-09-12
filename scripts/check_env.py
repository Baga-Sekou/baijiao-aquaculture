"""环境自检：确认本机能否正常运行本平台。

用法：
    python scripts/check_env.py

检查项：Python 版本、依赖包、数据库类型与连通性、表数量、基础数据、端口占用。
"""
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"), override=False)

OK, FAIL, WARN = "[通过]", "[失败]", "[警告]"
problems = []


def line(mark, text):
    print(f"  {mark} {text}")


def check_python():
    v = sys.version_info
    if v >= (3, 10):
        line(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        line(FAIL, f"Python {v.major}.{v.minor} 过低，需要 3.10+")
        problems.append("Python 版本过低")


def check_deps():
    need = {
        "flask": "Flask", "sqlalchemy": "SQLAlchemy",
        "dotenv": "python-dotenv", "pymysql": "PyMySQL", "requests": "requests",
    }
    missing = []
    for mod, pkg in need.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        line(FAIL, "缺少依赖：" + "、".join(missing))
        line(WARN, "修复：pip install -r requirements.txt")
        problems.append("缺少 Python 依赖")
    else:
        line(OK, f"依赖齐全（{len(need)} 个）")


def check_db():
    try:
        from database import DB_URL, IS_SQLITE, engine, db_kind
        import models  # noqa: F401
        from sqlalchemy import inspect, text
    except Exception as e:
        line(FAIL, f"无法导入数据库模块：{e}")
        problems.append("数据库模块导入失败")
        return

    line(OK, f"数据库类型：{db_kind()}")
    if IS_SQLITE:
        path = DB_URL.replace("sqlite:///", "")
        line(OK if os.path.exists(path) else WARN,
             f"数据库文件：{path}" + ("" if os.path.exists(path) else "（尚未创建，首次启动会自动建立）"))

    try:
        insp = inspect(engine)
        tables = insp.get_table_names()
    except Exception as e:
        line(FAIL, f"数据库连接失败：{e}")
        if not IS_SQLITE:
            line(WARN, "请确认 MySQL 已启动，且 .env 中的 DB_URL / 密码正确")
        problems.append("数据库连接失败")
        return

    if not tables:
        line(WARN, "数据库中还没有表（首次启动或运行 scripts/init_db.py --seed 会自动创建）")
        return
    line(OK, f"已建立 {len(tables)} 张表")

    try:
        from database import SessionLocal
        from models import Pond, User
        db = SessionLocal()
        n_user, n_pond = db.query(User).count(), db.query(Pond).count()
        db.close()
        if n_user and n_pond:
            line(OK, f"基础数据：{n_user} 个用户、{n_pond} 个鱼塘")
        else:
            line(WARN, "基础数据为空，运行 scripts/init_db.py --seed 可写入")
    except Exception as e:
        line(WARN, f"读取基础数据失败：{e}")


def check_port():
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5000"))
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect((probe_host, port))
        line(WARN, f"端口 {port} 已被占用（服务可能已在运行）")
    except Exception:
        line(OK, f"端口 {port} 可用")
    finally:
        s.close()


def main():
    print("=" * 60)
    print("  白蕉水产养殖管理平台 · 环境自检")
    print("=" * 60)
    print("\n[1] Python 版本")
    check_python()
    print("\n[2] Python 依赖")
    check_deps()
    print("\n[3] 数据库")
    check_db()
    print("\n[4] 服务端口")
    check_port()

    print("\n" + "=" * 60)
    if problems:
        print("  自检未通过，请先解决：")
        for p in problems:
            print("    -", p)
        return 1
    print("  自检通过，可执行以下命令启动：")
    print("    Windows：双击 start.bat")
    print("    或命令行：python backend/app.py")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
