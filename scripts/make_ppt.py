"""生成《白蕉水产养殖管理平台》介绍 PPT（含界面截图与运行数据）。

用法：python scripts/make_ppt.py
输出：docs/白蕉水产养殖管理平台_介绍.pptx

截图来源：docs/ppt_shots/（由 Edge headless 抓取的真实界面）
数字来源：实际运行结果与 tests/ 记录，非估计值。
"""
import os

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "ppt_shots")
OUT = os.path.join(ROOT, "docs", "白蕉水产养殖管理平台_介绍.pptx")

# 取自界面实际配色
DARK = RGBColor(0x20, 0x3E, 0x34)      # 侧边栏深绿
PRIMARY = RGBColor(0x32, 0x76, 0x5C)   # 主按钮绿
LIGHT = RGBColor(0xE8, 0xF0, 0xEC)     # 浅底
INK = RGBColor(0x22, 0x30, 0x3C)       # 正文
MUTED = RGBColor(0x6B, 0x7C, 0x8C)     # 次要
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
ACCENT = RGBColor(0xC5, 0x5A, 0x11)    # 强调橙

CN = "微软雅黑"
W, H = Inches(13.333), Inches(7.5)

prs = Presentation()
prs.slide_width, prs.slide_height = W, H
BLANK = prs.slide_layouts[6]


def cn(run, size=18, bold=False, color=INK, name=CN):
    """设置中英文字体（中文需单独设 a:ea，否则可能回退成宋体）。"""
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = name
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", name)


def box(slide, x, y, w, h, text="", size=18, bold=False, color=INK,
        align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, line=1.25, space=6):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    if text:
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            p.line_spacing = line
            p.space_after = Pt(space)
            cn(p.add_run(), size)          # 占位，下面替换
            p.runs[0].text = ln
            cn(p.runs[0], size, bold, color)
    return tb


def rect(slide, x, y, w, h, fill, line=None):
    from pptx.enum.shapes import MSO_SHAPE
    sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
    sh.shadow.inherit = False
    return sh


def pic(slide, name, x, y, max_w, max_h):
    """按等比缩放贴图，居中在给定框内。"""
    path = os.path.join(SHOTS, name)
    if not os.path.exists(path):
        box(slide, x, y, max_w, max_h, f"[缺图 {name}]", 14, color=MUTED)
        return
    from PIL import Image
    iw, ih = Image.open(path).size
    scale = min(max_w / iw, max_h / ih)
    w, h = int(iw * scale), int(ih * scale)
    slide.shapes.add_picture(path, int(x + (max_w - w) / 2), int(y + (max_h - h) / 2),
                             width=w, height=h)


def head(slide, kicker, title, sub=None):
    """统一页头：小标签 + 标题 + 可选副标题。"""
    rect(slide, 0, 0, W, Inches(1.15), DARK)
    box(slide, Inches(0.6), Inches(0.14), Inches(11), Inches(0.32),
        kicker, 11, True, RGBColor(0x9C, 0xC2, 0xAE))
    box(slide, Inches(0.6), Inches(0.42), Inches(12), Inches(0.52),
        title, 26, True, WHITE)
    if sub:
        box(slide, Inches(0.6), Inches(1.32), Inches(12.1), Inches(0.4),
            sub, 13, color=MUTED)


def slide():
    return prs.slides.add_slide(BLANK)


# ============================================================ 1 封面
s = slide()
rect(s, 0, 0, W, H, DARK)
rect(s, 0, Inches(4.55), W, Inches(0.06), PRIMARY)
box(s, Inches(1.1), Inches(1.75), Inches(11), Inches(0.4),
    "软件实训课程项目汇报", 16, True, RGBColor(0x9C, 0xC2, 0xAE))
box(s, Inches(1.1), Inches(2.3), Inches(11.2), Inches(1.0),
    "白蕉水产养殖管理平台", 44, True, WHITE)
