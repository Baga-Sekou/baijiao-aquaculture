"""投喂任务服务（需求说明书 5.4「投喂控制与执行记录」）。

核心约束：
- 客户端请求编号做幂等：相同请求编号返回原任务。
- 后端生成唯一任务号；同一设备已有未结束/待核查任务时，不接收新任务。
- 状态：pending 待下发 / dispatched 已下发待回执 / running 执行中 /
        done 已完成 / stopped 已停止 / failed 失败 / unknown 结果未知(待核查) / cancelled
- 建议量、确认量、实际量分列；设备无法测量时实际量=未获取，不用确认量补齐。
- 回执超时 -> 待核查并保留设备占用；不自动重发。
- 迟到/重复回执关联原任务，不重复累计；与核查结论冲突时保留证据、再次核对。
"""
import json
from datetime import timedelta

from models import Alert, AuditLog, Device, FeedingTask, Receipt, Review, Suggestion, Terminal
from sqlalchemy.orm import object_session
from services.common import audit, cfg, cfg_float, now, parse_dt

ACTIVE_STATUSES = ("pending", "dispatched", "running", "unknown")


def _feeder_for(db, pond_id):
    return db.query(Device).filter_by(pond_id=pond_id, kind="feeder").first()


def _terminal_for(db, pond_id):
    return db.query(Terminal).filter_by(pond_id=pond_id).first()


def device_busy(db, device_id):
    """同一设备已有未结束或待核查任务时，暂不接受新的投喂任务。"""
    return (db.query(FeedingTask)
            .filter(FeedingTask.device_id == device_id,
                    FeedingTask.device_locked.is_(True))
            .first())


def create(db, payload, user, expected_pond_id=None):
    """创建投喂任务。幂等：相同 request_no 返回原任务。

    expected_pond_id：URL 中的鱼塘。建议所属鱼塘必须与其一致，
    防止用其他塘的建议编号跨塘越权下发；幂等命中时同样校验。
    返回 (task, error, reason)
    """
    request_no = (payload.get("request_no") or "").strip()
    suggestion_code = (payload.get("suggestion_code") or "").strip()
    if not request_no:
        return None, "缺少请求编号 request_no", "reject"
    if not suggestion_code:
        return None, "缺少建议编号 suggestion_code", "reject"

    # ---- 幂等：相同请求编号直接返回原任务 ----
    dup = db.query(FeedingTask).filter_by(request_no=request_no).first()
    if dup:
        if expected_pond_id is not None and dup.pond_id != expected_pond_id:
            return None, "该请求编号已属于其他鱼塘的任务", "reject"
        audit(db, "task", "duplicate_request", pond_id=dup.pond_id, task_id=dup.id,
              user_id=getattr(user, "id", None),
              detail={"request_no": request_no, "returned_task": dup.task_no})
        db.commit()
        return dup, None, "duplicate"

    sg = db.query(Suggestion).filter_by(code=suggestion_code).first()
    if not sg:
        return None, f"建议不存在：{suggestion_code}", "reject"
    if expected_pond_id is not None and sg.pond_id != expected_pond_id:
        return None, f"建议 {suggestion_code} 不属于该鱼塘", "forbidden"
    if sg.status == "expired" or (sg.valid_until and sg.valid_until < now()):
        sg.status = "expired"
        db.commit()
        return None, "建议已过期，不能直接执行", "reject"
    if sg.status == "confirmed":
        return None, "该建议已被确认，不能重复使用", "reject"
    if sg.status == "cancelled":
        return None, "该建议已取消，不能执行", "reject"
    if sg.status != "pending":
        return None, f"建议状态 {sg.status} 不可执行", "reject"

    pond_id = sg.pond_id
    try:
        confirm_amount = float(payload.get("confirm_amount", sg.amount))
    except (TypeError, ValueError):
        return None, "确认量必须是数值", "reject"
    if confirm_amount <= 0:
        return None, "确认量必须大于 0", "reject"

    min_amt = cfg_float(db, "min_feed_per_task", 0.1)
    max_amt = cfg_float(db, "max_feed_per_task", 1200)
    if not (min_amt <= confirm_amount <= max_amt):
        return None, f"确认量超出允许范围 {min_amt}~{max_amt}kg", "reject"

    unit = payload.get("unit") or sg.unit or "kg"
    change_reason = payload.get("change_reason")
    if abs(confirm_amount - (sg.amount or 0)) > 1e-6 and not change_reason:
        return None, "修改建议量时必须填写原因", "reject"

    device = _feeder_for(db, pond_id)
    if not device:
        return None, "该鱼塘没有投料设备", "reject"

    busy = device_busy(db, device.id)
    if busy:
        return None, (f"设备已有未结束任务 {busy.task_no}（状态 {busy.status}），"
                      f"暂不接受新的投喂任务"), "reject"

    terminal = _terminal_for(db, pond_id)
    from services.common import next_code
    task = FeedingTask(
        task_no=next_code(db, FeedingTask, "task_no", "TK"),
        request_no=request_no,
        suggestion_id=sg.id, pond_id=pond_id, batch_id=sg.batch_id,
        device_id=device.id, terminal_id=terminal.id if terminal else None,
        suggested_amount=sg.amount, confirm_amount=confirm_amount,
        unit=unit, change_reason=change_reason,
        status="pending", approved_by=getattr(user, "id", None), approved_at=now(),
        device_locked=True,
    )
    db.add(task)
    sg.status = "confirmed"
    # 任务保存与设备占用一致：先持久化，再由调用方下发
    audit(db, "task", "create", pond_id=pond_id, task_id=task.id,
          user_id=getattr(user, "id", None),
          detail={"task_no": task.task_no, "request_no": request_no,
                  "confirm_amount": confirm_amount, "suggestion": sg.code,
                  "changed": bool(change_reason)})
    db.commit()
    return task, None, None


