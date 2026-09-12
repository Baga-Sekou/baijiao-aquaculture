"""视觉监控、疑似死鱼事件与异常告警接口（需求说明书 5.5 / 5.6）。"""
import base64
import os

from flask import Blueprint, g, request

from models import Alert, CameraView, Device, FishEvent
from services import vision
from services.common import (audit, can_view, fail, next_code, now, ok)
from services.serialize import row

bp = Blueprint("monitor", __name__)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    """视觉识别接口：优先对已有图片做真实分析；无图时按仿真流程演示。

    未接入真实相机时，以样例图与模拟结果演示识别流程，来源标记为 sim；
    提供 image_path（磁盘上已存在的图片）时走真实算法（cv-float-v1）。
    """
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    body = request.get_json(silent=True) or {}

    image_path = body.get("image_path")
    disk_path = os.path.join(BACKEND_DIR, image_path) if image_path else None
    if image_path and os.path.exists(disk_path):
        result = vision.analyze_frame(disk_path)
        if not result.get("ok"):
            return fail(result.get("error", "帧分析失败"), 400)
        ev = None
        if result["suspected"]:
            ev = _create_vision_event(g.db, pond_id, image_path,
                                      result["confidence"], result["model_version"])
        audit(g.db, "control", "vision_analyze", pond_id=pond_id, user_id=g.user.id,
              detail={"image": image_path, "suspected": result["suspected"],
                      "model_version": result["model_version"]})
        g.db.commit()
        return ok(result, event=row(ev, ["id", "code", "status", "confidence",
                                         "model_version", "source"]) if ev else None,
                  note="识别结果作为待确认事件，人工确认前不计入已确认数量")

    hit = bool(body.get("detected", False))
    if not hit:
        return ok({"detected": False, "note": "未检测到疑似对象",
                   "source": "sim", "model_version": "vision-sim-v1"})
    ev = FishEvent(
        code=next_code(g.db, FishEvent, "code", "EV"),
        pond_id=pond_id, kind="suspected_death", status="pending",
        detected_at=now(), position_desc=body.get("position_desc", "水面东北角"),
        observation="识别到疑似死鱼对象（仿真流程演示）",
        image_path=body.get("image_path", "static/samples/sample_death.jpg"),
        source="vision", model_version="vision-sim-v1",
        confidence=float(body.get("confidence", 0.83)),
    )
    g.db.add(ev)
    g.db.commit()
    return ok({"detected": True, "source": "sim", "event": row(ev, ["id", "code", "status",
                                                   "confidence", "model_version", "source"]),
               "note": "仿真流程演示；接入相机后由 /vision/frame 上传帧做真实分析"})


# ---------------------------------------------------------------- 相机帧上传与真实识别
def _camera_device(db, pond_id, position="水上"):
    d = db.query(Device).filter_by(pond_id=pond_id, kind="camera").first()
    if not d:
        d = Device(code=next_code(db, Device, "code", "CAM", width=3),
                   pond_id=pond_id, kind="camera", location=position)
        db.add(d)
        db.flush()
    return d


def _create_vision_event(db, pond_id, image_path, confidence, model_version):
    ev = FishEvent(
        code=next_code(db, FishEvent, "code", "EV"),
        pond_id=pond_id, kind="suspected_death", status="pending",
        detected_at=now(), position_desc="相机画面识别",
        observation="算法检测到疑似漂浮对象，待人工确认",
        image_path=image_path, source="vision",
        model_version=model_version, confidence=confidence,
    )
    db.add(ev)
    return ev


@bp.post("/ponds/<int:pond_id>/vision/frame")
def upload_frame(pond_id):
    """上传相机帧（multipart 文件或 JSON base64），保存并做真实分析。

    - 更新视觉通道状态（在线、最近帧时间、图片路径）；
    - 运行 cv-float-v1 帧分析（质量 + 漂浮物）；
    - 检出疑似对象时生成待确认事件（source=vision），人工确认前不计数。
    """
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)

    position = "水上"
    data = None
    ext = "jpg"
    if request.files:
        f = next(iter(request.files.values()))
        position = request.form.get("position", position)
        data = f.read()
        ext = (f.filename.rsplit(".", 1)[-1] or "jpg").lower()[:4]
    else:
        body = request.get_json(silent=True) or {}
        b64 = body.get("image_base64")
        if not b64:
            return fail("缺少图片：multipart file 字段或 JSON image_base64")
        position = body.get("position", position)
        try:
            data = base64.b64decode(b64.split(",")[-1])
        except Exception:
            return fail("image_base64 解码失败")
    if not data:
        return fail("图片内容为空")
    if ext not in ("jpg", "jpeg", "png"):
        ext = "jpg"

    image_path = vision.save_frame(pond_id, position, data, ext=ext)
    disk_path = os.path.join(BACKEND_DIR, image_path)
    device = _camera_device(g.db, pond_id, position)
    view = (g.db.query(CameraView).filter_by(pond_id=pond_id, device_id=device.id).first())
    if not view:
        view = CameraView(device_id=device.id, pond_id=pond_id, position=position)
        g.db.add(view)
    view.online = True
    view.last_frame_at = now()
    view.image_path = image_path
    view.note = f"最近帧来源：浏览器/终端上传（{position}）"

    result = vision.analyze_frame(disk_path)
    ev = None
    if result.get("ok") and result.get("suspected"):
        ev = _create_vision_event(g.db, pond_id, image_path,
                                  result["confidence"], result["model_version"])
    audit(g.db, "control", "vision_frame", pond_id=pond_id, device_id=device.id,
          user_id=g.user.id,
          detail={"image": image_path, "suspected": result.get("suspected"),
                  "quality": result.get("quality")})
    g.db.commit()
    resp = result if result.get("ok") else {"ok": False, "error": result.get("error")}
    resp.update({
        "image_path": image_path,
        "camera": {"device_id": device.id, "position": position,
                   "online": True, "last_frame_at": view.last_frame_at.strftime("%Y-%m-%d %H:%M:%S")},
    })
    if ev:
        resp["event"] = row(ev, ["id", "code", "status", "confidence",
                                 "model_version", "source"])
    return ok(resp, note="识别结果作为待确认事件，人工确认前不计入已确认数量")


@bp.post("/ponds/<int:pond_id>/vision/ocr")
def ocr(pond_id):
    """图片文字识别（称重单 / 饲料袋标签）。依赖未安装时明确说明，不做假识别。"""
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    if request.files:
        data = next(iter(request.files.values())).read()
    else:
        body = request.get_json(silent=True) or {}
        b64 = body.get("image_base64")
        if not b64:
            return fail("缺少图片：multipart file 字段或 JSON image_base64")
        try:
            data = base64.b64decode(b64.split(",")[-1])
        except Exception:
            return fail("image_base64 解码失败")
    image_path = vision.save_frame(pond_id, "ocr", data)
    disk_path = os.path.join(BACKEND_DIR, image_path)
    text, err = vision.ocr_text(disk_path)
    audit(g.db, "control", "vision_ocr", pond_id=pond_id, user_id=g.user.id,
          detail={"image": image_path, "ok": text is not None})
    g.db.commit()
    if err:
        return fail(err, 200, reason="ocr_unavailable")
    return ok({"text": text, "image_path": image_path})