box(s, Inches(1.1), Inches(3.5), Inches(11), Inches(0.5),
    "环境采集 → 智能建议 → 人工确认 → 任务下发 → 回执核查", 18,
    color=RGBColor(0xC7, 0xDC, 0xD2))
box(s, Inches(1.1), Inches(4.05), Inches(11), Inches(0.4),
    "面向珠海斗门白蕉海鲈养殖 · 一条可核查、可演示的投喂业务闭环", 14,
    color=RGBColor(0x9C, 0xC2, 0xAE))
box(s, Inches(1.1), Inches(4.85), Inches(11), Inches(1.4),
    "智能渔业 · 3 组\n"
    "组员：李世泓、叶翔、李森、陈富基、陈海瀚、王延旭、杨耀浚、刘越、黄东旭\n"
    "指导教师：黄杰        2026 年 9 月",
    13, color=RGBColor(0xB9, 0xD1, 0xC6), line=1.5)
box(s, Inches(1.1), Inches(6.75), Inches(11), Inches(0.35),
    "演示数据与建议仅用于课程联调验证，不作为实际养殖依据", 11,
    color=RGBColor(0x7F, 0xA8, 0x93))

# ============================================================ 2 定位
s = slide()
head(s, "01  POSITIONING", "这是个什么系统？",
     "给塘主和养殖员的「投喂决策辅助 + 执行留痕」工具，不是一个泛泛的数据大屏")
cards = [
    ("解决什么", "投喂靠经验、无记录、\n出问题查不清当时依据"),
    ("给谁用", "塘主 / 管理员 / 运营者\n三级角色 + 塘口授权"),
    ("怎么帮", "系统给建议 → 人工拍板 →\n执行留痕 → 异常有人管"),
    ("和别的不一样", "不堆功能，重点在\n每个环节「说得清、留得住」"),
]
x = Inches(0.6)
for t, d in cards:
    rect(s, x, Inches(2.0), Inches(2.95), Inches(2.3), LIGHT)
    box(s, x + Inches(0.24), Inches(2.2), Inches(2.5), Inches(0.4), t, 17, True, PRIMARY)
    box(s, x + Inches(0.24), Inches(2.8), Inches(2.5), Inches(1.3), d, 13, color=INK, line=1.45)
    x += Inches(3.1)
box(s, Inches(0.6), Inches(4.65), Inches(12.1), Inches(2.2),
    "四个「不」——把边界说在前面：\n"
    "· 不诊断疾病：不提供脂肪肝判定算法、投喂配方或水质阈值\n"
    "· 不替代人：最终是否投喂、投多少由养殖员确认\n"
    "· 不伪造数据：估算标估算、仿真标仿真、未知就显示未知\n"
    "· 不夸效果：减少浪费、提高收益需要另定基线另行验证",
    14, color=INK, line=1.6)

# ============================================================ 3 闭环
s = slide()
head(s, "02  WORKFLOW", "一条完整的投喂业务闭环", "每个环节都有明确的状态与留痕，异常分支同样可演示")
steps = [("环境采集", "水温/溶氧/pH\n来源与时效校验"),
         ("校验与建议", "数据有效才计算\n带依据与规则版本"),
         ("人工确认", "确认 / 调整 / 取消\n改量必须填原因"),
         ("任务下发", "唯一任务号\n设备占用与去重"),
         ("回执与核查", "完成/失败/未知\n未知须人工核查")]
x = Inches(0.55)
for i, (t, d) in enumerate(steps):
    rect(s, x, Inches(2.1), Inches(2.3), Inches(1.5), PRIMARY if i % 2 == 0 else DARK)
    box(s, x + Inches(0.12), Inches(2.28), Inches(2.06), Inches(0.4), t, 16, True, WHITE,
        align=PP_ALIGN.CENTER)
    box(s, x + Inches(0.12), Inches(2.75), Inches(2.06), Inches(0.7), d, 11,
        color=RGBColor(0xD8, 0xE8, 0xE0), align=PP_ALIGN.CENTER, line=1.3)
    if i < 4:
        box(s, x + Inches(2.3), Inches(2.6), Inches(0.25), Inches(0.5), "▶", 12, color=MUTED,
            align=PP_ALIGN.CENTER)
    x += Inches(2.55)
