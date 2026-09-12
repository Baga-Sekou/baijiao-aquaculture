"""通用工具：编号生成、配置读取、审计日志、角色与权限校验。"""
import json
from datetime import datetime, timedelta

from flask import jsonify
from sqlalchemy import func

from models import AuditLog, Pond, PondGrant, SysConfig, Terminal, User


# ---------------------------------------------------------------- 时间
def now():
    return datetime.now()


# ---------------------------------------------------------------- 编号
def next_code(db, model, field, prefix, width=4):
    """生成形如 PREFIX-0001 的顺序编号。

    注意：用 count() 只能看到已 flush 的行，同一事务内连续新建多条时
    会重复；因此同时检查会话中尚未落库的对象。
    """
    col = getattr(model, field)
    n = db.query(func.count(model.id)).scalar() or 0
    # 会话中已 add 但可能尚未 flush 的对象也算数
    try:
        n += sum(1 for o in db.new if isinstance(o, model))
    except Exception:
        pass
    while True:
        n += 1
        code = f"{prefix}-{n:0{width}d}"
        if db.query(model.id).filter(col == code).first():
            continue
        if any(getattr(o, field, None) == code for o in db.new if isinstance(o, model)):
            continue
        return code


# ---------------------------------------------------------------- 配置
def cfg(db, key, default=None):
    row = db.query(SysConfig).filter_by(key=key).first()
    return row.value if row else default


def cfg_float(db, key, default=None):
    v = cfg(db, key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def cfg_range(db, key, default=(0, 9999)):
    v = cfg(db, key)
    try:
        lo, hi = v.split(",")
        return float(lo), float(hi)
    except (AttributeError, ValueError):
        return default


# ---------------------------------------------------------------- 审计
def audit(db, category, action, **kw):
    """写审计日志。detail 不记录明文密码等认证信息。"""
    detail = kw.pop("detail", None)
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False)
    db.add(AuditLog(category=category, action=action, detail=detail, **kw))


# ---------------------------------------------------------------- 响应
def ok(data=None, **extra):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return jsonify(payload)


def fail(message, code=400, reason=None, **extra):
    payload = {"ok": False, "message": message}
    if reason:
        payload["reason"] = reason
    payload.update(extra)
    return jsonify(payload), code


# ---------------------------------------------------------------- 权限
ROLE_RANK = {"operator": 1, "admin": 2, "owner": 3}


def current_user(db, username):
    if not username:
        return None
    return db.query(User).filter_by(username=username).first()


def grant_for(db, user, pond_id):
    if not user:
        return None
    return db.query(PondGrant).filter_by(user_id=user.id, pond_id=pond_id).first()


def can_view(db, user, pond_id):
    """塘主/管理员可见全部；养殖员仅限被分配鱼塘。"""
    if not user:
        return False
    if user.role in ("owner", "admin"):
        return True
    return grant_for(db, user, pond_id) is not None


def can_control(db, user, pond_id):
    """是否有权下发投喂。"""
    if not user:
        return False
    if user.role in ("owner", "admin"):
        return True
    g = grant_for(db, user, pond_id)
    return bool(g and g.can_control)


def can_review(db, user, pond_id):
    """是否有权做投喂核查（核查结论由具备该权限者确认）。"""
    if not user:
        return False
    if user.role in ("owner", "admin"):
        return True
    g = grant_for(db, user, pond_id)
    return bool(g and g.can_review)


# ---------------------------------------------------------------- 序列化
def iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def parse_dt(value, default=None):
    """把请求里的时间字段解析成 datetime。

    SQLite 的 DateTime 列只接受 datetime 对象（MySQL 会宽松地接受字符串），
    因此所有来自请求体的时间字段都必须先经过这里，否则换库后会报错。
    """
    if value is None or value == "":
        return default
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        s = value.strip().replace("Z", "").replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return default
    return default


def pond_brief(p):
    return {"id": p.id, "code": p.code, "name": p.name,
            "area": p.area, "area_unit": p.area_unit, "location": p.location}


def terminal_brief(t):
    return {"id": t.id, "code": t.code, "pond_id": t.pond_id,
            "status": t.status, "last_seen": iso(t.last_seen)}
