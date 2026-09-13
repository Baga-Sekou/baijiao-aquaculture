"""内嵌终端仿真开关（演示用）。

打开：后端内启动一个仿真终端，自动应答回执 —— 投喂任务能顺畅跑完闭环。
关闭：不启动终端 —— 下发后收不到回执，复现「结果未知（待核查）」异常流程。

用途：答辩时可一键切换，对比「有终端」与「设备失联」两种情形。

注意：仿真终端不投任何真实饲料，其上报的「实际量」为模拟值，
      来源标记为 simulated，界面与统计不得当作实测数据。
"""
import os
import sys
import threading
import time

# simulator/ 位于项目根目录，不是 backend/ 下
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SIM_DIR = os.path.join(_PROJECT_ROOT, "simulator")
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)


def _terminal_cls():
    """延迟导入 Terminal：避免模块加载期就依赖 simulator 目录。"""
    from terminal import Terminal
    return Terminal

# 仿真终端默认参数（演示口径）
DEFAULT_POND_IDS = (1,)
DEFAULT_INTERVAL = 3.0

_state = {
    "running": False,
    "thread": None,
    "stop": None,
    "terminals": [],       # [(code, pond_id)]
    "interval": DEFAULT_INTERVAL,
    "started_at": None,
    "replies": 0,          # 粗略统计自动应答次数（由终端日志侧累计）
}


class _Stop:
    def __init__(self):
        self.flag = threading.Event()


def _run_terminals(codes_ponds, interval, stop, port):
    """在后台线程里跑若干仿真终端，直到 stop 被置位。"""
    from terminal import make_base
    Terminal = _terminal_cls()
    terms = []
    try:
        for code, pond_id in codes_ponds:
            t = Terminal(code, pond_id, interval=interval, base=make_base(port))
            if not t.register():
                print(f"[sim] 终端 {code} 注册失败，跳过")
                continue
            terms.append(t)
        if not terms:
            print("[sim] 没有终端启动成功")
            return
        print(f"[sim] 已启动 {len(terms)} 个仿真终端，间隔 {interval}s")
        while not stop.flag.is_set():
            for t in terms:
                t.heartbeat()
                t.collect_once()
                for task in t.poll_tasks():
                    t.handle_task(task)
            # 分段 sleep，便于快速停止
            for _ in range(int(interval * 10)):
                if stop.flag.is_set():
                    break
                time.sleep(0.1)
    except Exception as e:                       # noqa: BLE001
        print(f"[sim] 运行异常：{e}")
    finally:
        for t in terms:
            try:
                t.stop()
            except Exception:                     # noqa: BLE001
                pass
        with _state_lock:
            _state["running"] = False
            _state["thread"] = None
            _state["stop"] = None
            _state["terminals"] = []
        print("[sim] 仿真终端已停止")


_state_lock = threading.Lock()


def start(port, pond_ids=DEFAULT_POND_IDS, interval=DEFAULT_INTERVAL):
    """启动内嵌仿真终端。已在运行则返回说明。"""
    with _state_lock:
        if _state["running"]:
            return False, "仿真终端已在运行"
        stop = _Stop()
        codes_ponds = [(f"TERM-{_pond_code(pid)}", pid) for pid in pond_ids]
        th = threading.Thread(target=_run_terminals,
                              args=(codes_ponds, interval, stop, port),
                              daemon=True, name="embedded-simulator")
        _state.update(running=True, thread=th, stop=stop,
                      terminals=codes_ponds, interval=interval,
                      started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        th.start()
    return True, f"仿真终端已启动：{', '.join(c for c, _ in codes_ponds)}"


def _pond_code(pond_id):
    """鱼塘 id -> 塘编号（A01/A02...）。查库，取不到就退回 P<id>。"""
    try:
        from database import SessionLocal
        from models import Pond
        db = SessionLocal()
        try:
            p = db.query(Pond).filter_by(id=pond_id).first()
            return p.code if p else f"P{pond_id}"
        finally:
            db.close()
    except Exception:                             # noqa: BLE001
        return f"P{pond_id}"


def stop():
    """停止内嵌仿真终端。"""
    with _state_lock:
        if not _state["running"]:
            return False, "仿真终端未在运行"
        if _state["stop"]:
            _state["stop"].flag.set()
    return True, "已发出停止指令"


def status():
    """返回当前开关状态。"""
    with _state_lock:
        return {
            "running": _state["running"],
            "terminals": [{"code": c, "pond_id": p} for c, p in _state["terminals"]],
            "interval": _state["interval"],
            "started_at": _state["started_at"],
        }
