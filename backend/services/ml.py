"""AI 模型预测服务（需求书 2.3 标注的后续扩展，本模块为其实现）。

覆盖三个预测目标：
- growth        生长预测：称重记录拟合「日龄 → 体重」回归，外推到达标日期
- feed          投喂量预测：按日汇总投喂量，对「生物量 + 水温均值」做多元回归
- water_quality 水质预测：最近水温/溶氧小时均值做线性趋势外推（短期）

实现约束（与项目原则一致）：
- 训练数据不足时明确返回原因，不返回伪预测、不返回 0。
- 模型版本、训练时间、样本数、R²/MAE 一并保存与返回，输出可追溯。
- 模型保存为 data/models/ 下的 JSON（纯标准库即可训练/预测，无额外依赖）。
- 外推范围有限：生长预测最多外推 180 天，水质最多外推 24 小时，超范围明确说明。
"""
import json
import math
import os
from datetime import datetime, timedelta

from sqlalchemy import func, or_

from models import (Batch, FeedingTask, Measurement, Pond, WeighRecord)
from services.common import now

MODEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "models")

VERSION = {
    "growth": "growth-lin-v1",
    "feed": "feed-lin-v1",
    "water_quality": "wq-trend-v1",
}

# 数据量下限：低于该值视为「数据不足」，明确拒绝而非硬预测
MIN_GROWTH_SAMPLES = 3        # 称重样本
MIN_GROWTH_SPAN_DAYS = 14     # 称重跨度
MIN_FEED_DAYS = 7             # 有投喂记录的天数
MIN_WQ_HOURS = 12             # 水质趋势的有效小时样本
MAX_GROWTH_EXTRAPOLATE_DAYS = 180
MAX_WQ_HORIZON_HOURS = 24


# ---------------------------------------------------------------- 数值工具
def _solve(A, b):
    """解小型线性方程组（高斯消元，带主元选择）。"""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            return None
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r == c:
                continue
            f = M[r][c] / M[c][c]
            for k in range(c, n + 1):
                M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def _ols(xs, ys):
    """多元最小二乘（按列归一化后解正规方程，避免量纲悬殊导致的病态）。

    xs: [[f1, f2, ...], ...]，自动加常数项；零方差列剔除并置 0 系数。
    返回 (coefs=[w0, w1, ...], r2, mae)；完全共线/退化时返回 (None, None, None)。
    """
    n_feat = len(xs[0])
    # 中心化标准差判断常量列（rms 会把大幅值常量列误判为有效特征）；
    # 有效列按 std 归一化，cond 数量级受控
    stats = []
    for j in range(n_feat):
        col = [row[j] for row in xs]
        mean = sum(col) / len(col)
        std = math.sqrt(sum((v - mean) ** 2 for v in col) / len(col))
        stats.append((mean, std))
    kept = [j for j in range(n_feat)
            if stats[j][1] > 1e-9 * max(1.0, abs(stats[j][0]))]
    X = [[1.0] + [row[j] / stats[j][1] for j in kept] for row in xs]
    m = len(X[0])
    A = [[sum(X[i][a] * X[i][b_] for i in range(len(X))) for b_ in range(m)]
         for a in range(m)]
    b = [sum(X[i][a] * ys[i] for i in range(len(X))) for a in range(m)]
    coefs_red = _solve(A, b)
    if coefs_red is None:
        return None, None, None
    coefs = [0.0] * (n_feat + 1)
    coefs[0] = coefs_red[0]
    for k, j in enumerate(kept, start=1):
        coefs[j + 1] = coefs_red[k] / stats[j][1]
    resid = [ys[i] - sum(c * x for c, x in zip(coefs, [1.0] + xs[i]))
             for i in range(len(xs))]
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum(r ** 2 for r in resid)
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else 1.0
    mae = sum(abs(r) for r in resid) / len(resid)
    return coefs, round(r2, 4), round(mae, 4)


def _model_path(pond_id, target):
    return os.path.join(MODEL_DIR, f"pond_{pond_id}_{target}.json")


