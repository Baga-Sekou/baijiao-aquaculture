"""领域模型（对应需求说明书 7.1「主要业务记录」）。

设计要点：
- 建议量 / 确认量 / 实际量三者分列保存，实际量带来源标识，不用确认量补齐。
- 任务带唯一任务号与请求编号，请求编号用于幂等去重。
- 核查（review）独立成表，保留结论、依据与设备恢复依据。
- 审计日志覆盖校验失败、任务下发与反馈、控制操作与参数变更。
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


def now():
    return datetime.now()


# ---------------------------------------------------------------- 组织与权限
class User(Base):
    """用户。角色：塘主 owner / 管理员 admin / 运营者（养殖员）operator。"""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    display_name = Column(String(64), nullable=False)
    role = Column(String(16), nullable=False)          # owner / admin / operator
    password_hash = Column(String(128))
    phone = Column(String(32))
    created_at = Column(DateTime, default=now)


class PondGrant(Base):
    """鱼塘授权：养殖员只能操作获授权鱼塘（非功能需求 8.1）。"""
    __tablename__ = "pond_grants"
    __table_args__ = (UniqueConstraint("user_id", "pond_id", name="uq_user_pond"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    can_control = Column(Boolean, default=True)        # 是否可下发投喂
    can_review = Column(Boolean, default=False)        # 是否可做投喂核查
    created_at = Column(DateTime, default=now)


# ---------------------------------------------------------------- 鱼塘与批次
class Pond(Base):
    """鱼塘。面积等带量纲信息同时保存单位。"""
    __tablename__ = "ponds"
    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, nullable=False)   # 鱼塘编号，唯一识别
    name = Column(String(64), nullable=False)
    area = Column(Float)
    area_unit = Column(String(16), default="亩")
    owner_id = Column(Integer, ForeignKey("users.id"))
    location = Column(String(128))
    created_at = Column(DateTime, default=now)

    batches = relationship("Batch", back_populates="pond", cascade="all, delete-orphan")


class Batch(Base):
    """养殖批次。同一鱼塘按养殖周期建批次，避免跨批次接生长曲线。"""
    __tablename__ = "batches"
    id = Column(Integer, primary_key=True)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    code = Column(String(32), nullable=False)
    species = Column(String(64), default="海鲈")
    fry_source = Column(String(128))                    # 种苗来源
    stock_date = Column(DateTime)                       # 投放日期
    fish_number = Column(Integer)                       # 投放鱼数量
    initial_weight = Column(Float)                      # 初始鱼重(g)
    target_weight = Column(Float)                       # 目标鱼重(g)
    weigh_cycle_days = Column(Integer)                  # 称重周期
    plan_harvest_date = Column(DateTime)                # 预计上市日期
    actual_harvest_date = Column(DateTime)              # 实际完成日期
    status = Column(String(16), default="active")       # active / closed
    src_note = Column(String(128))                      # 资料来源
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    pond = relationship("Pond", back_populates="batches")


# ---------------------------------------------------------------- 设备与终端
class Device(Base):
    """硬件设备：传感器 / 相机 / 投料机。"""
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    pond_id = Column(Integer, ForeignKey("ponds.id"))
    kind = Column(String(24), nullable=False)     # sensor / camera / feeder
    metric = Column(String(32))                   # 传感器指标: temperature/oxygen/ph/orp
    unit = Column(String(16))
    location = Column(String(32))                 # 水上 / 水下
    status = Column(String(16), default="offline")  # online / offline
    created_at = Column(DateTime, default=now)


class Terminal(Base):
    """采集控制终端。同一终端身份在重复连接时更新状态，不产生错误鱼塘归属。"""
    __tablename__ = "terminals"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    pond_id = Column(Integer, ForeignKey("ponds.id"))
    ip = Column(String(64))
    status = Column(String(16), default="offline")   # online / offline
    last_seen = Column(DateTime)
    connected_at = Column(DateTime)
    created_at = Column(DateTime, default=now)


# ---------------------------------------------------------------- 环境与摄食
class Measurement(Base):
    """环境测量。保留原始记录；断线补传区分采集时间与接收时间。"""
    __tablename__ = "measurements"
    __table_args__ = (
        Index("ix_meas_pond_time", "pond_id", "collected_at"),
    )
    id = Column(Integer, primary_key=True)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"))
    terminal_id = Column(Integer, ForeignKey("terminals.id"))
    batch_id = Column(Integer, ForeignKey("batches.id"))
    metric = Column(String(32), nullable=False)      # temperature/oxygen/ph/orp/salinity
    value = Column(Float, nullable=False)
    unit = Column(String(16), nullable=False)
    collected_at = Column(DateTime, nullable=False)  # 采集时间
    received_at = Column(DateTime, default=now)      # 接收时间
    valid = Column(Boolean, default=True)            # 有效性
    invalid_reason = Column(String(64))              # 超量程 / 漂移 / 离线
    source = Column(String(16), default="real")      # real / sim / manual
    src_record = Column(String(64))                  # 导入来源记录编号


class FeedingFeedback(Base):
    """摄食状态反馈。原始信号、算法饥饿度、人工判断分别保存。"""
    __tablename__ = "feeding_feedback"
    id = Column(Integer, primary_key=True)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"))
    round_no = Column(String(32))
    state = Column(String(16), nullable=False)       # hungry/normal/full/unknown
    source = Column(String(16), default="manual")    # manual / vision / sensor
    observed_at = Column(DateTime, default=now)
    valid = Column(Boolean, default=True)


# ---------------------------------------------------------------- 建议
class Suggestion(Base):
    """投喂建议。含建议量、单位、依据、规则版本与有效期限。"""
    __tablename__ = "suggestions"
    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"))
    round_no = Column(String(32))
    status = Column(String(16), default="pending")   # pending/confirmed/cancelled/expired
    amount = Column(Float)                           # 建议量
    unit = Column(String(16), default="kg")
    reason = Column(Text)                            # 依据说明（可追溯）
    rule_version = Column(String(32))
    metric_version = Column(String(32))
    inputs_json = Column(Text)                       # 输入快照
    generated_at = Column(DateTime, default=now)
    valid_until = Column(DateTime)                   # 有效截止时间
    fail_reason = Column(String(128))                # 计算失败原因；失败不返回 0


# ---------------------------------------------------------------- 任务与回执
class FeedingTask(Base):
    """投喂任务。建议量/确认量/实际量分列；实际量带来源。"""
    __tablename__ = "feeding_tasks"
    __table_args__ = (
        UniqueConstraint("request_no", name="uq_task_request"),
        Index("ix_task_pond_status", "pond_id", "status"),
    )
    id = Column(Integer, primary_key=True)
    task_no = Column(String(40), unique=True, nullable=False)   # 唯一任务号
    request_no = Column(String(40), nullable=False)             # 客户端请求编号（幂等）
    suggestion_id = Column(Integer, ForeignKey("suggestions.id"))
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"))
    device_id = Column(Integer, ForeignKey("devices.id"))
    terminal_id = Column(Integer, ForeignKey("terminals.id"))
    round_no = Column(String(32))

    suggested_amount = Column(Float)                 # 建议量
    confirm_amount = Column(Float)                   # 确认量
    actual_amount = Column(Float)                    # 实际量
    actual_source = Column(String(16))               # measured / manual / unknown
    unit = Column(String(16), default="kg")
    change_reason = Column(String(255))              # 修改原因

    status = Column(String(24), default="pending")   # 见 TASK_STATUS
    # pending 待下发 / dispatched 已下发待回执 / running 执行中 /
    # done 已完成 / stopped 已停止 / failed 失败 / unknown 结果未知(待核查) / cancelled
    approved_by = Column(Integer, ForeignKey("users.id"))
    approved_at = Column(DateTime)
    dispatched_at = Column(DateTime)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    device_locked = Column(Boolean, default=True)    # 设备占用
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    receipts = relationship("Receipt", back_populates="task", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="task", cascade="all, delete-orphan")


class Receipt(Base):
    """回执。迟到/重复回执关联原任务保存，不重复累计用料。"""
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("feeding_tasks.id"), nullable=False)
    kind = Column(String(16), nullable=False)        # accept / execute / finish / stop
    status = Column(String(24), nullable=False)
    actual_amount = Column(Float)
    fault = Column(String(64))                       # 卡料 / 失联
    raw = Column(Text)
    seq = Column(Integer, default=0)                 # 同一任务内回执序号
    occurred_at = Column(DateTime, default=now)      # 设备侧发生时间
    received_at = Column(DateTime, default=now)      # 后端接收时间
    late = Column(Boolean, default=False)            # 是否迟到回执
    duplicate = Column(Boolean, default=False)       # 是否重复回执

    task = relationship("FeedingTask", back_populates="receipts")


class Review(Base):
    """投喂核查。结论：done/stopped/failed/not_executed/unknown。"""
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("feeding_tasks.id"), nullable=False)
    reviewer_id = Column(Integer, ForeignKey("users.id"))
    conclusion = Column(String(24), nullable=False)  # done/stopped/failed/not_executed/unknown
    basis = Column(Text)                             # 依据（终端记录 / 现场证据）
    evidence = Column(Text)                          # 现场证据说明
    device_recovery = Column(Text)                   # 设备恢复依据
    conflict = Column(Boolean, default=False)        # 与迟到回执冲突
    created_at = Column(DateTime, default=now)

    task = relationship("FeedingTask", back_populates="reviews")


# ---------------------------------------------------------------- 视觉与事件
class CameraView(Base):
    """视觉通道状态。画面加载失败须给出提示，不用旧截图冒充正常。"""
    __tablename__ = "camera_views"
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    position = Column(String(16))                    # 水上 / 水下
    online = Column(Boolean, default=False)
    last_frame_at = Column(DateTime)
    image_path = Column(String(255))
    note = Column(String(255))


class FishEvent(Base):
    """疑似死鱼事件。识别结果作为待确认事件，人工确认前不计入已确认数量。"""
    __tablename__ = "fish_events"
    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    kind = Column(String(24), default="suspected_death")
    status = Column(String(16), default="pending")   # pending/processing/handled/false_alarm
    detected_at = Column(DateTime, default=now)      # 发现时间
    position_desc = Column(String(128))
    observation = Column(Text)
    image_path = Column(String(255))
    source = Column(String(16), default="manual")    # manual / vision
    model_version = Column(String(32))
    confidence = Column(Float)
    confirmed_by = Column(Integer, ForeignKey("users.id"))
    confirmed_at = Column(DateTime)
    handler_note = Column(Text)
    created_at = Column(DateTime, default=now)


class Alert(Base):
    """告警 / 异常事件。同一设备持续异常关联为持续事件。"""
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False)
    pond_id = Column(Integer, ForeignKey("ponds.id"))
    device_id = Column(Integer, ForeignKey("devices.id"))
    task_id = Column(Integer, ForeignKey("feeding_tasks.id"))
    kind = Column(String(32), nullable=False)        # data_invalid/device_offline/feed_fail/death
    level = Column(String(12), default="warning")
    status = Column(String(16), default="open")      # open/ack/processing/closed
    message = Column(Text)
    is_ongoing = Column(Boolean, default=False)      # 持续事件
    occurrence_count = Column(Integer, default=1)
    handled_by = Column(Integer, ForeignKey("users.id"))
    handled_at = Column(DateTime)
    resolution = Column(Text)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


# ---------------------------------------------------------------- 运营与日志
class PlanTask(Base):
    """养殖计划任务。"""
    __tablename__ = "plan_tasks"
    id = Column(Integer, primary_key=True)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    cycle = Column(String(16), default="daily")      # daily / weekly
    content = Column(String(255), nullable=False)
    assignee_id = Column(Integer, ForeignKey("users.id"))
    plan_time = Column(DateTime)
    due_time = Column(DateTime)
    completed = Column(Boolean, default=False)
    completed_at = Column(DateTime)
    overdue = Column(Boolean, default=False)
    created_at = Column(DateTime, default=now)


class MedicineRecord(Base):
    """用药记录。只保存实际操作，不生成诊断或剂量建议。"""
    __tablename__ = "medicine_records"
    id = Column(Integer, primary_key=True)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"))
    used_at = Column(DateTime, default=now)
    item = Column(String(128), nullable=False)
    amount = Column(Float)
    unit = Column(String(16))
    operator_id = Column(Integer, ForeignKey("users.id"))
    note = Column(String(255))


class WeighRecord(Base):
    """称重记录，用于增重口径与饲料系数。"""
    __tablename__ = "weigh_records"
    id = Column(Integer, primary_key=True)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"))
    weighed_at = Column(DateTime, default=now)
    avg_weight = Column(Float)
    sample_count = Column(Integer)
    operator_id = Column(Integer, ForeignKey("users.id"))
    note = Column(String(255))


class CostRecord(Base):
    """成本记录。区分预计成本 / 实际支出 / 预计产出。"""
    __tablename__ = "cost_records"
    id = Column(Integer, primary_key=True)
    pond_id = Column(Integer, ForeignKey("ponds.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"))
    kind = Column(String(16), default="actual")      # plan / actual / output
    item = Column(String(128))
    amount = Column(Float)
    unit = Column(String(16))
    brand = Column(String(128))
    occurred_at = Column(DateTime, default=now)
    note = Column(String(255))


class AuditLog(Base):
    """审计日志。覆盖数据校验失败、任务下发与反馈、控制操作、参数变更。"""
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_log_time", "created_at"),
    )
    id = Column(Integer, primary_key=True)
    category = Column(String(32), nullable=False)    # validate/task/control/config/auth
    action = Column(String(64), nullable=False)
    pond_id = Column(Integer, ForeignKey("ponds.id"))
    device_id = Column(Integer, ForeignKey("devices.id"))
    task_id = Column(Integer, ForeignKey("feeding_tasks.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    detail = Column(Text)                            # 不含明文密码等认证信息
    created_at = Column(DateTime, default=now)


class SysConfig(Base):
    """系统配置。采样周期、数据有效期、量程、任务超时、规则版本统一配置，变更留痕。"""
    __tablename__ = "sys_config"
    __table_args__ = (UniqueConstraint("key", name="uq_cfg_key"),)
    id = Column(Integer, primary_key=True)
    key = Column(String(64), nullable=False)
    value = Column(String(255), nullable=False)
    unit = Column(String(16))
    note = Column(String(255))
    effective_at = Column(DateTime, default=now)
    updated_by = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(DateTime, default=now, onupdate=now)


# ---------------------------------------------------------------- 交流与排行榜（需求书 5.11 扩展）
class CommunityPost(Base):
    """交流帖。养殖经验交流；排行榜数据另行实时计算，不落库。"""
    __tablename__ = "community_posts"
    id = Column(Integer, primary_key=True)
    pond_id = Column(Integer, ForeignKey("ponds.id"))   # 可不关联鱼塘（纯经验交流）
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(128), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(24), default="经验交流")    # 经验交流 / 问题求助 / 行情信息
    status = Column(String(16), default="open")          # open / closed
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    replies = relationship("PostReply", back_populates="post",
                           cascade="all, delete-orphan")


class PostReply(Base):
    """交流帖回复。"""
    __tablename__ = "post_replies"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("community_posts.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=now)

    post = relationship("CommunityPost", back_populates="replies")


class ImportBatch(Base):
    """CSV 导入批次，保留来源与源记录编号，避免两年 id 冲突。"""
    __tablename__ = "import_batches"
    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    rows_total = Column(Integer, default=0)
    rows_inserted = Column(Integer, default=0)
    rows_duplicated = Column(Integer, default=0)
    rows_conflict = Column(Integer, default=0)
    note = Column(String(255))
    created_at = Column(DateTime, default=now)