box(s, Inches(0.6), Inches(4.0), Inches(12.1), Inches(0.4),
    "异常分支同样有既定行为，不是「出错就完事」", 15, True, PRIMARY)
rows = [("数据失效 / 过期", "不产生可执行建议，列出需补录项"),
        ("设备离线 / 卡料", "暂停下发，记录原因，不静默改判为人工完成"),
        ("回执超时", "标记「结果未知」，保留设备占用，不自动重发"),
        ("重复点击 / 重复回执", "按请求号与任务号去重，不重复累计用料")]
y = Inches(4.55)
for a, b in rows:
    box(s, Inches(0.6), y, Inches(3.4), Inches(0.4), a, 14, True, INK)
    box(s, Inches(4.1), y, Inches(8.6), Inches(0.4), b, 14, color=MUTED)
    y += Inches(0.55)

# ============================================================ 4 三个量
s = slide()
head(s, "03  DATA DISCIPLINE", "三个量分列：谁建议的、谁改的、实际投了多少",
     "这是需求书反复强调的一条，也是很多「看起来能用」的系统最容易含糊的地方")
three = [("建议量", "系统生成", "按全塘口径计算：鱼重 × 尾数 × 比例，\n温度与摄食系数参与，\n保存输入快照与规则版本"),
         ("确认量", "人工拍板", "养殖员可现场调整数量，\n但必须填写修改原因，\n原建议与修改一并留痕"),
         ("实际量", "设备回执", "设备实测或人工填报，\n必须标明来源；无计量能力\n时显示「未获取」，\n不用确认量补齐")]
x = Inches(0.6)
for t, tag, d in three:
    rect(s, x, Inches(2.1), Inches(3.95), Inches(2.5), LIGHT)
    box(s, x + Inches(0.25), Inches(2.28), Inches(3.4), Inches(0.45), t, 20, True, PRIMARY)
    box(s, x + Inches(0.25), Inches(2.82), Inches(3.4), Inches(0.35), tag, 12, color=MUTED)
    box(s, x + Inches(0.25), Inches(3.22), Inches(3.5), Inches(1.35), d, 12, color=INK, line=1.4)
    x += Inches(4.15)
box(s, Inches(0.6), Inches(4.9), Inches(12.1), Inches(1.6),
    "为什么较真这件事：\n"
    "· 把估算当实测 → 汇总出来的用料、饲料系数全是假的，没法用来做成本核算\n"
    "· 把「指令已发」当「已经投了」 → 设备离线时会产生重复投喂\n"
    "· 没有来源标识 → 分不清是人填的还是机器测的，事后无法审计",
    14, color=INK, line=1.6)

# ============================================================ 5 运行截图：总览
s = slide()
head(s, "04  LIVE SCREENSHOTS", "养殖总览（真实运行界面）",
     "两个塘的环境、投喂、告警、终端状态一屏可见；右侧直接列出待办异常并可跳转核查")
pic(s, "home.png", Inches(0.55), Inches(1.75), Inches(12.2), Inches(5.5))

# ============================================================ 6 单塘
s = slide()
head(s, "05  POND WORKSPACE", "单塘看板：从环境到投喂的一站式操作",
     "仪表盘显示量程位置（非安全评级）；趋势图按实际采样时间绘制，超过 8 小时断线不连线")
