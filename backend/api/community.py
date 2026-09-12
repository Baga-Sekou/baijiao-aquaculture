"""交流与排行榜接口（需求书 5.11，扩展项的实现）。

- 交流：发帖 / 回帖 / 列表（经验交流、问题求助、行情信息）。
- 排行榜：按窗口期内的饲料系数（FCR，越低越好）与生长速度排序；
  数据不足以计算口径的塘明确标注「未参与排名」，不用缺数据的数据凑名次。
"""
from datetime import timedelta

from flask import Blueprint, g, request
from sqlalchemy import func, or_

from models import (Alert, Batch, CommunityPost, FeedingTask, Pond, PostReply,
                    User, WeighRecord)
from services.common import (audit, can_view, fail, next_code, now, ok)
from services.serialize import row

bp = Blueprint("community", __name__)

CATEGORIES = ("经验交流", "问题求助", "行情信息")

RANK_NOTE = ("排名口径：饲料系数 = 窗口期饲料消耗 / 同期鱼体增重（越低越好），"
             "需同一批次内两次以上有效称重；数据不足的塘不参与排名")


def _user_brief(db, user_id):
    u = db.query(User).filter_by(id=user_id).first()
    return {"id": user_id, "name": u.display_name if u else f"用户{user_id}",
            "username": u.username if u else None, "role": u.role if u else None}


# ---------------------------------------------------------------- 交流帖
@bp.get("/community/posts")
def list_posts():
    if not g.user:
        return fail("未提供有效身份", 401)
    cat = request.args.get("category")
    q = g.db.query(CommunityPost)
    if cat:
        q = q.filter(CommunityPost.category == cat)
    rows = q.order_by(CommunityPost.id.desc()).limit(50).all()
    out = []
    for p in rows:
        d = row(p, ["id", "title", "content", "category", "status", "created_at"])
        d["author"] = _user_brief(g.db, p.user_id)
        d["pond_id"] = p.pond_id
        d["reply_count"] = (g.db.query(func.count(PostReply.id))
                            .filter(PostReply.post_id == p.id).scalar())
        latest = (g.db.query(PostReply).filter_by(post_id=p.id)
                  .order_by(PostReply.id.desc()).first())
        d["latest_reply"] = row(latest, ["content", "created_at"]) if latest else None
        out.append(d)
    return ok(out, categories=list(CATEGORIES))


@bp.post("/community/posts")
def create_post():
    if not g.user:
        return fail("未提供有效身份", 401)
    body = request.get_json(silent=True) or {}
    if not (body.get("title") or "").strip() or not (body.get("content") or "").strip():
        return fail("缺少标题 title 或内容 content")
    cat = body.get("category", "经验交流")
    if cat not in CATEGORIES:
        return fail(f"category 必须是 {'/'.join(CATEGORIES)}")
    pond_id = body.get("pond_id")
    if pond_id and not can_view(g.db, g.user, pond_id):
        return fail("无权关联该鱼塘", 403)
    p = CommunityPost(user_id=g.user.id, pond_id=pond_id,
                      title=body["title"].strip(), content=body["content"].strip(),
                      category=cat)
    g.db.add(p)
    audit(g.db, "control", "community_post_create", pond_id=pond_id,
          user_id=g.user.id, detail={"title": p.title, "category": cat})
    g.db.commit()
    d = row(p, ["id", "title", "content", "category", "created_at"])
    d["author"] = _user_brief(g.db, p.user_id)
    return ok(d)


