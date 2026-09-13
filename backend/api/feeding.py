"""投喂建议、任务、回执与核查接口（需求说明书 5.3 / 5.4）。"""
from datetime import datetime

from flask import Blueprint, g, request
from sqlalchemy.exc import IntegrityError

from models import FeedingTask, Review, Suggestion
from services import suggestion as sug_svc
from services import task_service as task_svc
from services.common import can_control, can_review, can_view, fail, ok

bp = Blueprint("feeding", __name__)


def _auth(need_control=False, need_review=False, pond_id=None):
    if not g.user:
        return fail("未提供有效身份（请带 X-User 请求头）", 401)
    if pond_id is not None:
        if not can_view(g.db, g.user, pond_id):
            return fail("无权访问该鱼塘", 403)
        if need_control and not can_control(g.db, g.user, pond_id):
            return fail("无投喂控制权限", 403)
        if need_review and not can_review(g.db, g.user, pond_id):
            return fail("无投喂核查权限", 403)
    return None


# ---------------------------------------------------------------- 建议
@bp.post("/ponds/<int:pond_id>/suggestions")
def create_suggestion(pond_id):
    err = _auth(need_control=True, pond_id=pond_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    day = None
    if body.get("date"):
        try:
            day = datetime.strptime(body["date"], "%Y-%m-%d").date()
        except ValueError:
            return fail("date 格式应为 YYYY-MM-DD")
    # 并发生成时编号可能冲突：回退重新生成再提交
    last_exc = None
    for attempt in range(4):
        try:
            sg, msg = sug_svc.generate(g.db, pond_id, batch_id=body.get("batch_id"),
                                       day=day, requested_by=g.user.id)
            break
        except IntegrityError as e:
            last_exc = e
            g.db.rollback()
    else:
        return fail("编号生成冲突，请重试", 503, reason="code_conflict")
    if msg:
        return fail(msg, 200, reason="no_valid_suggestion")
    return ok(sug_svc.serial(sg))


@bp.get("/suggestions/<code>")
def get_suggestion(code):
    if not g.user:
        return fail("未提供有效身份", 401)
    sg = g.db.query(Suggestion).filter_by(code=code).first()
    if not sg:
        return fail("建议不存在", 404)
    if not can_view(g.db, g.user, sg.pond_id):
        return fail("无权访问该建议", 403)
    return ok(sug_svc.serial(sg))


@bp.post("/suggestions/<code>/cancel")
def cancel_suggestion(code):
    """取消待确认建议：pending -> cancelled。已确认/已失效的不可取消。"""
    if not g.user:
        return fail("未提供有效身份", 401)
    sg = g.db.query(Suggestion).filter_by(code=code).first()
    if not sg:
        return fail("建议不存在", 404)
    if not can_control(g.db, g.user, sg.pond_id):
        return fail("无权操作该建议", 403)
    if sg.status != "pending":
        return fail(f"建议状态为 {sg.status}，只有待确认状态可取消", 409, reason="not_cancellable")
    sg.status = "cancelled"
    from services.common import audit
    audit(g.db, "control", "suggest_cancel", pond_id=sg.pond_id, user_id=g.user.id,
          detail={"suggestion": sg.code})
    g.db.commit()
    return ok(sug_svc.serial(sg), note="已取消的建议不能再下发任务")


@bp.get("/ponds/<int:pond_id>/suggestions")
def list_suggestions(pond_id):
    err = _auth(pond_id=pond_id)
    if err:
        return err
    sug_svc.expire_stale(g.db)
    rows = (g.db.query(Suggestion).filter_by(pond_id=pond_id)
            .order_by(Suggestion.id.desc()).limit(30).all())
    return ok([sug_svc.serial(s) for s in rows])


# ---------------------------------------------------------------- 任务
@bp.post("/ponds/<int:pond_id>/tasks")
def create_task(pond_id):
    err = _auth(need_control=True, pond_id=pond_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    # 并发创建时任务号可能冲突：回退重建再提交
    last_exc = None
    for attempt in range(4):
        try:
            task, msg, kind = task_svc.create(g.db, body, g.user, expected_pond_id=pond_id)
            break
        except IntegrityError as e:
            last_exc = e
            g.db.rollback()
    else:
        return fail("任务编号冲突，请重试", 503, reason="code_conflict")
    if msg:
        code = 200 if kind == "duplicate" else (403 if kind == "forbidden" else 400)
        return fail(msg, code, reason=kind)
    # 保存成功后下发
    task, derr = task_svc.dispatch(g.db, task)
    return ok(task_svc.serial(task, with_receipts=True), dispatched=derr is None)


@bp.get("/tasks/<task_no>")
def get_task(task_no):
    if not g.user:
        return fail("未提供有效身份", 401)
    task = g.db.query(FeedingTask).filter_by(task_no=task_no).first()
    if not task:
        return fail("任务不存在", 404)
    if not can_view(g.db, g.user, task.pond_id):
        return fail("无权访问该任务", 403)
    return ok(task_svc.serial(task, with_receipts=True))


@bp.get("/ponds/<int:pond_id>/tasks")
def list_tasks(pond_id):
    err = _auth(pond_id=pond_id)
    if err:
        return err
    status = request.args.get("status")
    q = g.db.query(FeedingTask).filter_by(pond_id=pond_id)
    if status:
        q = q.filter(FeedingTask.status == status)
    rows = q.order_by(FeedingTask.id.desc()).limit(50).all()
    return ok([task_svc.serial(t) for t in rows])


@bp.get("/tasks")
def list_all_tasks():
    """待核查列表：跨鱼塘查询结果未知/待核查任务。"""
    if not g.user:
        return fail("未提供有效身份", 401)
    status = request.args.get("status", "unknown")
    q = g.db.query(FeedingTask)
    if status != "all":
        q = q.filter(FeedingTask.status == status)
    rows = [t for t in q.order_by(FeedingTask.id.desc()).limit(200).all()
            if can_view(g.db, g.user, t.pond_id)]
    return ok([task_svc.serial(t, with_receipts=True) for t in rows[:50]])


# ---------------------------------------------------------------- 终端取任务
@bp.get("/terminals/<code>/tasks")
def terminal_tasks(code):
    """采集控制终端拉取下发给自己鱼塘的任务（含去重所需的任务号）。"""
    from models import Terminal
    t = g.db.query(Terminal).filter_by(code=code).first()
    if not t:
        return fail("终端未注册", 404)
    rows = (g.db.query(FeedingTask)
            .filter(FeedingTask.terminal_id == t.id,
                    FeedingTask.status.in_(["dispatched", "running"]))
            .order_by(FeedingTask.id).all())
    return ok([task_svc.serial(t2) for t2 in rows])


# ---------------------------------------------------------------- 回执（终端调用）
@bp.post("/receipts")
def post_receipt():
    body = request.get_json(silent=True) or {}
    task, msg = task_svc.receive_receipt(g.db, body)
    if msg:
        return fail(msg, 400)
    return ok(task_svc.serial(task, with_receipts=True))


# ---------------------------------------------------------------- 人工请求停止
@bp.post("/tasks/<task_no>/stop")
def stop_task(task_no):
    if not g.user:
        return fail("未提供有效身份", 401)
    task = g.db.query(FeedingTask).filter_by(task_no=task_no).first()
    if not task:
        return fail("任务不存在", 404)
    err = _auth(need_control=True, pond_id=task.pond_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    msg = task_svc.request_stop(g.db, task, g.user, body.get("reason"))
    if msg:
        return fail(msg, 400)
    return ok(task_svc.serial(task), note="已请求停止，等待设备反馈；未反馈时需现场处理")


# ---------------------------------------------------------------- 核查
@bp.post("/tasks/<task_no>/review")
def review_task(task_no):
    if not g.user:
        return fail("未提供有效身份", 401)
    task = g.db.query(FeedingTask).filter_by(task_no=task_no).first()
    if not task:
        return fail("任务不存在", 404)
    err = _auth(need_review=True, pond_id=task.pond_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    conclusion = body.get("conclusion")
    task, msg = task_svc.review(
        g.db, task_no, g.user, conclusion,
        basis=body.get("basis"), evidence=body.get("evidence"),
        device_recovery=body.get("device_recovery"),
        recovery_checks=body.get("recovery_checks"))
    if msg:
        return fail(msg, 400)
    return ok(task_svc.serial(task, with_receipts=True))


# ---------------------------------------------------------------- 超时巡检
@bp.post("/tasks/check-timeouts")
def check_timeouts():
    """由运维/前端临时触发；生产环境应由定时任务调用。"""
    n = task_svc.check_timeouts(g.db)
    return ok({"timed_out": n})