pic(s, "pond_env.png", Inches(0.55), Inches(1.8), Inches(6.1), Inches(2.3))
pic(s, "pond_feed.png", Inches(6.85), Inches(1.8), Inches(5.9), Inches(2.3))
box(s, Inches(0.6), Inches(4.25), Inches(6.0), Inches(2.6),
    "环境监测\n"
    "· 水温 / 溶氧 / pH / ORP 四项，各自显示单位、采集时间、来源\n"
    "· 数据过期时明确标注「已过期」，不假装是当前值\n"
    "· 未接入的指标显示「未接入」，不填示例值\n\n"
    "今日运行\n"
    "· 今日已知实际量（按设备回执累计，不是按指令量）\n"
    "· 有多少个任务的实测量待核实，单独列出",
    13, color=INK, line=1.5)
box(s, Inches(6.85), Inches(4.25), Inches(5.9), Inches(2.6),
    "投喂建议与确认\n"
    "· 两条路径：大模型建议 / 规则建议（不调用大模型）\n"
    "· 建议卡片显示建议量、规则版本与完整依据\n"
    "· 大模型读取的是环境摘要，失败时明确提示，\n"
    "  不会自动用规则结果冒充大模型结果\n\n"
    "水温 / 溶氧趋势\n"
    "· 按真实采样时间定位，采样间隔异常会断线",
    13, color=INK, line=1.5)

# ============================================================ 7 任务记录
s = slide()
head(s, "06  EXECUTION RECORD", "投喂任务记录：三个量在两列里同时可见",
     "每行都能看到建议量 / 确认量 / 实际量，以及实际量的来源标记")
pic(s, "pond_tasks.png", Inches(0.55), Inches(1.8), Inches(12.2), Inches(3.0))
box(s, Inches(0.6), Inches(4.95), Inches(12.1), Inches(2.0),
    "几个细节：\n"
    "· 「实际量」列标着 measured / simulated —— 仿真终端产生的量不会冒充设备实测\n"
    "· 失败任务仍然显示「实际量 未获取」，保留失败前已知投出的量，不用确认量补齐\n"
    "· 任务状态由状态机推进：待下发 → 已下发待回执 → 执行中 → 已完成 / 失败 / 结果未知\n"
    "· 表格每 5 秒自动刷新，右上角提示「N 个任务占用设备」，卡住的任务一眼可见",
    13, color=INK, line=1.5)

# ============================================================ 8 异常闭环
s = slide()
head(s, "07  EXCEPTION PATH", "设备失联时：不盲目重发，先转待核查",
     "页面右上角「终端仿真」开关：开 → 正常闭环；关 → 复现设备失联异常流程")
pic(s, "review_locked.png", Inches(0.55), Inches(1.8), Inches(8.3), Inches(4.0))
rect(s, Inches(9.1), Inches(1.8), Inches(3.65), Inches(4.0), LIGHT)
box(s, Inches(9.3), Inches(1.92), Inches(3.3), Inches(3.8),
    "任务 TK-0054\n\n"
    "状态：结果未知（待核查）\n"
    "建议 / 确认 / 实际\n"
    "379.469 / 379.469 / 未获取\n"
    "设备占用：占用中　回执：无\n\n"
    "系统做了什么：\n"
    "· 超时后自动转入待核查\n"
    "· 保留设备占用，拒绝新任务\n"
    "· 须人工核查并逐项确认\n"
    "  （终端无任务 / 旧指令不再\n"
    "  执行 / 设备可用）才解除占用",
    11.5, color=INK, line=1.35, space=3)
box(s, Inches(0.6), Inches(5.95), Inches(12.1), Inches(1.2),
    "为什么这么设计：设备是否真的停了、投了多少，系统无法自己证明。"
    "盲目重发会导致重复投料——这是比「少喂一次」严重得多的事故。"
    "所以宁可卡住等人核实，也不自动恢复。",
    13.5, color=ACCENT, line=1.5)

# ============================================================ 9 AI
s = slide()
head(s, "08  AI MODELS", "可解释的 AI：真训练、带指标、讲边界",
     "生长 / 投喂量 / 水质三组模型，输出都带版本、样本数与误差指标")
