"""环境数据与摄食反馈接口（需求说明书 5.2）。"""
from datetime import datetime

from flask import Blueprint, g, request

from models import FeedingFeedback, Terminal
from services import environment as env
from services.common import can_view, fail, now, ok, terminal_brief
from services.serialize import row

bp = Blueprint("env", __name__)


@bp.post("/measurements")
def ingest_measurement():
    """采集端上传一条环境测量。无需登录身份（终端持终端编号）。"""
    body = request.get_json(silent=True) or {}
    m, err = env.ingest(g.db, body)
    if err:
        return fail(err, 400)
    return ok(env.serial(m), valid=m.valid, invalid_reason=m.invalid_reason)


@bp.post("/measurements/batch")
def ingest_batch():
    """批量上传（断线恢复后补传）。返回逐条结果。"""
    body = request.get_json(silent=True) or {}
    items = body.get("items") or []
    results, n_ok, n_bad = [], 0, 0
    for it in items:
        m, err = env.ingest(g.db, it)
        if err:
            n_bad += 1
            results.append({"ok": False, "error": err, "payload": it})
        else:
            n_ok += 1
            results.append({"ok": True, "id": m.id, "valid": m.valid})
    return ok(results, total=len(items), accepted=n_ok, rejected=n_bad)


@bp.get("/ponds/<int:pond_id>/env/latest")
def latest_all(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    out = {}
    for metric in ("temperature", "oxygen", "ph", "orp", "salinity"):
        m = env.latest(g.db, pond_id, metric)
        if not m:
            out[metric] = {"connected": False, "value": None,
                           "note": "未接入"}
            continue
        d = env.serial(m)
        d["expired"] = env.is_expired(g.db, m)
        d["connected"] = True
        out[metric] = d
    return ok(out)


@bp.get("/ponds/<int:pond_id>/env/history")
def history(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    metric = request.args.get("metric", "temperature")
    start = request.args.get("start")
    end = request.args.get("end")
    def _p(s):
        try:
            return datetime.fromisoformat(s.replace("T", " "))
        except (ValueError, AttributeError):
            return None
    rows = env.history(g.db, pond_id, metric, _p(start), _p(end))
    return ok([env.serial(m) for m in rows])


@bp.get("/ponds/<int:pond_id>/env/day")
def day_view(pond_id):
    """某日日内曲线 + 统计特征（缺测/异常分别标记，不补零）。"""
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    metric = request.args.get("metric", "temperature")
    day_s = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    try:
        day = datetime.strptime(day_s, "%Y-%m-%d").date()
    except ValueError:
        return fail("date 格式应为 YYYY-MM-DD")
    rows = env.day_curve(g.db, pond_id, metric, day)
    feat = env.day_features(g.db, pond_id, metric, day)
    return ok({
        "series": [env.serial(m) for m in rows],
        "features": feat,
        "gaps_marked": True,
        "note": "缺测区间以空缺表示，异常值单独标记，未补零",
    })


# ---------------------------------------------------------------- 摄食反馈
@bp.post("/ponds/<int:pond_id>/feedback")
def add_feedback(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    body = request.get_json(silent=True) or {}
    state = body.get("state")
    if state not in ("hungry", "normal", "full", "unknown"):
        return fail("state 必须是 hungry/normal/full/unknown")
    fb = FeedingFeedback(pond_id=pond_id, batch_id=body.get("batch_id"),
                         round_no=body.get("round_no"), state=state,
                         source=body.get("source", "manual"),
                         valid=True, observed_at=now())
    g.db.add(fb)
    g.db.commit()
    return ok(row(fb, ["id", "pond_id", "state", "source", "observed_at", "valid"]))


@bp.get("/ponds/<int:pond_id>/feedback")
def list_feedback(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    rows = (g.db.query(FeedingFeedback).filter_by(pond_id=pond_id)
            .order_by(FeedingFeedback.observed_at.desc()).limit(50).all())
    return ok([row(x, ["id", "state", "source", "observed_at", "valid"]) for x in rows])


# ---------------------------------------------------------------- 终端注册
@bp.post("/terminals/register")
def register_terminal():
    """终端接入登记身份、关联鱼塘及设备；重复连接更新同一终端状态。"""
    body = request.get_json(silent=True) or {}
    code = (body.get("code") or "").strip()
    if not code:
        return fail("缺少终端编号 code")
    t = g.db.query(Terminal).filter_by(code=code).first()
    if not t:
        t = Terminal(code=code, pond_id=body.get("pond_id"),
                     ip=body.get("ip"))
        g.db.add(t)
    else:
        if body.get("pond_id"):
            t.pond_id = body["pond_id"]
        t.ip = body.get("ip") or t.ip
    t.status = "online"
    t.connected_at = t.connected_at or now()
    t.last_seen = now()
    g.db.commit()
    return ok(terminal_brief(t), devices=body.get("devices") or [])


@bp.post("/terminals/<code>/heartbeat")
def heartbeat(code):
    t = g.db.query(Terminal).filter_by(code=code).first()
    if not t:
        return fail("终端未注册", 404)
    t.status = "online"
    t.last_seen = now()
    g.db.commit()
    return ok(terminal_brief(t))


@bp.post("/terminals/<code>/offline")
def set_offline(code):
    t = g.db.query(Terminal).filter_by(code=code).first()
    if not t:
        return fail("终端未注册", 404)
    t.status = "offline"
    g.db.commit()
    return ok(terminal_brief(t))


@bp.get("/terminals")
def list_terminals():
    rows = g.db.query(Terminal).order_by(Terminal.code).all()
    return ok([terminal_brief(t) for t in rows])
