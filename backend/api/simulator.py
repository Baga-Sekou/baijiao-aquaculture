"""演示开关：终端仿真器的启停（需求说明书 2.3 仿真验证）。

- 打开：后端内启动仿真终端，自动应答回执 → 投喂闭环顺畅跑完。
- 关闭：不启动终端 → 下发后无回执 → 复现「结果未知（待核查）」异常流程。

仅用于课堂演示；仿真终端不投真实饲料，实际量为模拟值（来源标 simulated）。
"""
from flask import Blueprint, g, request

from services import simulator_ctl
from services.common import fail, ok

bp = Blueprint("simulator", __name__)


def _need_owner():
    if not g.user:
        return fail("未提供有效身份", 401)
    if g.user.role not in ("owner", "admin"):
        return fail("仅塘主/管理员可操作演示开关", 403)
    return None


@bp.get("/simulator/status")
def sim_status():
    """查看开关状态（不限角色，便于页面展示）。"""
    if not g.user:
        return fail("未提供有效身份", 401)
    return ok(simulator_ctl.status())


@bp.post("/simulator/start")
def sim_start():
    err = _need_owner()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    # 默认覆盖全部鱼塘：只起一个塘的终端会让别的塘的任务卡在「已下发」，
    # 演示时容易误以为系统坏了。需要单塘演示时可显式传 pond_ids。
    ponds = body.get("pond_ids") or _all_pond_ids(g.db)
    interval = float(body.get("interval") or 3)
    port = int(request.host.split(":")[-1]) if ":" in request.host else 5000
    started, msg = simulator_ctl.start(port, pond_ids=[int(p) for p in ponds],
                                       interval=interval)
    if not started:
        return ok(simulator_ctl.status(), message=msg)
    return ok(simulator_ctl.status(), message=msg)


def _all_pond_ids(db):
    from models import Pond
    return [p.id for p in db.query(Pond).order_by(Pond.id).all()]


@bp.post("/simulator/stop")
def sim_stop():
    err = _need_owner()
    if err:
        return err
    stopped, msg = simulator_ctl.stop()
    return ok(simulator_ctl.status(), message=msg)