@bp.get("/community/posts/<int:post_id>")
def get_post(post_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    p = g.db.query(CommunityPost).filter_by(id=post_id).first()
    if not p:
        return fail("帖子不存在", 404)
    d = row(p, ["id", "title", "content", "category", "status", "created_at"])
    d["author"] = _user_brief(g.db, p.user_id)
    d["pond_id"] = p.pond_id
    d["replies"] = [{
        "id": r.id, "content": r.content, "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "author": _user_brief(g.db, r.user_id),
    } for r in g.db.query(PostReply).filter_by(post_id=post_id)
        .order_by(PostReply.id).all()]
    return ok(d)


@bp.post("/community/posts/<int:post_id>/replies")
def reply_post(post_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    p = g.db.query(CommunityPost).filter_by(id=post_id).first()
    if not p:
        return fail("帖子不存在", 404)
    if p.status != "open":
        return fail("帖子已关闭，不能回复")
    body = request.get_json(silent=True) or {}
    if not (body.get("content") or "").strip():
        return fail("缺少回复内容 content")
    r = PostReply(post_id=post_id, user_id=g.user.id, content=body["content"].strip())
    g.db.add(r)
    g.db.commit()
    d = row(r, ["id", "content", "created_at"])
    d["author"] = _user_brief(g.db, r.user_id)
    return ok(d)


@bp.post("/community/posts/<int:post_id>/close")
def close_post(post_id):
    if not g.user:
        return fail("未提供有效身份", 401)
    p = g.db.query(CommunityPost).filter_by(id=post_id).first()
    if not p:
        return fail("帖子不存在", 404)
    if p.user_id != g.user.id and g.user.role not in ("owner", "admin"):
        return fail("仅发帖人或管理员可关闭帖子", 403)
    p.status = "closed"
    g.db.commit()
    return ok(row(p, ["id", "status"]))


# ---------------------------------------------------------------- 排行榜
def _pond_metrics(db, pond_id, since):
    """窗口期内可解释的单塘指标：饲料系数、生长速度、告警数。"""

    def feed_sum(start=None):
        q = db.query(func.sum(FeedingTask.confirm_amount)).filter(
            FeedingTask.pond_id == pond_id,
            FeedingTask.status.in_(["done", "stopped"]))
        if start:
            q = q.filter(FeedingTask.created_at >= start)
        return q.scalar() or 0.0

    weighs_q = (db.query(WeighRecord)
                .filter(WeighRecord.pond_id == pond_id,
                        WeighRecord.avg_weight.isnot(None)))
    batch = (db.query(Batch).filter_by(pond_id=pond_id, status="active")
             .order_by(Batch.id.desc()).first())
    if batch is not None:
        # 只取本批次（含未归属批次但投苗之后的记录），不跨批次接增重
        weighs_q = weighs_q.filter(or_(WeighRecord.batch_id == batch.id,
                                       WeighRecord.batch_id.is_(None)))
        if batch.stock_date:
            weighs_q = weighs_q.filter(WeighRecord.weighed_at >= batch.stock_date)
    weighs = weighs_q.order_by(WeighRecord.weighed_at).all()
    n_fish = batch.fish_number if batch and batch.fish_number else None

    fcr, fcr_basis = None, None
    if len(weighs) >= 2 and n_fish:
        first, last = weighs[0], weighs[-1]
        gain_kg = (last.avg_weight - first.avg_weight) * n_fish / 1000.0
        consumed = feed_sum(first.weighed_at)
        if gain_kg > 0 and consumed > 0:
            fcr = round(consumed / gain_kg, 3)
            fcr_basis = (f"饲料 {consumed:.1f}kg / 增重 {gain_kg:.1f}kg"
                         f"（{first.weighed_at:%m-%d} ~ {last.weighed_at:%m-%d}）")

    growth_rate, growth_basis = None, None
    if len(weighs) >= 2:
        first, last = weighs[0], weighs[-1]
        days = (last.weighed_at - first.weighed_at).total_seconds() / 86400.0
        if days >= 7:
            growth_rate = round((last.avg_weight - first.avg_weight) / days, 2)
            growth_basis = f"{first.avg_weight}g → {last.avg_weight}g / {days:.0f} 天"

    alerts = (db.query(func.count(Alert.id))
              .filter(Alert.pond_id == pond_id, Alert.created_at >= since).scalar()) or 0
    feed_window = round(feed_sum(since), 1)
    return {"fcr": fcr, "fcr_basis": fcr_basis,
            "growth_rate_g_day": growth_rate, "growth_basis": growth_basis,
            "alerts": alerts, "feed_window_kg": feed_window,
            "weigh_count": len(weighs)}


@bp.get("/leaderboard")
def leaderboard():
    if not g.user:
        return fail("未提供有效身份", 401)
    days = int(request.args.get("days", 30))
    since = now() - timedelta(days=days)
    ranked, insufficient = [], []
    for p in g.db.query(Pond).order_by(Pond.code).all():
        if not can_view(g.db, g.user, p.id):
            continue
        m = _pond_metrics(g.db, p.id, since)
        entry = {"pond": {"id": p.id, "code": p.code, "name": p.name},
                 "window_days": days, **m}
        if m["fcr"] is not None:
            ranked.append(entry)
        else:
            entry["rank"] = None
            entry["not_ranked_reason"] = (
                "称重记录不足两次或缺少投魂数量，无法计算饲料系数" if m["weigh_count"] < 2
                else "有效增重或饲料消耗为 0，无法计算饲料系数")
            insufficient.append(entry)
    ranked.sort(key=lambda x: x["fcr"])
    for i, e in enumerate(ranked, 1):
        e["rank"] = i
    return ok(ranked + insufficient, window_days=days, note=RANK_NOTE,
              ranked_count=len(ranked))
