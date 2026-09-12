"""视觉监控、疑似死鱼事件与异常告警接口（需求说明书 5.5 / 5.6）。"""
from flask import Blueprint, g, request

from models import Alert, CameraView, Device, FishEvent
from services.common import (audit, can_view, fail, next_code, now, ok)
from services.serialize import row

bp = Blueprint("monitor", __name__)


# ---------------------------------------------------------------- 相机画面
@bp.get("/ponds/<int:pond_id>/cameras")
def list_cameras(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    rows = g.db.query(CameraView).filter_by(pond_id=pond_id).all()
    out = []
    for c in rows:
        d = row(c, ["id", "device_id", "position", "online", "last_frame_at", "image_path", "note"])
        if not c.online:
            d["display"] = "画面不可用"
            d["warn"] = "相机离线，不以旧截图表示正常监控"
        else:
            d["display"] = "在线"
        out.append(d)
    if not rows:
        return ok([], note="该鱼塘未接入相机")
    return ok(out)


# ---------------------------------------------------------------- 疑似死鱼事件
@bp.post("/ponds/<int:pond_id>/fish-events")
def create_event(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    body = request.get_json(silent=True) or {}
    ev = FishEvent(
        code=next_code(g.db, FishEvent, "code", "EV"),
        pond_id=pond_id, kind=body.get("kind", "suspected_death"),
        status="pending", detected_at=now(),
        position_desc=body.get("position_desc"),
        observation=body.get("observation"),
        image_path=body.get("image_path"),
        source=body.get("source", "manual"),
        model_version=body.get("model_version"),
        confidence=body.get("confidence"),
    )
    g.db.add(ev)
    audit(g.db, "control", "fish_event_create", pond_id=pond_id, user_id=g.user.id,
          detail={"code": ev.code, "source": ev.source})
    g.db.commit()
    return ok(row(ev, ["id", "code", "pond_id", "kind", "status", "detected_at",
                       "position_desc", "source", "model_version", "confidence"]))


@bp.get("/ponds/<int:pond_id>/fish-events")
def list_events(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    rows = (g.db.query(FishEvent).filter_by(pond_id=pond_id)
            .order_by(FishEvent.id.desc()).limit(50).all())
    return ok([row(x, ["id", "code", "kind", "status", "detected_at",
                       "position_desc", "source", "confidence",
                       "confirmed_at", "handler_note"]) for x in rows])


@bp.post("/fish-events/<code>/handle")
def handle_event(code):
    """确认 / 处理 / 标记误报。确认死鱼事件本身不等于确定病因。"""
    if not g.user:
        return fail("未提供有效身份", 401)
    ev = g.db.query(FishEvent).filter_by(code=code).first()
    if not ev:
        return fail("事件不存在", 404)
    if not can_view(g.db, g.user, ev.pond_id):
        return fail("无权访问该事件", 403)
    body = request.get_json(silent=True) or {}
    action = body.get("action")          # confirm / process / false_alarm
    if action == "confirm":
        ev.status = "processing"
        ev.confirmed_by = g.user.id
        ev.confirmed_at = now()
    elif action == "process":
        ev.status = "handled"
        ev.handler_note = body.get("note")
    elif action == "false_alarm":
        ev.status = "false_alarm"
        ev.handler_note = body.get("note", "误报")
    else:
        return fail("action 必须是 confirm/process/false_alarm")
    audit(g.db, "control", f"fish_event_{action}", pond_id=ev.pond_id,
          user_id=g.user.id, detail={"code": ev.code})
    g.db.commit()
    return ok(row(ev, ["id", "code", "status", "confirmed_at", "handler_note"]))


# ---------------------------------------------------------------- 告警
@bp.get("/alerts")
def list_alerts():
    if not g.user:
        return fail("未提供有效身份", 401)
    status = request.args.get("status", "open")
    q = g.db.query(Alert)
    if status != "all":
        q = q.filter(Alert.status == status)
    rows = [a for a in q.order_by(Alert.id.desc()).limit(200).all()
            if a.pond_id is None or can_view(g.db, g.user, a.pond_id)]
    return ok([row(a, ["id", "code", "pond_id", "device_id", "task_id", "kind",
                       "level", "status", "message", "is_ongoing",
                       "occurrence_count", "created_at"]) for a in rows[:80]])


@bp.post("/alerts/<int:alert_id>/handle")
def handle_alert(alert_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    a = g.db.query(Alert).filter_by(id=alert_id).first()
    if not a:
        return fail("告警不存在", 404)
    body = request.get_json(silent=True) or {}
    a.status = body.get("status", "closed")
    a.handled_by = g.user.id
    a.handled_at = now()
    a.resolution = body.get("resolution")
    audit(g.db, "control", "alert_handle", pond_id=a.pond_id, user_id=g.user.id,
          detail={"alert": a.id, "status": a.status})
    g.db.commit()
    return ok(row(a, ["id", "status", "handled_at", "resolution"]))


# ---------------------------------------------------------------- 演示：模拟识别服务
@bp.post("/ponds/<int:pond_id>/vision/detect")
def mock_detect(pond_id):
    """演示用视觉识别接口：输出疑似对象与置信度，作为待确认事件。

    未接入真实相机时，以样例图与模拟结果演示识别流程，来源标记为 sim。
    """
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    body = request.get_json(silent=True) or {}
    hit = bool(body.get("detected", False))
    if not hit:
        return ok({"detected": False, "note": "未检测到疑似对象",
                   "source": "sim", "model_version": "vision-sim-v1"})
    ev = FishEvent(
        code=next_code(g.db, FishEvent, "code", "EV"),
        pond_id=pond_id, kind="suspected_death", status="pending",
        detected_at=now(), position_desc=body.get("position_desc", "水面东北角"),
        observation="识别到疑似死鱼对象",
        image_path=body.get("image_path", "static/samples/sample_death.jpg"),
        source="vision", model_version="vision-sim-v1",
        confidence=float(body.get("confidence", 0.83)),
    )
    g.db.add(ev)
    g.db.commit()
    return ok({"detected": True, "event": row(ev, ["id", "code", "status",
                                                   "confidence", "model_version", "source"]),
               "note": "识别结果作为待确认事件，人工确认前不计入已确认数量"})