def dispatch(db, task):
    """下发任务到终端。仅“接收成功”不代表投喂完成。"""
    if task.status != "pending":
        return task, f"任务状态 {task.status} 不可下发"
    task.status = "dispatched"
    task.dispatched_at = now()
    if task.terminal_id:
        t = db.query(Terminal).filter_by(id=task.terminal_id).first()
        if t:
            t.status = "online"
            t.last_seen = now()
    audit(db, "task", "dispatch", pond_id=task.pond_id, task_id=task.id,
          detail={"task_no": task.task_no, "terminal_id": task.terminal_id})
    db.commit()
    return task, None


def _next_seq(db, task_id):
    from sqlalchemy import func
    n = db.query(func.count(Receipt.id)).filter(Receipt.task_id == task_id).scalar() or 0
    return n + 1


def receive_receipt(db, payload):
    """接收终端回执。返回 (task, error)。

    状态机约束：终态（done/stopped/failed）与待核查（unknown）不接受过程回执
    造成的回退——乱序/迟到回执一律留痕（late/duplicate 标记），不改变状态。
    """
    task_no = (payload.get("task_no") or "").strip()
    kind = (payload.get("kind") or "").strip()   # accept/execute/finish/stop
    status = (payload.get("status") or "").strip()
    if not task_no or not kind:
        return None, "缺少 task_no 或 kind"
    if kind not in ("accept", "execute", "finish", "stop"):
        return None, f"回执类型 kind 不合法：{kind}"
    valid_status = {"accept": ("accepted",), "execute": ("running",),
                    "finish": ("done", "failed", "stopped"),
                    "stop": ("stopped",)}
    if status not in valid_status.get(kind, ()):
        return None, f"{kind} 回执的 status 必须是 {'/'.join(valid_status[kind])}"

    task = db.query(FeedingTask).filter_by(task_no=task_no).first()
    if not task:
        return None, f"任务不存在：{task_no}"

    actual = payload.get("actual_amount")
    try:
        actual = float(actual) if actual not in (None, "") else None
    except (TypeError, ValueError):
        return None, "actual_amount 必须是数值"
    if actual is not None and (actual < 0 or actual != actual or actual == float("inf")):
        return None, "actual_amount 不能为负数或非有限数值"

    # ---- 重复回执：同一 kind 已存在则不重复累计 ----
    dup = (db.query(Receipt)
           .filter(Receipt.task_id == task.id, Receipt.kind == kind)
           .first())
    is_dup = dup is not None
    # ---- 迟到回执：任务已核查结束 ----
    reviewed = (db.query(Review).filter_by(task_id=task.id)
                .order_by(Review.id.desc()).first())
    is_late = reviewed is not None and task.status in ("done", "stopped", "failed", "unknown") \
        and reviewed.conclusion in ("done", "stopped", "failed", "not_executed")

    # 实际量来源：终端可声明 source。
    # 仿真/估算值不得冒充实测（需求书 5.3「汇总不得伪装成实测值」）：
    # 只有声明 simulated 才记为 simulated，其余默认为真机实测。
    declared = (payload.get("source") or "").strip().lower()
    actual_source = "simulated" if declared in ("sim", "simulated", "simulate") else "measured"

    r = Receipt(
        task_id=task.id, kind=kind, status=status,
        actual_amount=actual, fault=payload.get("fault"),
        raw=json.dumps(payload, ensure_ascii=False),
        seq=_next_seq(db, task.id), late=is_late, duplicate=is_dup,
        occurred_at=parse_dt(payload.get("occurred_at"), now()),
        received_at=now(),
    )
    db.add(r)

    if is_dup:
        # 不重复累计用料，只留痕
        audit(db, "task", "duplicate_receipt", pond_id=task.pond_id, task_id=task.id,
              detail={"kind": kind, "status": status})
        db.commit()
        return task, None

    # 迟到回执与已定核查结论冲突 -> 标记待再次核对（在状态守卫之前，保证留痕）
    if is_late and reviewed:
        mapped = {"finish": {"done": "done", "failed": "failed", "stopped": "stopped"},
                  "stop": {"stopped": "stopped"}}.get(kind, {}).get(status)
        if mapped and mapped != reviewed.conclusion:
            reviewed.conflict = True
            _raise_conflict_alert(db, task, reviewed, mapped)

    # ---- 状态推进（显式允许的转换，乱序回执不回退状态）----
    allowed = {
        "accept": ("dispatched",),
        "execute": ("dispatched", "running"),
        "finish": ("dispatched", "running"),
        "stop": ("dispatched", "running"),
    }
    if task.status not in allowed[kind]:
        # 终态/待核查后的迟到过程回执：留痕，不改变状态、不覆盖实测量
        audit(db, "task", "receipt_ignored_out_of_order", pond_id=task.pond_id,
              task_id=task.id,
              detail={"kind": kind, "status": status,
                      "current": task.status, "late": is_late})
        db.commit()
        return task, None

    if kind == "accept":
        task.status = "running"
        task.started_at = now()
    elif kind == "execute":
        task.status = "running"
    elif kind == "finish":
        if status == "failed" or payload.get("fault"):
            task.status = "failed"
            _raise_fault_alert(db, task, payload.get("fault"), status)
        elif status == "stopped":
            task.status = "stopped"
        else:
            task.status = "done"
        task.finished_at = now()
        if actual is not None:
            task.actual_amount = actual
            task.actual_source = actual_source
        else:
            task.actual_amount = None
            task.actual_source = "unknown"    # 不用确认量补齐
        task.device_locked = False            # 正常结束才释放占用
    elif kind == "stop":
        task.status = "stopped"
        task.finished_at = now()
        if actual is not None:
            task.actual_amount = actual
            task.actual_source = actual_source
        task.device_locked = False

    audit(db, "task", f"receipt_{kind}", pond_id=task.pond_id, task_id=task.id,
          detail={"status": status, "actual": actual, "late": is_late})
    db.commit()
    return task, None


