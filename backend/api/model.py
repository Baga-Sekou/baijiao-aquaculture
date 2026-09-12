"""AI 模型训练与预测接口（需求书 2.3「AI 模型预测」的实现）。

- POST /api/ponds/<id>/model/train                 训练（生长 / 投喂量 / 水质）
- GET  /api/ponds/<id>/model/status                模型版本与指标
- GET  /api/ponds/<id>/model/predict?target=...    预测（明确输出目标/单位/范围/版本）

数据不足或模型未训练时明确返回原因（reason=insufficient_data / model_not_trained），
不返回伪预测。
"""
from flask import Blueprint, g, request

from services import ml
from services.common import can_view, fail, ok

bp = Blueprint("model", __name__)


def _auth(pond_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    if not can_view(g.db, g.user, pond_id):
        return fail("无权访问该鱼塘", 403)
    return None


@bp.post("/ponds/<int:pond_id>/model/train")
def train(pond_id):
    err = _auth(pond_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    target = body.get("target", "all")
    if target == "all":
        results, first_err = ml.train_all(g.db, pond_id)
        return ok(results, note="三个目标分别报告；数据不足的目标未训练")
    if target not in ml.VERSION:
        return fail(f"未知训练目标 target={target}（支持 growth / feed / water_quality / all）")
    payload, msg = {"growth": ml.train_growth, "feed": ml.train_feed,
                    "water_quality": ml.train_water}[target](g.db, pond_id)
    if msg:
        return fail(msg, 200, reason="insufficient_data")
    return ok({"target": target, "version": payload["version"],
               "r2": payload.get("r2"), "mae": payload.get("mae"),
               "n_samples": payload.get("n_samples"), "basis": payload.get("basis")})


@bp.get("/ponds/<int:pond_id>/model/status")
def model_status(pond_id):
    err = _auth(pond_id)
    if err:
        return err
    return ok(ml.status(g.db, pond_id))


@bp.get("/ponds/<int:pond_id>/model/predict")
def predict(pond_id):
    err = _auth(pond_id)
    if err:
        return err
    target = request.args.get("target", "growth")
    horizon = request.args.get("horizon")
    horizon_v = None
    if horizon is not None:
        try:
            horizon_v = int(horizon)
        except ValueError:
            return fail("horizon 必须是整数（天或小时，随目标而定）")
    payload, msg = ml.predict(g.db, pond_id, target, horizon_v)
    if msg:
        reason = "model_not_trained" if "尚未训练" in msg else "insufficient_data"
        return fail(msg, 200, reason=reason)
    return ok(payload, note=payload.get("note"))
