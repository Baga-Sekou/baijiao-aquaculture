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


def _base_url(port):
    """内嵌仿真回调本机服务的基址（与 simulator.terminal.make_base 同义）。"""
    from terminal import make_base
    return make_base(port)

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
    # 本机服务基址：即使从未 start 过，也要能对残留的在线终端补发下线
    "base": f"http://127.0.0.1:{os.getenv('APP_PORT', '5000')}",
}


class _Stop:
    def __init__(self):
        self.flag = threading.Event()


def _run_terminals(codes_ponds, interval, stop, port):
    """在后台线程里跑若干仿真终端，直到 stop 被置位。"""
    Terminal = _terminal_cls()
    terms = []
    try:
        for code, pond_id in codes_ponds:
            t = Terminal(code, pond_id, interval=interval, base=_base_url(port))
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
                      base=_base_url(port),
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


def stop(wait_sec=8.0):
    """停止内嵌仿真终端，确保所有终端都置为离线后再返回。

    只设标志就返回会让调用方紧接着查到的状态仍是 running=True
    （界面表现为「点了关闭但按钮还是绿的」），因此这里 join 线程。

    随后再做一次**兜底下线**：线程退出时逐个 POST /offline，
    若某个请求失败或超时，就会出现「一个终端已离线、另一个仍在线」的不一致。
    这里不论线程状态如何都对全部终端补发一次下线（接口幂等），
    也用于清理「启动失败但终端已被注册为在线」的残留状态。
    """
    with _state_lock:
        stop_obj = _state["stop"]
        th = _state["thread"]
        pairs = list(_state["terminals"])
        running = _state["running"]
        if stop_obj:
            stop_obj.flag.set()

    # 未在运行且无已知终端：退化为按鱼塘推断终端编号，清理残留在线状态
    if not running and not pairs:
        pairs = _known_pairs()

    # 在锁外等待，避免线程退出时抢锁造成死锁
    if th is not None:
        th.join(timeout=wait_sec)

    # 兜底：无论线程是否及时退出，都确保终端置为离线
    leftovers = _offline_all(pairs)

    if not running:
        msg = "仿真终端已停止"
        if leftovers:
            msg += f"（{', '.join(leftovers)} 下线未确认）"
        return False, msg
    if th is not None and th.is_alive():
        return True, "已发出停止指令，终端仍在退出中"
    if leftovers:
        return True, f"仿真终端已关闭（{leftovers} 个终端下线确认较慢）"
    return True, "仿真终端已关闭"


def _known_pairs():
    """按数据库里的鱼塘推断终端编号，用于清理残留的在线状态。"""
    try:
        from database import SessionLocal
        from models import Terminal
        db = SessionLocal()
        try:
            return [(t.code, t.pond_id) for t in db.query(Terminal).all()]
        finally:
            db.close()
    except Exception:                             # noqa: BLE001
        return []


def _offline_all(pairs):
    """对给定终端逐个 POST /offline（幂等）。返回未能确认下线的终端编号。"""
    import requests as _rq

    base = _state.get("base")
    if not base:
        return [c for c, _ in (pairs or [])]

    failed = []
    for code, _pond in pairs or []:
        try:
            r = _rq.post(f"{base}/api/terminals/{code}/offline", timeout=3)
            if not r.ok:
                failed.append(code)
        except _rq.RequestException:
            failed.append(code)
    return failed


def status():
    """返回当前开关状态。"""
    with _state_lock:
        return {
            "running": _state["running"],
            "terminals": [{"code": c, "pond_id": p} for c, p in _state["terminals"]],
            "interval": _state["interval"],
            "started_at": _state["started_at"],
        }