def _alert_code(db):
    from services.common import next_code
    return next_code(db, Alert, "code", "AL")


def _raise_fault_alert(db, task, fault, status):
    db.add(Alert(code=_alert_code(db),
                 pond_id=task.pond_id, device_id=task.device_id, task_id=task.id,
                 kind="feed_fail", level="error",
                 message=f"任务 {task.task_no} 执行失败：{fault or status}，"
                         f"实际投喂量{'未知' if task.actual_amount is None else task.actual_amount}",
                 status="open"))


def _raise_conflict_alert(db, task, review, mapped):
    db.add(Alert(code=_alert_code(db),
                 pond_id=task.pond_id, device_id=task.device_id, task_id=task.id,
                 kind="receipt_conflict", level="warning",
                 message=f"任务 {task.task_no} 迟到回执({mapped})与核查结论"
                         f"({review.conclusion})不一致，需再次核对",
                 status="open"))


def check_timeouts(db, timeout_sec=None):
    """回执超时 -> 标记结果未知（待核查），保留设备占用，不自动重发。

    覆盖两个等待阶段：dispatched（等待接收/开始）以 dispatched_at 计，
    running（等待执行完成）以 started_at（缺省 dispatched_at）计——
    设备接收任务后失联同样要进入待核查闭环。
    """
    # 注意：timeout_sec=0 是合法值（立即判超时），不能用 `or` 兜底
    t = cfg_float(db, "task_timeout_sec", 120) if timeout_sec is None else timeout_sec
    # 留 1 秒余量：任务刚下发 (<1s) 时不应被判定为超时
    deadline = now() - timedelta(seconds=t + 1)
    rows = (db.query(FeedingTask)
            .filter(FeedingTask.status.in_(["dispatched", "running"]),
                    FeedingTask.device_locked.is_(True)).all())
    timed_out = []
    for task in rows:
        anchor = task.started_at or task.dispatched_at
        if anchor is None or anchor >= deadline:
            continue
        task.status = "unknown"
        audit(db, "task", "receipt_timeout", pond_id=task.pond_id, task_id=task.id,
              detail={"task_no": task.task_no, "timeout_sec": t,
                      "from_status": "dispatched/running"})
        db.add(Alert(code=_alert_code(db),
                     pond_id=task.pond_id, device_id=task.device_id, task_id=task.id,
                     kind="receipt_timeout", level="error",
                     message=f"任务 {task.task_no} 回执超时，结果待核实，已暂停追加",
                     status="open"))
        timed_out.append(task)
    if timed_out:
        db.commit()
    return len(timed_out)