pic(s, "pond_ai.png", Inches(0.55), Inches(1.8), Inches(8.3), Inches(4.4))
rect(s, Inches(9.1), Inches(1.8), Inches(3.65), Inches(4.4), LIGHT)
box(s, Inches(9.35), Inches(1.98), Inches(3.2), Inches(4.1),
    "实际运行结果\n\n"
    "生长预测\n"
    "· 当前估计 780.8 g\n"
    "· 生长速度 2.641 g/天\n"
    "· 距目标 8 天\n"
    "· 模型 R² = 0.9965\n\n"
    "投喂量预测\n"
    "· 日投喂量 ~ 生物量 + 日均温\n"
    "· 如实报出较低 R²，不粉饰\n\n"
    "水质预测\n"
    "· 72 小时趋势外推，仅 ≤24h",
    12.5, color=INK, line=1.45)
box(s, Inches(0.6), Inches(6.35), Inches(12.1), Inches(0.9),
    "数据不足时明确拒绝，不返回伪预测——例如某塘称重记录不足两次，"
    "饲料系数直接显示「暂不能计算」而不是给个凑出来的数。",
    13.5, color=ACCENT)

# ============================================================ 10 视觉 + 监控
s = slide()
head(s, "09  VISION & MONITOR", "塘口监控与疑似死鱼事件",
     "识别结果先作为「待确认事件」，人工确认前不计入已确认数量")
pic(s, "pond_monitor.png", Inches(0.55), Inches(1.8), Inches(5.9), Inches(3.0))
pic(s, "pond_ai.png", Inches(6.85), Inches(1.8), Inches(5.9), Inches(3.0))
box(s, Inches(0.6), Inches(5.0), Inches(6.0), Inches(2.0),
    "塘口监控\n"
    "· 相机离线时明确显示「画面不可用」，不用旧截图冒充正常\n"
    "· 支持浏览器摄像头拍照 / 图片上传 / OCR\n"
    "· OCR 依赖本机安装 tesseract，未装时明确提示，不做假识别",
    13, color=INK, line=1.5)
box(s, Inches(6.85), Inches(5.0), Inches(5.9), Inches(2.0),
    "疑似死鱼事件\n"
    "· 编号 / 状态 / 来源 / 置信度 / 位置 / 发现时间\n"
    "· 状态流转：待确认 → 处理中 → 已处理 / 误报\n"
    "· 误报与已处理为终态，不可互相改写而丢失分类\n"
    "· 确认死鱼事件本身不等于确定病因，页面不生成诊断结论",
    13, color=INK, line=1.5)

# ============================================================ 11 多塘 + 交流
s = slide()
head(s, "10  COMPARISON & COMMUNITY", "多塘比较与养殖交流",
     "同一时间范围对照水质、告警、用药与饲料；数据条件不同的塘不直接判定优劣")
pic(s, "compare.png", Inches(0.55), Inches(1.8), Inches(6.6), Inches(4.2))
pic(s, "community.png", Inches(7.35), Inches(1.8), Inches(5.4), Inches(4.2))
box(s, Inches(0.6), Inches(6.15), Inches(12.1), Inches(1.1),
    "饲料系数 FCR = 区间内饲料消耗 / 同期鱼体增重，实测 1.29，"
    "落在真实鲈鱼范围（约 1.2~1.8）；分子分母覆盖相同时间区间，"
    "区间之后的投喂不计入。A02 因称重数据不足，明确标「不参与排名」而不凑名次。",
    13, color=INK, line=1.5)

# ============================================================ 12 质量
s = slide()
head(s, "11  QUALITY", "质量有据可查", "所有数字来自实际运行与自动化测试，不是估计值")
stats = [("28 / 28", "验收场景自动化通过", "含越权、乱序回执、并发、模型、导入"),
         ("9 / 9", "投喂安全回归", "停止链路、重启去重、实际用料、核查约束"),
         ("9 / 9", "大模型适配器", "缺密钥、超时、限流、异常输出、并发"),
         ("2", "数据库模式", "SQLite 与 MySQL 各连跑 3 次全通过"),
         ("605 KB", "代码仓库体积", "87 个文件，无产物与密钥入库")]
