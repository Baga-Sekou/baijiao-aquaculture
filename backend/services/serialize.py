"""统一序列化助手：把 ORM 行按白名单字段转成 dict（含 datetime 格式化）。"""
from datetime import datetime


def dt(v):
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return v


def row(obj, fields):
    """按 fields 列表序列化。fields 支持 ('alias', 'attr') 或 'attr'。"""
    out = {}
    for f in fields:
        if isinstance(f, tuple):
            alias, attr = f
        else:
            alias = attr = f
        out[alias] = dt(getattr(obj, attr, None))
    return out
