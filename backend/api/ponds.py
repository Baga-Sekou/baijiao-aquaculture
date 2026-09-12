"""鱼塘与批次、用户、授权相关接口（需求说明书 5.1）。"""
from flask import Blueprint, g, request

from models import Batch, Device, Pond, PondGrant, Terminal, User
from services.common import (audit, can_view, fail, now, ok, pond_brief,
                             terminal_brief)
from services.serialize import row

bp = Blueprint("ponds", __name__)


def _need_user():
    if not g.user:
        return fail("未提供有效身份（请带 X-User 请求头）", 401)
    return None


@bp.get("/users")
def list_users():
    """列出用户（供前端切换身份，演示用；不含密码字段）。"""
    users = g.db.query(User).order_by(User.id).all()
    return ok([row(u, ["id", "username", "display_name", "role", "phone"]) for u in users])


@bp.get("/ponds")
def list_ponds():
    err = _need_user()
    if err:
        return err
    ponds = g.db.query(Pond).order_by(Pond.code).all()
    out = []
    for p in ponds:
        if not can_view(g.db, g.user, p.id):
            continue
        d = pond_brief(p)
        b = (g.db.query(Batch).filter_by(pond_id=p.id, status="active")
             .order_by(Batch.id.desc()).first())
        d["active_batch"] = row(b, ["id", "code", "species", "fish_number",
                                    "initial_weight", "target_weight",
                                    "plan_harvest_date", "status"]) if b else None
        feeder = g.db.query(Device).filter_by(pond_id=p.id, kind="feeder").first()
        d["feeder"] = row(feeder, ["id", "code", "status"]) if feeder else None
        t = g.db.query(Terminal).filter_by(pond_id=p.id).first()
        d["terminal"] = terminal_brief(t) if t else None
        out.append(d)
    return ok(out)


@bp.get("/ponds/<int:pond_id>")
def get_pond(pond_id):
    err = _need_user()
    if err:
        return err
    p = g.db.query(Pond).filter_by(id=pond_id).first()
    if not p:
        return fail("鱼塘不存在", 404)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    d = pond_brief(p)
    d["batches"] = [row(b, ["id", "code", "species", "fry_source", "stock_date",
                            "fish_number", "initial_weight", "target_weight",
                            "weigh_cycle_days", "plan_harvest_date",
                            "actual_harvest_date", "status"])
                    for b in g.db.query(Batch).filter_by(pond_id=pond_id).all()]
    d["devices"] = [row(x, ["id", "code", "kind", "metric", "unit", "location", "status"])
                    for x in g.db.query(Device).filter_by(pond_id=pond_id).all()]
    return ok(d)


@bp.post("/ponds/<int:pond_id>/batches")
def create_batch(pond_id):
    """新建批次（重新投苗时另建批次，避免跨批次接生长曲线）。"""
    err = _need_user()
    if err:
        return err
    if g.user.role not in ("owner", "admin"):
        return fail("仅塘主/管理员可新建批次", 403)
    p = g.db.query(Pond).filter_by(id=pond_id).first()
    if not p:
        return fail("鱼塘不存在", 404)
    body = request.get_json(silent=True) or {}
    if not body.get("code"):
        return fail("缺少批次编号 code")
    b = Batch(pond_id=pond_id, code=body["code"],
              species=body.get("species", "海鲈"),
              fry_source=body.get("fry_source"),
              fish_number=body.get("fish_number"),
              initial_weight=body.get("initial_weight"),
              target_weight=body.get("target_weight"),
              weigh_cycle_days=body.get("weigh_cycle_days"),
              src_note=body.get("src_note", "手工登记"))
    g.db.add(b)
    audit(g.db, "config", "create_batch", pond_id=pond_id, user_id=g.user.id,
          detail={"batch": b.code})
    g.db.commit()
    return ok(row(b, ["id", "code", "species", "fish_number", "initial_weight",
                      "target_weight", "status"]))


@bp.get("/grants")
def list_grants():
    err = _need_user()
    if err:
        return err
    rows = g.db.query(PondGrant).filter_by(user_id=g.user.id).all()
    return ok([{"pond_id": x.pond_id, "can_control": x.can_control,
                "can_review": x.can_review} for x in rows])