def _save(pond_id, target, payload):
    os.makedirs(MODEL_DIR, exist_ok=True)
    payload["version"] = VERSION[target]
    payload["target"] = target
    payload["pond_id"] = pond_id
    payload["trained_at"] = now().strftime("%Y-%m-%d %H:%M:%S")
    with open(_model_path(pond_id, target), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return payload


def _load(pond_id, target):
    p = _model_path(pond_id, target)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- 数据准备
def _pond_exists(db, pond_id):
    return db.query(Pond).filter_by(id=pond_id).first() is not None


def _weight_series(db, pond_id, batch=None):
    """称重序列 [(datetime, avg_weight)]，按时间升序。

    给定批次时取「本批次 + 未归属批次但落在投苗期之后」的记录，
    避免把上一批（如 CSV 历史数据）的体重接成同一条生长曲线；
    手工登记的称重常不带批次，故不能只按 batch_id 过滤。
    """
    q = (db.query(WeighRecord)
         .filter(WeighRecord.pond_id == pond_id,
                 WeighRecord.avg_weight.isnot(None)))
    if batch is not None:
        q = q.filter(or_(WeighRecord.batch_id == batch.id,
                         WeighRecord.batch_id.is_(None)))
    rows = q.order_by(WeighRecord.weighed_at).all()
    out = [(r.weighed_at, r.avg_weight) for r in rows]
    if batch is not None and batch.stock_date:
        out = [(t, w) for t, w in out if t >= batch.stock_date]
    return out


def _active_batch(db, pond_id):
    return (db.query(Batch).filter_by(pond_id=pond_id, status="active")
            .order_by(Batch.id.desc()).first())


def _daily_feed(db, pond_id, days=90):
    """按日汇总已确认投喂量（只统计落定状态，不含 unknown/失败）。

    归日锚点：优先 finished_at（完成时间），回退 created_at——演示数据会回填
    完整时间轴，而接口创建的任务两者一致。
    """
    since = now() - timedelta(days=days)
    rows = (db.query(FeedingTask.finished_at, FeedingTask.created_at,
                     FeedingTask.confirm_amount)
            .filter(FeedingTask.pond_id == pond_id,
                    FeedingTask.created_at >= since,
                    FeedingTask.status.in_(["done", "stopped"])).all())
    daily = {}
    for finished, created, amt in rows:
        if amt is None:
            continue
        ts = finished or created
        key = ts.date()
        daily[key] = daily.get(key, 0.0) + amt
    return daily


def _daily_temp(db, pond_id, days=90):
    """按日平均有效水温。"""
    since = now() - timedelta(days=days)
    rows = (db.query(Measurement.collected_at, Measurement.value)
            .filter(Measurement.pond_id == pond_id,
                    Measurement.metric == "temperature",
                    Measurement.valid.is_(True),
                    Measurement.collected_at >= since).all())
    daily = {}
    for ts, v in rows:
        daily.setdefault(ts.date(), []).append(v)
    return {k: sum(v) / len(v) for k, v in daily.items()}


def _weight_at(weighs, batch, day):
    """按称重记录线性插值估计某日鱼重（g）。"""
    if weighs:
        if day <= weighs[0][0]:
            return weighs[0][1]
        if day >= weighs[-1][0]:
            return weighs[-1][1]
        for (t0, w0), (t1, w1) in zip(weighs, weighs[1:]):
            if t0 <= day <= t1 and t1 > t0:
                f = (day - t0).total_seconds() / (t1 - t0).total_seconds()
                return w0 + f * (w1 - w0)
    ref = (batch.stock_date or batch.created_at) if batch else None
    w = (batch.initial_weight if batch else None) or 50.0
    return w if ref else w


# ---------------------------------------------------------------- 训练
def train_growth(db, pond_id):
    """生长模型：体重(g) ~ 投后天数 线性回归。"""
    if not _pond_exists(db, pond_id):
        return None, "鱼塘不存在"
    batch = _active_batch(db, pond_id)
    weighs = _weight_series(db, pond_id, batch)
    ref = (batch.stock_date or (weighs[0][0] if weighs else None)) if (batch or weighs) else None
    if not ref or len(weighs) < MIN_GROWTH_SAMPLES:
        return None, (f"数据不足：有效称重记录 {len(weighs)} 条，"
                      f"至少需要 {MIN_GROWTH_SAMPLES} 条（两次称重之间的间隔计 1 条起）")
    xs, ys = [], []
    for t, w in weighs:
        d = (t - ref).total_seconds() / 86400.0
        xs.append([d])
        ys.append(w)
    span = xs[-1][0] - xs[0][0]
    if span < MIN_GROWTH_SPAN_DAYS:
        return None, (f"数据不足：称重跨度仅 {span:.0f} 天，"
                      f"至少需要 {MIN_GROWTH_SPAN_DAYS} 天才能外推生长趋势")
    coefs, r2, mae = _ols(xs, ys)
    if coefs is None:
        return None, "训练失败：称重数据共线或退化"
    daily_gain = coefs[1]
    return _save(pond_id, "growth", {
        "coefs": coefs, "r2": r2, "mae": round(mae, 2),
        "n_samples": len(ys), "span_days": round(span, 1),
        "daily_gain_g": round(daily_gain, 3),
        "reference_date": ref.strftime("%Y-%m-%d"),
        "unit": "g",
        "basis": "体重(g) = w0 + w1 × 投后天数；样本为历史称重记录",
    }), None


def train_feed(db, pond_id):
    """投喂量模型：日投喂量(kg) ~ 生物量(kg) + 当日均温(℃)。"""
    if not _pond_exists(db, pond_id):
        return None, "鱼塘不存在"
    batch = _active_batch(db, pond_id)
    if not batch or not batch.fish_number:
        return None, "数据不足：缺少活动批次或投魂数量，无法估计生物量"
    feed = _daily_feed(db, pond_id)
    temps = _daily_temp(db, pond_id)
    weighs = _weight_series(db, pond_id, batch)
    days = sorted(set(feed) & set(temps))
    if len(days) < MIN_FEED_DAYS:
        return None, (f"数据不足：同时有投喂记录与水温记录的天数仅 {len(days)} 天，"
                      f"至少需要 {MIN_FEED_DAYS} 天")
    xs, ys = [], []
    for d in days:
        w = _weight_at(weighs, batch, datetime.combine(d, datetime.min.time()))
        biomass = w * batch.fish_number / 1000.0
        xs.append([biomass, temps[d]])
        ys.append(feed[d])
    coefs, r2, mae = _ols(xs, ys)
    if coefs is None:
        return None, "训练失败：特征共线或退化"
    return _save(pond_id, "feed", {
        "coefs": coefs, "r2": r2, "mae": round(mae, 3),
        "n_samples": len(ys),
        "window_days": len(days),
        "unit": "kg",
        "basis": "日投喂量(kg) = w0 + w1 × 生物量(kg) + w2 × 日均水温(℃)；"
                 "生物量 = 鱼重(g) × 投魂数量 / 1000，鱼重由称重记录插值",
    }), None


def train_water(db, pond_id):
    """水质趋势模型：最近 72 小时水温/溶氧的小时均值线性趋势。"""
    if not _pond_exists(db, pond_id):
        return None, "鱼塘不存在"
    since = now() - timedelta(hours=72)
    trends = {}
    counts = {}
    for metric in ("temperature", "oxygen"):
        rows = (db.query(Measurement.collected_at, Measurement.value)
                .filter(Measurement.pond_id == pond_id, Measurement.metric == metric,
                        Measurement.valid.is_(True),
                        Measurement.collected_at >= since)
                .order_by(Measurement.collected_at).all())
        hourly = {}
        for ts, v in rows:
            hourly.setdefault(ts.replace(minute=0, second=0, microsecond=0), []).append(v)
        hours = sorted(hourly)
        counts[metric] = len(hours)
        if len(hours) >= MIN_WQ_HOURS:
            t0 = hours[0]
            xs = [[(h - t0).total_seconds() / 3600.0] for h in hours]
            ys = [sum(hourly[h]) / len(hourly[h]) for h in hours]
            coefs, r2, mae = _ols(xs, ys)
            if coefs is not None:
                trends[metric] = {
                    "coefs": coefs, "r2": r2, "mae": round(mae, 4),
                    "n_hours": len(hours),
                    "t0": t0.strftime("%Y-%m-%d %H:%M:%S"),
                    "last_value": ys[-1],
                }
    if "temperature" not in trends and "oxygen" not in trends:
        return None, (f"数据不足：最近 72 小时内水温/溶氧有效小时样本不足"
                      f"（水温 {counts.get('temperature', 0)} 小时、"
                      f"溶氧 {counts.get('oxygen', 0)} 小时，至少各需 {MIN_WQ_HOURS} 小时之一）")
    return _save(pond_id, "water_quality", {
        "trends": trends, "unit": None,
        "basis": "小时均值线性趋势外推：值(t) = w0 + w1 × 小时数；仅适合短期（≤24h）参考",
    }), None


def train_all(db, pond_id):
    out, first_err = {}, None
    for target in ("growth", "feed", "water_quality"):
        payload, err = {"growth": train_growth, "feed": train_feed,
                        "water_quality": train_water}[target](db, pond_id)
        out[target] = ({"ok": True, "version": VERSION[target],
                        "r2": payload.get("r2"), "n_samples": payload.get("n_samples")}
                       if payload else {"ok": False, "message": err})
        if err and not first_err:
            first_err = f"{target}: {err}"
    return out, first_err


# ---------------------------------------------------------------- 预测
def _fmt(dt_):
    return dt_.strftime("%Y-%m-%d %H:%M")


def predict_growth(db, pond_id, horizon_days=None):
    m = _load(pond_id, "growth")
    if not m:
        return None, "模型尚未训练：请先调用 POST /api/ponds/<id>/model/train"
    batch = _active_batch(db, pond_id)
    ref = datetime.strptime(m["reference_date"], "%Y-%m-%d")
    weighs = _weight_series(db, pond_id, batch)
    today = now()
    current_w = _weight_at(weighs, batch, today)
    w0, w1 = m["coefs"]
    fit_today = w0 + w1 * (today - ref).total_seconds() / 86400.0
    daily_gain = m["daily_gain_g"]

    horizon = int(horizon_days or 30)
    horizon = max(1, min(horizon, MAX_GROWTH_EXTRAPOLATE_DAYS))
    # 预测曲线从当前估计体重起算（与 KPI 同口径），按拟合日增重外推
    series = [{"date": _fmt(today + timedelta(days=i)),
               "weight_g": round(current_w + i * daily_gain, 1)}
              for i in range(0, horizon + 1, max(1, horizon // 10))]

    days_to_target, target_note = None, None
    if batch and batch.target_weight and w1 > 0:
        days_to_target = math.ceil((batch.target_weight - current_w) / w1)
        if days_to_target < 0:
            days_to_target = 0
            target_note = "当前估计体重已达到目标体重"
        elif days_to_target > MAX_GROWTH_EXTRAPOLATE_DAYS:
            target_note = (f"按当前生长速度需 {days_to_target} 天达标，"
                           f"超出线性外推可靠范围（{MAX_GROWTH_EXTRAPOLATE_DAYS} 天），仅供参考")
            days_to_target = None
        else:
            target_note = (f"按当前生长速度 {w1:.2f}g/天，约 {days_to_target} 天后"
                           f"达到目标体重 {batch.target_weight}g")
    elif batch and batch.target_weight:
        target_note = "拟合生长速度非正或为 0，无法估计达标时间"
    return {
        "target": "growth", "unit": "g",
        "version": m["version"], "trained_at": m["trained_at"],
        "current_weight_g": round(current_w, 1),
        "model_weight_g": round(fit_today, 1),
        "daily_gain_g": m["daily_gain_g"],
        "target_weight_g": batch.target_weight if batch else None,
        "days_to_target": days_to_target,
        "eta_date": _fmt(today + timedelta(days=days_to_target)) if days_to_target is not None else None,
        "horizon_days": horizon,
        "series": series,
        "metrics": {"r2": m["r2"], "mae_g": m["mae"], "n_samples": m["n_samples"]},
        "basis": m["basis"],
        "note": target_note or "线性外推仅供参考，临近上市请以实际称重为准",
    }, None


def predict_feed(db, pond_id, horizon_days=None):
    m = _load(pond_id, "feed")
    if not m:
        return None, "模型尚未训练：请先调用 POST /api/ponds/<id>/model/train"
    batch = _active_batch(db, pond_id)
    if not batch or not batch.fish_number:
        return None, "缺少活动批次或投魂数量，无法估计生物量"
    w0, w1, w2 = m["coefs"]
    today = now()
    weighs = _weight_series(db, pond_id, batch)
    temp = _daily_temp(db, pond_id, days=3)
    temp_mean = (sum(temp.values()) / len(temp.values())) if temp else None
    if temp_mean is None:
        return None, "缺少近 3 天有效水温数据，无法给出投喂量预测（不使用假设温度）"
    horizon = int(horizon_days or 1)
    horizon = max(1, min(horizon, 7))
    out = []
    for i in range(1, horizon + 1):
        day = today + timedelta(days=i)
        w = _weight_at(weighs, batch, day)
        biomass = w * batch.fish_number / 1000.0
        amount = w0 + w1 * biomass + w2 * temp_mean
        out.append({"date": day.strftime("%Y-%m-%d"),
                    "amount_kg": round(max(amount, 0.0), 2),
                    "biomass_kg": round(biomass, 1)})
    return {
        "target": "feed", "unit": "kg",
        "version": m["version"], "trained_at": m["trained_at"],
        "temperature_basis_c": round(temp_mean, 2),
        "temperature_note": "沿用近 3 天日均水温；未来温度未知，预测随实际水温变化需重新评估",
        "horizon_days": horizon,
        "series": out,
        "metrics": {"r2": m["r2"], "mae_kg": m["mae"], "n_samples": m["n_samples"]},
        "basis": m["basis"],
        "note": "预测值为日投喂总量参考；单次投喂仍以系统建议+人工确认为准",
    }, None


def predict_water(db, pond_id, horizon_hours=None):
    m = _load(pond_id, "water_quality")
    if not m:
        return None, "模型尚未训练：请先调用 POST /api/ponds/<id>/model/train"
    horizon = int(horizon_hours or 6)
    horizon = max(1, min(horizon, MAX_WQ_HORIZON_HOURS))
    unit = {"temperature": "℃", "oxygen": "mg/L"}
    series, notes = [], []
    for metric, tr in m["trends"].items():
        t0 = datetime.strptime(tr["t0"], "%Y-%m-%d %H:%M:%S")
        base = (now() - t0).total_seconds() / 3600.0
        w0, w1 = tr["coefs"]
        vals = []
        for i in range(1, horizon + 1):
            v = w0 + w1 * (base + i)
            vals.append({"time": _fmt(now() + timedelta(hours=i)),
                         "value": round(v, 2)})
        if w1 < 0 and vals[-1]["value"] < 0:
            notes.append(f"{metric} 趋势外推出现负值，已截断为 0，提示趋势样本不足")
        for v in vals:
            v["value"] = max(v["value"], 0.0)
        series.append({"metric": metric, "metric_unit": unit.get(metric, ""),
                       "points": vals,
                       "current_value": round(tr["last_value"], 2),
                       "trend_per_hour": round(w1, 4)})
    return {
        "target": "water_quality", "unit": None,
        "version": m["version"], "trained_at": m["trained_at"],
        "horizon_hours": horizon,
        "series": series,
        "metrics": {k: {"r2": t["r2"], "mae": t["mae"], "n_hours": t["n_hours"]}
                    for k, t in m["trends"].items()},
        "basis": m["basis"],
        "note": "；".join(notes) if notes else
                "线性趋势外推仅适合短期参考；溶氧受天气与增氧机影响大，请结合实时监测",
    }, None


def predict(db, pond_id, target, horizon=None):
    fn = {"growth": predict_growth, "feed": predict_feed,
          "water_quality": predict_water}.get(target)
    if not fn:
        return None, f"未知预测目标 target={target}（支持 growth / feed / water_quality）"
    return fn(db, pond_id, horizon)


def status(db, pond_id):
    out = []
    for target in ("growth", "feed", "water_quality"):
        m = _load(pond_id, target)
        if not m:
            out.append({"target": target, "trained": False,
                        "version": VERSION[target],
                        "note": "未训练，调用 POST /api/ponds/<id>/model/train"})
        else:
            out.append({"target": target, "trained": True,
                        "version": m["version"], "trained_at": m["trained_at"],
                        "r2": m.get("r2"), "n_samples": m.get("n_samples")})
    return out
