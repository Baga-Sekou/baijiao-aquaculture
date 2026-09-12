"""投喂建议服务（需求说明书 5.3）。

设计要点：
- 输入：批次与鱼数量、清洗后的环境曲线/统计特征、摄食状态、历史投喂。
- 输出：建议量 + 单位 + 依据 + 规则版本 + 有效期限；失败时返回原因，不返回 0。
- 温度参与决策过程可追溯：先判断温度有效性，再按规则计算，保存输入快照。
- 课堂联调用的示例规则必须明确标识其测试用途。
"""
import json
from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from models import Batch, FeedingFeedback, FeedingTask, Suggestion
from services.common import (audit, cfg, cfg_float, commit_with_code_retry,
                             next_code, now, weight_series)
from services import environment as env

RULE_VERSION_FALLBACK = "rule-v1.0-demo"

# 示例规则参数（演示用途，非养殖阈值；真实口径由业务方确认）
DEMO_RULE = {
    "label": "演示规则（测试用途，非养殖依据）",
    "base_ratio": 0.015,        # 参考投喂量 = 全塘生物量(kg) × 比例
    "temp_optimal": (24.0, 28.0),
    "temp_factor_low": 0.7,     # 低于适温区
    "temp_factor_high": 0.6,    # 高于适温区
    "feedback_factor": {"hungry": 1.1, "normal": 1.0, "full": 0.6, "unknown": 0.9},
}


def _latest_feedback(db, pond_id):
    return (db.query(FeedingFeedback)
            .filter(FeedingFeedback.pond_id == pond_id, FeedingFeedback.valid.is_(True))
            .order_by(FeedingFeedback.observed_at.desc()).first())


def _feeder_for(db, pond_id):
    from models import Device
    return db.query(Device).filter_by(pond_id=pond_id, kind="feeder").first()


def generate(db, pond_id, batch_id=None, day=None, requested_by=None):
    """生成一条建议。返回 (suggestion, error_message)。

    口径：全塘单次投喂量(kg) = 鱼重(g) × 投魂数量 / 1000 × 比例 × 系数；
    编号唯一冲突（并发）时回退重新生成再提交。
    """
    for attempt in range(4):
        sg, err = _build(db, pond_id, batch_id, day, requested_by)
        if err:
            return None, err
        try:
            db.commit()
            return sg, None
        except IntegrityError:
            db.rollback()
            if attempt == 3:
                raise
    raise RuntimeError("unreachable")


