"""后台定时任务：自动执行回执超时巡检。

需求书 5.4 要求「设备接收任务后失联要能进入待核查闭环」。此前超时巡检
只能由待核查页手动触发，设备失联后要人工点一下才会转状态，不符合
「异常应有既定系统行为」的要求。

这里用守护线程按固定间隔自动调用 task_service.check_timeouts。
间隔由环境变量 TIMEOUT_SWEEP_SEC 控制：
    >0  启用（默认 5 秒，与默认 7 秒超时阈值配套，演示时约 10 秒内可见）
    =0  禁用 —— 验收脚本等需要精确控制时序的场景用这个，避免后台巡检
        抢先把任务转成 unknown，导致「回执超时」用例的断言不稳定。

注意：实际转入待核查的耗时 ≈ 超时阈值 + 巡检间隔，
调大 task_timeout_sec 时建议同步调大本间隔，避免无谓的空转查询。
"""
import os
import threading
import time

_interval = float(os.getenv("TIMEOUT_SWEEP_SEC", "5"))
_started = False
_lock = threading.Lock()


def enabled():
    return _interval > 0


def _loop():
    from database import SessionLocal
    from services.task_service import check_timeouts

    print(f"[sweeper] 超时巡检已启用，间隔 {_interval:g}s")
    while True:
        time.sleep(_interval)
        db = SessionLocal()
        try:
            n = check_timeouts(db)
            if n:
                print(f"[sweeper] 巡检：{n} 个任务转入待核查")
        except Exception as e:            # noqa: BLE001
            # 巡检失败不应终止守护线程，也不应影响接口服务
            print(f"[sweeper] 巡检异常（已忽略）: {e}")
        finally:
            db.close()


def start():
    """启动守护线程。禁用或已启动时为空操作。"""
    global _started
    if not enabled():
        print("[sweeper] 超时巡检未启用（TIMEOUT_SWEEP_SEC=0），仅支持手动触发")
        return False
    with _lock:
        if _started:
            return False
        t = threading.Thread(target=_loop, daemon=True, name="timeout-sweeper")
        t.start()
        _started = True
    return True
