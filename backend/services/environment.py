"""环境数据服务：上传校验、有效性、日内曲线与统计特征（需求说明书 5.2）。

关键约束：
- 每次上传带鱼塘、设备、采集时间、指标值、单位、采集状态。
- 超量程 / 漂移 / 离线保留原始记录并打异常标记；无效值不进入建议输入。
- 缺测不补零；不能用当前温度代替全天变化。
- 断线补传保留原采集时间，区分采集时间与补传时间。
"""
from datetime import datetime, timedelta

from sqlalchemy import func

from models import Alert, Device, Measurement, Terminal
from services.common import audit, cfg, cfg_float, cfg_range, now

METRIC_CN = {
    "temperature": "水温", "oxygen": "溶氧", "ph": "pH", "orp": "ORP",
    "salinity": "盐度", "water_quality": "水质指数",
}
RANGE_CFG = {
    "temperature": "temp_valid_range",
    "oxygen": "oxygen_valid_range",
    "ph": "ph_valid_range",
}


def validate_metric(db, metric, value):
    """按配置量程校验。返回 (valid, reason)。"""
    if metric in RANGE_CFG:
        lo, hi = cfg_range(db, RANGE_CFG[metric], (0, 9999))
        if value < lo or value > hi:
            return False, f"超量程（有效范围 {lo}~{hi}）"
    return True, None


def ingest(db, payload):
    """接收一条环境测量。返回 (measurement, error)。

    必填：pond_id, metric, value, unit, collected_at
    可选：device_code, terminal_code, source
    """
    pond_id = payload.get("pond_id")
    metric = (payload.get("metric") or "").strip()
    if not pond_id or not metric:
        return None, "缺少 pond_id 或 metric"
    try:
        value = float(payload.get("value"))
    except (TypeError, ValueError):
        return None, "value 必须是数值"
    unit = payload.get("unit") or ""

    # 采集时间：缺失或不可解析 -> 拒绝（不静默用当前时间顶替）
    raw_ts = payload.get("collected_at")
    if not raw_ts:
        return None, "缺少采集时间 collected_at"
    try:
        if isinstance(raw_ts, str):
            collected = datetime.fromisoformat(raw_ts.replace("Z", "").replace("T", " "))
        else:
            collected = raw_ts
    except ValueError:
        return None, "采集时间格式不正确"

    device = None
    if payload.get("device_code"):
        device = db.query(Device).filter_by(code=payload["device_code"]).first()
        if not device:
            return None, f"设备不存在：{payload['device_code']}"

    terminal = None
    if payload.get("terminal_code"):
        terminal = db.query(Terminal).filter_by(code=payload["terminal_code"]).first()

    valid, reason = validate_metric(db, metric, value)
    m = Measurement(
        pond_id=pond_id,
        device_id=device.id if device else None,
        terminal_id=terminal.id if terminal else None,
        batch_id=payload.get("batch_id"),
        metric=metric, value=value, unit=unit,
        collected_at=collected, received_at=now(),
        valid=valid, invalid_reason=reason,
        source=payload.get("source", "real"),
    )
    db.add(m)

    if not valid:
        _raise_alert(db, pond_id, device, "data_invalid",
                     f"{METRIC_CN.get(metric, metric)} 数值 {value}{unit} {reason}")

    audit(db, "validate", "ingest_measurement", pond_id=pond_id,
          device_id=device.id if device else None,
          detail={"metric": metric, "value": value, "valid": valid, "reason": reason})
    db.commit()
    return m, None


def _raise_alert(db, pond_id, device, kind, message):
    """同一设备持续异常关联为持续事件，避免重复刷新产生大量独立记录。"""
    existing = (db.query(Alert)
                .filter(Alert.pond_id == pond_id, Alert.kind == kind,
                        Alert.status.in_(["open", "processing"]))
                .order_by(Alert.id.desc()).first())
    if existing and existing.device_id == (device.id if device else None):
        existing.is_ongoing = True
        existing.occurrence_count = (existing.occurrence_count or 1) + 1
        existing.message = message
        return existing
    a = Alert(code=_alert_code(db), pond_id=pond_id,
              device_id=device.id if device else None,
              kind=kind, level="warning",
              message=message, is_ongoing=False, occurrence_count=1)
    db.add(a)
    return a


def _alert_code(db):
    from services.common import next_code
    return next_code(db, Alert, "code", "AL")


def latest(db, pond_id, metric, upto=None):
    """最近一次有效测量。upto 给定时只取该时刻之前（避免用未来数据）。"""
    q = db.query(Measurement).filter(
        Measurement.pond_id == pond_id, Measurement.metric == metric,
        Measurement.valid.is_(True))
    if upto is not None:
        q = q.filter(Measurement.collected_at <= upto)
    return q.order_by(Measurement.collected_at.desc()).first()


def is_expired(db, m):
    if not m:
        return True
    expire_min = cfg_float(db, "measurement_expire_min", 180)
    return now() - m.collected_at > timedelta(minutes=expire_min)


def day_curve(db, pond_id, metric, day):
    """返回某日的原始曲线（有序）与异常/缺测情况。"""
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    rows = (db.query(Measurement)
            .filter(Measurement.pond_id == pond_id, Measurement.metric == metric,
                    Measurement.collected_at >= start, Measurement.collected_at < end)
            .order_by(Measurement.collected_at).all())
    return rows


def day_features(db, pond_id, metric, day, expected_interval_min=600):
    """日内统计特征。剔除无效样本后计算，注明窗口、有效样本数与缺失情况。

    不使用即时值代替全天变化；缺测不补零。
    """
    rows = day_curve(db, pond_id, metric, day)
    valid = [r for r in rows if r.valid]
    values = [r.value for r in valid]
    expected = int(24 * 60 / expected_interval_min) if expected_interval_min else None
    feat = {
        "metric": metric,
        "day": day.strftime("%Y-%m-%d"),
        "window": f"{day} 00:00 ~ 24:00",
        "samples_total": len(rows),
        "samples_valid": len(valid),
        "samples_invalid": len(rows) - len(valid),
        "expected_samples": expected,
        "missing": max(0, (expected or 0) - len(valid)),
        "enough": len(valid) > 0,
    }
    if values:
        feat.update({
            "min": min(values), "max": max(values),
            "mean": round(sum(values) / len(values), 3),
            "change": round(max(values) - min(values), 3),
            "first": values[0], "last": values[-1],
        })
    else:
        feat.update({"min": None, "max": None, "mean": None,
                     "change": None, "first": None, "last": None})
    return feat


def history(db, pond_id, metric, start=None, end=None, limit=2000):
    q = db.query(Measurement).filter(Measurement.pond_id == pond_id,
                                     Measurement.metric == metric)
    if start:
        q = q.filter(Measurement.collected_at >= start)
    if end:
        q = q.filter(Measurement.collected_at <= end)
    return q.order_by(Measurement.collected_at.desc()).limit(limit).all()


def serial(m):
    return {
        "id": m.id, "pond_id": m.pond_id, "metric": m.metric,
        "metric_cn": METRIC_CN.get(m.metric, m.metric),
        "value": m.value, "unit": m.unit,
        "collected_at": m.collected_at.strftime("%Y-%m-%d %H:%M:%S"),
        "received_at": m.received_at.strftime("%Y-%m-%d %H:%M:%S") if m.received_at else None,
        "valid": m.valid, "invalid_reason": m.invalid_reason,
        "source": m.source,
    }