def _build(db, pond_id, batch_id, day, requested_by):
    batch = None
    if batch_id:
        batch = db.query(Batch).filter_by(id=batch_id, pond_id=pond_id).first()
    if not batch:
        batch = (db.query(Batch).filter_by(pond_id=pond_id, status="active")
                 .order_by(Batch.id.desc()).first())
    if not batch:
        return None, "该鱼塘没有可用的养殖批次"

    # ---- 1) 温度有效性检查（先判断有效，再计算）----
    temp_m = env.latest(db, pond_id, "temperature")
    if not temp_m:
        return None, "缺少必需输入：无有效水温数据"
    if env.is_expired(db, temp_m):
        return None, (f"数据过期：最近水温采集于 "
                      f"{temp_m.collected_at:%Y-%m-%d %H:%M}，已超过有效期")

    # ---- 2) 日内特征（不使用即时值代替全天变化）----
    target_day = (day or temp_m.collected_at.date())
    feat = env.day_features(db, pond_id, "temperature", target_day)
    if not feat["enough"]:
        return None, "数据不足：当日无有效水温样本，无法形成特征"

    # ---- 3) 摄食状态与鱼重（称重绑定当前批次，不跨批次取值）----
    fb = _latest_feedback(db, pond_id)
    fb_state = "unknown"
    if fb:
        expire_min = cfg_float(db, "measurement_expire_min", 180)
        from datetime import timedelta as _td
        if fb.observed_at and now() - fb.observed_at <= _td(minutes=expire_min):
            fb_state = fb.state
        # 反馈过期则按 unknown 处理，不把旧反馈当成当前状态

    weight = batch.initial_weight or 50.0
    latest_w = None
    weighs = weight_series(db, pond_id, batch)
    if weighs:
        latest_w = weighs[-1][1]
    if latest_w:
        weight = latest_w

    if not batch.fish_number:
        return None, "批次缺少投魂数量，无法按全塘口径计算建议量"
    biomass_kg = weight * batch.fish_number / 1000.0

    # ---- 4) 规则计算 ----
    temp_for_calc = feat["mean"] if feat["mean"] is not None else temp_m.value
    base = biomass_kg * DEMO_RULE["base_ratio"]
    lo, hi = DEMO_RULE["temp_optimal"]
    if temp_for_calc < lo:
        temp_factor = DEMO_RULE["temp_factor_low"]
        temp_note = f"水温均值 {temp_for_calc:.1f}℃ 低于适温区 {lo}~{hi}℃"
    elif temp_for_calc > hi:
        temp_factor = DEMO_RULE["temp_factor_high"]
        temp_note = f"水温均值 {temp_for_calc:.1f}℃ 高于适温区 {lo}~{hi}℃"
    else:
        temp_factor = 1.0
        temp_note = f"水温均值 {temp_for_calc:.1f}℃ 处于适温区 {lo}~{hi}℃"

    fb_factor = DEMO_RULE["feedback_factor"].get(fb_state, 0.9)
    amount = base * temp_factor * fb_factor

    # ---- 5) 安全限制（每次上限/下限由配置给出）----
    max_amt = cfg_float(db, "max_feed_per_task", 1200)
    min_amt = cfg_float(db, "min_feed_per_task", 0.1)
    capped = False
    if amount > max_amt:
        amount, capped = max_amt, True
    if amount < min_amt:
        amount = min_amt

    valid_min = cfg_float(db, "feed_suggest_valid_min", 30)
    rule_version = cfg(db, "rule_version", RULE_VERSION_FALLBACK)

    reason = (
        f"规则 {rule_version}（{DEMO_RULE['label']}）："
        f"全塘口径 鱼重 {weight:.1f}g × {batch.fish_number} 尾 / 1000 = 生物量 {biomass_kg:.1f}kg，"
        f"× 基础比例 {DEMO_RULE['base_ratio']} = {base:.2f}kg；"
        f"{temp_note}，温度系数 {temp_factor}；"
        f"摄食状态 {fb_state}，系数 {fb_factor}；"
        f"温度特征窗口 {feat['window']}，有效样本 {feat['samples_valid']}/{feat['expected_samples']}。"
        + (f"已按单次上限 {max_amt}kg 截断。" if capped else "")
    )

    sg = Suggestion(
        code=next_code(db, Suggestion, "code", "SG"),
        pond_id=pond_id, batch_id=batch.id,
        status="pending",
        amount=round(amount, 3), unit="kg",
        reason=reason, rule_version=rule_version,
        inputs_json=json.dumps({
            "temperature": {"value": temp_m.value, "unit": temp_m.unit,
                            "collected_at": temp_m.collected_at.strftime("%Y-%m-%d %H:%M:%S")},
            "day_features": feat,
            "feedback_state": fb_state,
            "fish_weight_g": weight,
            "fish_number": batch.fish_number,
            "biomass_kg": round(biomass_kg, 1),
            "batch_code": batch.code,
            "demo_rule": DEMO_RULE["label"],
        }, ensure_ascii=False),
        generated_at=now(),
        valid_until=now() + timedelta(minutes=valid_min),
    )
    db.add(sg)
    audit(db, "control", "suggest_generate", pond_id=pond_id,
          user_id=requested_by,
          detail={"suggestion": sg.code, "amount": sg.amount,
                  "rule_version": rule_version, "demo": True})
    return sg, None


def expire_stale(db):
    """把已过有效期的待确认建议标记为已失效。"""
    rows = (db.query(Suggestion)
            .filter(Suggestion.status == "pending",
                    Suggestion.valid_until < now()).all())
    for s in rows:
        s.status = "expired"
    if rows:
        db.commit()
    return len(rows)


def serial(sg):
    if not sg:
        return None
    return {
        "id": sg.id, "code": sg.code, "pond_id": sg.pond_id,
        "batch_id": sg.batch_id, "status": sg.status,
        "amount": sg.amount, "unit": sg.unit,
        "reason": sg.reason, "rule_version": sg.rule_version,
        "generated_at": sg.generated_at.strftime("%Y-%m-%d %H:%M:%S"),
        "valid_until": sg.valid_until.strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": json.loads(sg.inputs_json) if sg.inputs_json else None,
    }
