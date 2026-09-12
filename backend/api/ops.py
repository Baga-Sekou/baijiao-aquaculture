"""Web 看板 / 多塘比较 / 运营记录 / 审计日志 / 配置接口（需求说明书 5.7 / 5.9）。"""
from datetime import datetime, timedelta

from flask import Blueprint, g, request
from sqlalchemy import func, or_

from models import (Alert, AuditLog, Batch, CostRecord, FeedingTask, Measurement,
                    MedicineRecord, PlanTask, Pond, SysConfig, Terminal, User,
                    WeighRecord)
from services.common import (audit, can_view, fail, now, ok, pond_brief)
from services.serialize import row

bp = Blueprint("ops", __name__)


# ---------------------------------------------------------------- 单塘首页
@bp.get("/ponds/<int:pond_id>/dashboard")
def dashboard(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    from services import environment as env

    p = g.db.query(Pond).filter_by(id=pond_id).first()
    b = (g.db.query(Batch).filter_by(pond_id=pond_id, status="active")
         .order_by(Batch.id.desc()).first())

    metrics = {}
    for metric in ("temperature", "oxygen", "ph", "orp"):
        m = env.latest(g.db, pond_id, metric)
        if m:
            d = env.serial(m)
            d["expired"] = env.is_expired(g.db, m)
            d["connected"] = True
            metrics[metric] = d
        else:
            metrics[metric] = {"connected": False, "note": "未接入"}

    today = datetime.now().date()
    start = datetime.combine(today, datetime.min.time())
    tasks_today = (g.db.query(FeedingTask)
                   .filter(FeedingTask.pond_id == pond_id,
                           FeedingTask.created_at >= start).all())
    fed_today = sum(t.confirm_amount or 0 for t in tasks_today
                    if t.status in ("done", "stopped", "running", "dispatched"))

    alerts = (g.db.query(Alert)
              .filter(Alert.pond_id == pond_id, Alert.status.in_(["open", "processing"]))
              .order_by(Alert.id.desc()).limit(10).all())

    return ok({
        "pond": pond_brief(p),
        "batch": row(b, ["id", "code", "species", "fish_number", "initial_weight",
                         "target_weight", "stock_date", "plan_harvest_date", "status"]) if b else None,
        "env": metrics,
        "today": {
            "tasks": len(tasks_today),
            "confirmed_feed_kg": round(fed_today, 2),
            "note": "已确认投喂量按确认量累计；实际量未知的单独标明",
        },
        "pending_alerts": [row(a, ["id", "kind", "level", "status", "message",
                                   "is_ongoing", "occurrence_count", "created_at"])
                           for a in alerts],
        "online_users": _online_users(g.db),
    })


def _online_users(db, minutes=30):
    """在线人数与终端连接数分别统计。"""
    since = now() - timedelta(minutes=minutes)
    active_sessions = (db.query(AuditLog)
                       .filter(AuditLog.created_at >= since, AuditLog.user_id.isnot(None))
                       .with_entities(AuditLog.user_id).distinct().count())
    terminals = db.query(Terminal).filter_by(status="online").count()
    return {"users": active_sessions, "terminals_online": terminals,
            "window_min": minutes,
            "note": "用户按近窗口内产生操作的用户去重；终端按连接状态计数"}


# ---------------------------------------------------------------- 多塘比较
@bp.get("/compare")
def compare():
    if not g.user:
        return fail("未提供有效身份", 401)
    days = int(request.args.get("days", 7))
    since = now() - timedelta(days=days)
    out = []
    for p in g.db.query(Pond).order_by(Pond.code).all():
        if not can_view(g.db, g.user, p.id):
            continue
        b = (g.db.query(Batch).filter_by(pond_id=p.id, status="active")
             .order_by(Batch.id.desc()).first())
        temp = g.db.query(func.avg(Measurement.value)).filter(
            Measurement.pond_id == p.id, Measurement.metric == "temperature",
            Measurement.valid.is_(True), Measurement.collected_at >= since).scalar()
        oxy = g.db.query(func.avg(Measurement.value)).filter(
            Measurement.pond_id == p.id, Measurement.metric == "oxygen",
            Measurement.valid.is_(True), Measurement.collected_at >= since).scalar()
        feed = g.db.query(func.sum(FeedingTask.confirm_amount)).filter(
            FeedingTask.pond_id == p.id, FeedingTask.created_at >= since).scalar()
        alerts = g.db.query(func.count(Alert.id)).filter(
            Alert.pond_id == p.id, Alert.created_at >= since).scalar()
        meds = g.db.query(func.count(MedicineRecord.id)).filter(
            MedicineRecord.pond_id == p.id, MedicineRecord.used_at >= since).scalar()
        out.append({
            "pond": pond_brief(p),
            "batch_code": b.code if b else None,
            "species": b.species if b else None,
            "stage_weight_g": b.initial_weight if b else None,
            "area": p.area, "area_unit": p.area_unit,
            "avg_temperature": round(temp, 2) if temp else None,
            "avg_oxygen": round(oxy, 2) if oxy else None,
            "feed_kg": round(feed, 2) if feed else 0,
            "alerts": alerts or 0,
            "medicine_count": meds or 0,
            "sample_note": "数据条件不同的鱼塘不直接据此判定优劣",
        })
    return ok(out, window_days=days)


# ---------------------------------------------------------------- 趋势图数据
@bp.get("/ponds/<int:pond_id>/charts")
def charts(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    days = int(request.args.get("days", 30))
    since = now() - timedelta(days=days)

    def series(metric):
        rows = (g.db.query(Measurement)
                .filter(Measurement.pond_id == pond_id, Measurement.metric == metric,
                        Measurement.collected_at >= since)
                .order_by(Measurement.collected_at).all())
        return [{"t": m.collected_at.strftime("%Y-%m-%d"), "v": m.value,
                 "unit": m.unit, "valid": m.valid, "source": m.source} for m in rows]

    feed_rows = (g.db.query(FeedingTask)
                 .filter(FeedingTask.pond_id == pond_id,
                         FeedingTask.created_at >= since)
                 .order_by(FeedingTask.created_at).all())
    return ok({
        "temperature": series("temperature"),
        "oxygen": series("oxygen"),
        "ph": series("ph"),
        "feed": [{"t": f.created_at.strftime("%Y-%m-%d"),
                  "suggested": f.suggested_amount, "confirmed": f.confirm_amount,
                  "actual": f.actual_amount, "unit": f.unit, "status": f.status}
                 for f in feed_rows],
        "note": "未接入指标显示未接入，不填示例值；缺测不补零",
    })


# ---------------------------------------------------------------- 养殖计划
@bp.post("/ponds/<int:pond_id>/plans")
def create_plan(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    body = request.get_json(silent=True) or {}
    if not body.get("content"):
        return fail("缺少任务内容 content")
    pt = PlanTask(pond_id=pond_id, cycle=body.get("cycle", "daily"),
                  content=body["content"],
                  assignee_id=body.get("assignee_id"),
                  plan_time=_parse(body.get("plan_time")),
                  due_time=_parse(body.get("due_time")))
    g.db.add(pt)
    g.db.commit()
    return ok(row(pt, ["id", "cycle", "content", "assignee_id", "plan_time",
                       "due_time", "completed", "overdue"]))


def _parse(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("T", " "))
    except ValueError:
        return None


@bp.get("/ponds/<int:pond_id>/plans")
def list_plans(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    rows = (g.db.query(PlanTask).filter_by(pond_id=pond_id)
            .order_by(PlanTask.id.desc()).limit(50).all())
    for r in rows:
        if r.due_time and not r.completed and r.due_time < now():
            r.overdue = True
    g.db.commit()
    return ok([row(r, ["id", "cycle", "content", "assignee_id", "plan_time",
                       "due_time", "completed", "completed_at", "overdue"]) for r in rows])


@bp.post("/plans/<int:plan_id>/complete")
def complete_plan(plan_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    pt = g.db.query(PlanTask).filter_by(id=plan_id).first()
    if not pt:
        return fail("计划不存在", 404)
    if not can_view(g.db, g.user, pt.pond_id):
        return fail("无权访问该计划", 403)
    pt.completed = True
    pt.completed_at = now()
    pt.overdue = bool(pt.due_time and pt.due_time < now())
    g.db.commit()
    return ok(row(pt, ["id", "completed", "completed_at", "overdue"]),
              note="手动标记完成不替代投料机执行回执")


# ---------------------------------------------------------------- 称重 / 用药 / 成本
@bp.post("/ponds/<int:pond_id>/weigh")
def add_weigh(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    body = request.get_json(silent=True) or {}
    w = WeighRecord(pond_id=pond_id, batch_id=body.get("batch_id"),
                    weighed_at=_parse(body.get("weighed_at")) or now(),
                    avg_weight=body.get("avg_weight"),
                    sample_count=body.get("sample_count"),
                    operator_id=g.user.id, note=body.get("note"))
    g.db.add(w)
    g.db.commit()
    return ok(row(w, ["id", "weighed_at", "avg_weight", "sample_count"]))


@bp.post("/ponds/<int:pond_id>/medicine")
def add_medicine(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    body = request.get_json(silent=True) or {}
    if not body.get("item"):
        return fail("缺少用品名称 item")
    m = MedicineRecord(pond_id=pond_id, batch_id=body.get("batch_id"),
                       used_at=_parse(body.get("used_at")) or now(),
                       item=body["item"], amount=body.get("amount"),
                       unit=body.get("unit"), operator_id=g.user.id,
                       note=body.get("note"))
    g.db.add(m)
    g.db.commit()
    return ok(row(m, ["id", "used_at", "item", "amount", "unit"]),
              note="仅记录实际操作，不自动生成诊断或剂量建议")


@bp.get("/ponds/<int:pond_id>/medicine")
def list_medicine(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    rows = (g.db.query(MedicineRecord).filter_by(pond_id=pond_id)
            .order_by(MedicineRecord.used_at.desc()).limit(100).all())
    return ok([row(x, ["id", "used_at", "item", "amount", "unit", "note"]) for x in rows])


@bp.post("/ponds/<int:pond_id>/costs")
def add_cost(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    body = request.get_json(silent=True) or {}
    c = CostRecord(pond_id=pond_id, batch_id=body.get("batch_id"),
                   kind=body.get("kind", "actual"), item=body.get("item"),
                   amount=body.get("amount"), unit=body.get("unit"),
                   brand=body.get("brand"))
    g.db.add(c)
    g.db.commit()
    return ok(row(c, ["id", "kind", "item", "amount", "unit", "brand"]))


# ---------------------------------------------------------------- 饲料系数 / 饲料统计
@bp.get("/ponds/<int:pond_id>/feed-stats")
def feed_stats(pond_id):
    """饲料统计与饲料系数。缺少有效增重数据时显示暂不能计算，不虚报。"""
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    days = int(request.args.get("days", 30))
    since = now() - timedelta(days=days)
    today = datetime.now().date()
    t0 = datetime.combine(today, datetime.min.time())

    def feed_sum(start):
        v = (g.db.query(func.sum(FeedingTask.confirm_amount))
             .filter(FeedingTask.pond_id == pond_id, FeedingTask.created_at >= start)
             .scalar())
        return round(v, 2) if v else 0.0

    feed_today = feed_sum(t0)
    feed_window = feed_sum(since)

    # 饲料系数：需有效增重（两次称重）；只取本批次（含未归属批次但投苗之后），
    # 避免把上一批（如 CSV 历史数据）的体重接进同一条增重曲线
    batch = (g.db.query(Batch).filter_by(pond_id=pond_id, status="active")
             .order_by(Batch.id.desc()).first())
    weighs_q = (g.db.query(WeighRecord).filter_by(pond_id=pond_id))
    if batch is not None:
        weighs_q = weighs_q.filter(or_(WeighRecord.batch_id == batch.id,
                                       WeighRecord.batch_id.is_(None)))
        if batch.stock_date:
            weighs_q = weighs_q.filter(WeighRecord.weighed_at >= batch.stock_date)
    weighs = weighs_q.order_by(WeighRecord.weighed_at).all()
    fcr, fcr_note = None, None
    if len(weighs) >= 2:
        n = batch.fish_number if batch and batch.fish_number else None
        first, last = weighs[0], weighs[-1]
        if n and first.avg_weight and last.avg_weight:
            gain_kg = (last.avg_weight - first.avg_weight) * n / 1000.0
            consumed = (g.db.query(func.sum(FeedingTask.confirm_amount))
                        .filter(FeedingTask.pond_id == pond_id,
                                FeedingTask.created_at >= first.weighed_at)
                        .scalar()) or 0
            if gain_kg > 0:
                fcr = round(consumed / gain_kg, 3)
                fcr_note = (f"口径：饲料消耗 {consumed:.2f}kg / 鱼体增重 {gain_kg:.2f}kg；"
                            f"称重区间 {first.weighed_at:%Y-%m-%d} ~ {last.weighed_at:%Y-%m-%d}")
            else:
                fcr_note = "有效增重为 0 或负值，暂不能计算"
        else:
            fcr_note = "缺少鱼数量或有效称重记录，暂不能计算"
    else:
        fcr_note = "称重记录不足两次，暂不能计算饲料系数"

    return ok({
        "feed_today_kg": feed_today,
        "feed_window_kg": feed_window,
        "window_days": days,
        "fcr": fcr,
        "fcr_note": fcr_note,
        "saving_note": "节省饲料量需有同口径基准记录；无基准时不展示“已节省”",
    })


# ---------------------------------------------------------------- 日志与配置
@bp.get("/logs")
def logs():
    if not g.user:
        return fail("未提供有效身份", 401)
    q = g.db.query(AuditLog)
    for key, col in (("category", AuditLog.category), ("action", AuditLog.action)):
        v = request.args.get(key)
        if v:
            q = q.filter(col == v)
    if request.args.get("pond_id"):
        q = q.filter(AuditLog.pond_id == int(request.args["pond_id"]))
    if request.args.get("task_no"):
        t = g.db.query(FeedingTask).filter_by(task_no=request.args["task_no"]).first()
        q = q.filter(AuditLog.task_id == (t.id if t else -1))
    rows = q.order_by(AuditLog.id.desc()).limit(int(request.args.get("limit", 100))).all()
    return ok([row(x, ["id", "category", "action", "pond_id", "device_id",
                       "task_id", "user_id", "detail", "created_at"]) for x in rows])


@bp.get("/config")
def list_config():
    if not g.user:
        return fail("未提供有效身份", 401)
    rows = g.db.query(SysConfig).order_by(SysConfig.key).all()
    return ok([row(x, ["key", "value", "unit", "note", "effective_at", "updated_at"])
               for x in rows])


@bp.put("/config/<key>")
def set_config(key):
    if not g.user:
        return fail("未提供有效身份", 401)
    if g.user.role not in ("owner", "admin"):
        return fail("仅塘主/管理员可修改配置", 403)
    row_cfg = g.db.query(SysConfig).filter_by(key=key).first()
    if not row_cfg:
        return fail("配置项不存在", 404)
    body = request.get_json(silent=True) or {}
    old = row_cfg.value
    row_cfg.value = str(body.get("value"))
    row_cfg.effective_at = now()
    row_cfg.updated_by = g.user.id
    audit(g.db, "config", "update_config", user_id=g.user.id,
          detail={"key": key, "old": old, "new": row_cfg.value})
    g.db.commit()
    return ok(row(row_cfg, ["key", "value", "unit", "effective_at"]))


# ---------------------------------------------------------------- 导出
@bp.get("/ponds/<int:pond_id>/export")
def export(pond_id):
    """导出当前授权范围内的记录（含时间、单位、来源）。"""
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    rows = (g.db.query(Measurement).filter_by(pond_id=pond_id)
            .order_by(Measurement.collected_at.desc()).limit(1000).all())
    return ok([{"time": m.collected_at.strftime("%Y-%m-%d %H:%M:%S"),
                "metric": m.metric, "value": m.value, "unit": m.unit,
                "source": m.source, "valid": m.valid} for m in rows],
              note="导出内容包含时间、单位及来源")