def stop_requested(db, task):
    return db.query(AuditLog.id).filter_by(task_id=task.id, action="stop_request").first() is not None


def request_stop(db, task, user, reason):
    if task.status not in ("dispatched", "running"):
        return "只有已下发或执行中的任务可以请求停止"
    if not isinstance(reason, str) or not reason.strip():
        return "请填写停止原因"
    if not stop_requested(db, task):
        audit(db, "control", "stop_request", task_id=task.id, pond_id=task.pond_id,
              user_id=user.id, detail={"reason": reason.strip()})
        db.commit()
    return None


def review(db, task_no, user, conclusion, basis=None, evidence=None, device_recovery=None,
           recovery_checks=None):
    """投喂核查。结论 done/stopped/failed/not_executed/unknown。"""
    task = db.query(FeedingTask).filter_by(task_no=task_no).first()
    if not task:
        return None, f"任务不存在：{task_no}"
    if conclusion not in ("done", "stopped", "failed", "not_executed", "unknown"):
        return None, "核查结论不合法"

    if task.status not in ("unknown", "done", "stopped", "failed"):
        return None, "任务仍在下发或执行，请先请求停止或等待超时核查"
    if not isinstance(basis, str) or not basis.strip():
        return None, "请填写核查依据"
    if device_recovery is not None and not isinstance(device_recovery, str):
        return None, "设备恢复依据必须是文字"
    device_recovery = (device_recovery or "").strip() or None
    if device_recovery:
        if conclusion == "unknown":
            return None, "执行结果仍不明，不能解除设备占用"
        checks = recovery_checks if isinstance(recovery_checks, dict) else {}
        if not all(checks.get(k) is True for k in
                   ("terminal_idle", "old_command_disabled", "device_ready")):
            return None, "请逐项确认终端无任务执行、旧指令不会继续执行、设备可用"
    if conclusion == "unknown":
        task.status = "unknown"
        task.device_locked = True

    rv = Review(task_id=task.id, reviewer_id=getattr(user, "id", None),
                conclusion=conclusion, basis=basis, evidence=evidence,
                device_recovery=device_recovery)
    db.add(rv)

    if conclusion != "unknown":
        # 仍不明时保持待核查
        task.status = {"done": "done", "stopped": "stopped",
                       "failed": "failed", "not_executed": "failed"}[conclusion]
        if conclusion == "not_executed":
            task.actual_amount = None
            task.actual_source = "unknown"
        task.finished_at = task.finished_at or now()
        # 解除设备占用的条件：核查结束且设备满足恢复条件
        if device_recovery:
            task.device_locked = False

    audit(db, "control", "review", pond_id=task.pond_id, task_id=task.id,
          user_id=getattr(user, "id", None),
          detail={"task_no": task_no, "conclusion": conclusion,
                  "released": bool(device_recovery), "recovery_checks": recovery_checks})
    db.commit()
    return task, None


def serial(task, with_receipts=False):
    if not task:
        return None
    d = {
        "id": task.id, "task_no": task.task_no, "request_no": task.request_no,
        "pond_id": task.pond_id, "batch_id": task.batch_id,
        "suggestion_id": task.suggestion_id,
        "suggested_amount": task.suggested_amount,
        "confirm_amount": task.confirm_amount,
        "actual_amount": task.actual_amount,
        "actual_source": task.actual_source,
        "unit": task.unit, "status": task.status,
        "change_reason": task.change_reason,
        "stop_requested": stop_requested(object_session(task), task),
        "device_locked": task.device_locked,
        "approved_at": task.approved_at.strftime("%Y-%m-%d %H:%M:%S") if task.approved_at else None,
        "dispatched_at": task.dispatched_at.strftime("%Y-%m-%d %H:%M:%S") if task.dispatched_at else None,
        "finished_at": task.finished_at.strftime("%Y-%m-%d %H:%M:%S") if task.finished_at else None,
    }
    if with_receipts:
        d["receipts"] = [{
            "kind": r.kind, "status": r.status, "actual_amount": r.actual_amount,
            "fault": r.fault, "seq": r.seq, "late": r.late, "duplicate": r.duplicate,
            "occurred_at": r.occurred_at.strftime("%Y-%m-%d %H:%M:%S") if r.occurred_at else None,
            "received_at": r.received_at.strftime("%Y-%m-%d %H:%M:%S") if r.received_at else None,
        } for r in sorted(task.receipts, key=lambda x: x.seq or 0)]
        d["reviews"] = [{
            "conclusion": rv.conclusion, "basis": rv.basis,
            "evidence": rv.evidence, "reviewer_id": rv.reviewer_id,
            "device_recovery": rv.device_recovery, "conflict": rv.conflict,
            "created_at": rv.created_at.strftime("%Y-%m-%d %H:%M:%S") if rv.created_at else None,
        } for rv in task.reviews]
    return d