y = Inches(1.95)
for n, t, d in stats:
    rect(s, Inches(0.6), y, Inches(12.1), Inches(0.85), LIGHT)
    box(s, Inches(0.85), y + Inches(0.12), Inches(2.0), Inches(0.6), n, 22, True, PRIMARY)
    box(s, Inches(3.0), y + Inches(0.14), Inches(3.6), Inches(0.5), t, 15, True, INK)
    box(s, Inches(6.8), y + Inches(0.18), Inches(5.7), Inches(0.5), d, 12.5, color=MUTED)
    y += Inches(0.98)
box(s, Inches(0.6), Inches(6.85), Inches(12.1), Inches(0.5),
    "验收脚本默认跑在独立临时库上，不会弄乱演示数据；换台电脑只需 Python，"
    "无需安装数据库即可运行。", 12.5, color=MUTED)

# ============================================================ 13 边界
s = slide()
head(s, "12  HONEST LIMITS", "诚实的边界", "这些不是遗漏，是主动声明的范围")
items = [("真实硬件", "未接入", "传感器 / 相机 / 投料机用仿真终端验证同一套流程，需求书 2.3 允许"),
         ("深度学习视觉", "未实现", "当前是 OpenCV 启发式算法，反光水草会误报，结果需人工确认"),
         ("识别准确率", "未评估", "没有标注数据集，无法给出准确率——不编一个数字"),
         ("微信小程序", "未真机运行", "代码完整、接口已用脚本预检通过，渲染与触控需人工确认"),
         ("身份认证", "演示级", "用请求头 X-User 传身份，未做密码与会话体系，不可用于公开部署")]
y = Inches(1.95)
for t, st, d in items:
    rect(s, Inches(0.6), y, Inches(12.1), Inches(0.9), LIGHT)
    box(s, Inches(0.85), y + Inches(0.15), Inches(2.3), Inches(0.5), t, 15, True, INK)
    box(s, Inches(3.3), y + Inches(0.15), Inches(1.9), Inches(0.5), st, 13, True, ACCENT)
    box(s, Inches(5.4), y + Inches(0.15), Inches(7.1), Inches(0.6), d, 12, color=MUTED)
    y += Inches(1.02)
box(s, Inches(0.6), Inches(7.0), Inches(12.1), Inches(0.4),
    "答辩时被问到「这个做了吗」，如实回答比含糊带过稳妥。", 12.5, color=MUTED)

# ============================================================ 14 结尾
s = slide()
rect(s, 0, 0, W, H, DARK)
box(s, Inches(1.1), Inches(2.2), Inches(11), Inches(0.8),
    "谢谢各位老师", 40, True, WHITE)
box(s, Inches(1.1), Inches(3.2), Inches(11), Inches(0.5),
    "恳请批评指正 · 欢迎现场演示与提问", 18, color=RGBColor(0xC7, 0xDC, 0xD2))
rect(s, Inches(1.1), Inches(4.0), Inches(3.0), Inches(0.05), PRIMARY)
box(s, Inches(1.1), Inches(4.35), Inches(11), Inches(1.5),
    "现场演示：双击 start.bat   →   http://127.0.0.1:5000\n"
    "自动化验收：python tests/test_acceptance.py（隔离模式，不影响演示数据）\n"
    "源代码仓库：https://github.com/Baga-Sekou/baijiao-aquaculture",
    14, color=RGBColor(0xB9, 0xD1, 0xC6), line=1.6)
box(s, Inches(1.1), Inches(6.4), Inches(11), Inches(0.4),
    "演示数据与建议仅用于课程联调验证，不作为实际养殖依据", 11,
    color=RGBColor(0x7F, 0xA8, 0x93))

prs.save(OUT)
print(f"[ok] 已生成 {OUT}")
print(f"     共 {len(prs.slides.__iter__.__self__._sldIdLst)} 页")
