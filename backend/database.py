"""数据库连接与会话管理。

默认使用 SQLite（单文件，拷贝即可运行，无需安装数据库）；
通过 DB_URL 环境变量可切回 MySQL（课程要求口径）。

    # 默认（SQLite，文件位于 data/baijiao.db，相对项目根目录）
    python backend/app.py

    # 切回 MySQL
    set DB_URL=mysql+pymysql://root:password@127.0.0.1:3306/baijiao_aquaculture?charset=utf8mb4
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 先读项目根目录的 .env（不覆盖已存在的环境变量，便于临时切换）
load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)

DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# SQLite 文件放在 data/ 下；用相对项目根目录的路径，保证整包拷贝后仍可用
SQLITE_PATH = os.path.join(DATA_DIR, "baijiao.db")


def _sqlite_url() -> str:
    # SQLAlchemy 的 sqlite URL 用正斜杠
    return "sqlite:///" + SQLITE_PATH.replace("\\", "/")


def _mysql_url() -> str:
    user = os.getenv("DB_USER", "root")
    pwd = os.getenv("DB_PASSWORD", "")
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    name = os.getenv("DB_NAME", "baijiao_aquaculture")
    return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{name}?charset=utf8mb4"


def _env_flag(name: str, default: bool = False) -> bool:
    """把 '0'/'false'/'' 正确解析为 False（bool('0') 会误判为 True）。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


DB_URL = os.getenv("DB_URL") or _sqlite_url()
IS_SQLITE = DB_URL.startswith("sqlite")

# SQLite 需要跨线程共享连接（Flask 多线程）；MySQL 则不传该参数
_connect_args = {"check_same_thread": False} if IS_SQLITE else {}

engine = create_engine(
    DB_URL,
    echo=_env_flag("DB_ECHO"),
    connect_args=_connect_args,
    pool_pre_ping=True,          # 对 SQLite 无副作用；对 MySQL 防连接超时
    pool_recycle=3600,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def get_db():
    """会话生成器（依赖注入用）。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_kind():
    """返回当前数据库类型，供启动横幅与自检脚本显示。"""
    return "SQLite" if IS_SQLITE else "MySQL"
