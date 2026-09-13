# 白蕉水产养殖管理平台

软件实训课程作业（智能渔业-3组）。面向珠海斗门白蕉鲈鱼养殖场景，实现
「环境采集 → 后端校验与建议 → 人工确认 → 任务下发 → 设备回执 → 核查留痕」
的完整业务闭环，并针对数据失效、重复提交、回执超时、卡料、实际量未知等
异常分支提供可演示、可验证的处理。

> 本系统的数据与建议用于课程联调验证，**不得作为实际养殖依据**。

---

## 1. 快速开始

**推荐：双击 `start.bat`**（Windows）。首次运行会自动装依赖、建数据库、载入样例数据并打开浏览器。

手动启动见下方 1.1–1.6。

### 1.0 在别的电脑上使用（拷贝即用）

整个文件夹拷到另一台电脑后，只要那台电脑装了 **Python 3.10+**，双击 `start.bat` 即可，
**不需要安装 MySQL 或其他数据库**。

原因是本平台默认使用 **SQLite**（单文件数据库），数据库就是 `data/baijiao.db` 这一个文件。

```bash
# 先自检环境（会告诉你缺什么、怎么修）
python scripts/check_env.py
```

### 1.1 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | 3.10+（开发环境 3.14.5） | 唯一必需项 |
| 浏览器 | Edge / Chrome | 看板与移动端页面 |
| MySQL | 8.0（**可选**） | 仅在需要切回 MySQL 时需要 |

### 1.2 安装依赖

```bash
pip install -r requirements.txt
```

### 1.3 配置

默认无需任何配置。`.env` 中 `DB_URL=` 留空即使用 SQLite。

如需切回 MySQL（课程要求口径），在 `.env` 中这样写：

```ini
DB_URL=mysql+pymysql://root:你的密码@127.0.0.1:3306/baijiao_aquaculture?charset=utf8mb4
```

### 1.4 初始化数据库

```bash
python scripts/init_db.py --seed
```

### 1.5 导入课堂测试数据（可跳过）

项目已内置两份 CSV 于 `data/sample/`：

```bash
python scripts/import_csv.py --dir data/sample
```

（也可逐个指定文件路径；Windows 下命令行传中文文件名易因编码丢失，推荐 `--dir`。）

导入会保留原字段与来源文件，使用独立记录编号，避免两年重复的 `id` 相互覆盖；
重复导入同一文件不会重复写入。

### 1.6 启动后端

```bash
python backend/app.py
```

浏览器打开 <http://127.0.0.1:5000> 。左上角可切换身份（塘主 / 管理员 / 运营者），
用于演示三级权限。

> 想让手机在同一 WiFi 下访问：把 `.env` 里的 `APP_HOST` 改成 `0.0.0.0`，
> 手机访问 `http://本机局域网IP:5000`。

### 1.7 启动采集控制终端仿真器（可选，用于演示投喂闭环）

```bash
# 正常终端
python simulator/terminal.py --code TERM-A01 --pond 1

# 故障注入：卡料 / 实际量缺失 / 回执超时 / 不支持远程停止
python simulator/terminal.py --code TERM-A01 --pond 1 --fault stuck
```

### 1.8 生成演示数据与跑验收

```bash
python scripts/seed_demo.py        # 生成近 30 天环境曲线、若干投喂任务与异常
python tests/test_acceptance.py    # 跑第 9 章验收场景，输出 docs/验收测试记录.json
```

**验收脚本默认使用隔离模式**：在临时目录建专用测试库并拉起独立后端（端口 5057），
全程不触碰演示库 `data/baijiao.db`，跑完自动清理。因此可以随时跑验收而不丢演示数据。

如需对已启动的服务实测（会清空该服务的数据）：

```bash
python tests/test_acceptance.py --base http://127.0.0.1:5000
```

### 1.9 性能基准（需求书 8.4）

```bash
python scripts/benchmark.py --rows 200    # 需后端已启动；输出 docs/性能测试记录.md
```

报告包含：库内记录规模、单条/批量采样写入吞吐（条/秒）、常用查询接口
平均与 p95 耗时。

### 1.10 微信小程序（可选）

`miniprogram/` 为原生小程序脚手架：

1. 用微信开发者工具「导入项目」选择 `miniprogram/` 目录（测试号即可）；
2. 把 `miniprogram/utils/api.js` 里的 `BASE_URL` 改成电脑局域网 IP；
3. 后端 `.env` 设 `APP_HOST=0.0.0.0` 后重启，手机预览即可联调。

### 1.11 部署口径

默认 `APP_DEBUG=0`，`python backend/app.py` 以 **waitress**（多线程生产级 WSGI）
运行；仅本地开发设 `APP_DEBUG=1` 才进入 Flask 调试模式。

---

## 2. 目录结构

```
白蕉水产养殖管理平台/
├── start.bat                     一键启动（Windows：装依赖→建库→载数据→开浏览器）
├── .env                          DB_URL 留空=SQLite；填 MySQL 串=切回 MySQL
├── data/
│   ├── baijiao.db                SQLite 数据库文件（首次运行自动生成）
│   └── sample/                   内置的两年课堂测试 CSV
├── backend/
│   ├── app.py                    应用入口（Flask；APP_DEBUG=0 时用 waitress 运行）
│   ├── database.py               连接与会话（默认 SQLite，可切 MySQL）
│   ├── models.py                 领域模型（对应需求书 7.1 的业务记录）
│   ├── api/                      接口层
│   │   ├── ponds.py              鱼塘、批次、用户、授权
│   │   ├── env.py                环境测量、摄食反馈、终端注册
│   │   ├── feeding.py            建议、任务、回执、核查、超时巡检
│   │   ├── monitor.py            相机、帧上传识别、OCR、疑似死鱼事件、告警
│   │   ├── ops.py                看板、多塘比较、运营记录、日志、配置
│   │   ├── model.py              AI 模型训练与预测（生长/投喂量/水质）
│   │   └── community.py          交流帖与养殖排行榜（需求书 5.11）
│   ├── services/                 业务规则层
│   │   ├── common.py             编号、配置、审计、权限、时间解析
│   │   ├── environment.py        上传校验、有效性、日内曲线与统计特征
│   │   ├── suggestion.py         投喂建议生成（含演示规则与规则版本）
│   │   ├── task_service.py       任务幂等、设备占用、回执、超时、核查
│   │   ├── ml.py                 纯标准库线性回归模型（训练/预测/落盘）
│   │   └── vision.py             OpenCV 帧分析与可选 OCR
│   ├── templates/                Web 看板页面（响应式，兼顾移动端）
│   └── static/                   CSS / JS（无外部依赖，离线可演示）
├── miniprogram/                  微信小程序脚手架（鱼塘/单塘/交流三页）
├── simulator/terminal.py         采集控制终端仿真器（含故障注入）
├── scripts/
│   ├── check_env.py              环境自检（Python/依赖/数据库/端口）
│   ├── init_db.py                建库建表 + 基础数据
│   ├── import_csv.py             导入课堂 CSV（去重、冲突处理）
│   ├── seed_demo.py              生成演示数据
│   ├── benchmark.py              性能基准（记录数/采样吞吐/查询耗时，8.4）
│   └── reset_runtime.py          重置运行数据
├── tests/test_acceptance.py      验收场景测试（隔离模式，28 项，不影响演示库）
└── docs/
    ├── 设计说明.md               分层设计、状态机、异常策略
    ├── 验收测试记录.md/.json     验收场景实际结果
    └── 性能测试记录.md           8.4 性能基准实测
```

---

## 3. 关键设计约定

这些约定直接来自《需求说明书》，实现时不得绕过：

1. **三种量分列保存**：建议量（系统提出）、确认量（用户确认）、实际量（设备实测或
   人工填报，必须标来源）。设备无计量能力时实际量为「未获取」，**不用确认量补齐**。
2. **请求编号幂等**：客户端为一次确认生成请求编号，重复提交返回原任务；
   回执按任务号去重，不重复累计用料。
3. **设备占用**：同一设备已有未结束或待核查任务时，拒绝新的投喂任务。
4. **超时保守处理**：回执超时 → 任务转「结果未知（待核查）」并保留设备占用，
   **不自动重发**；核查结束且满足设备恢复条件才解除占用。
5. **来源可标识**：`real / sim / manual / csv` 四种来源分列，界面明确区分
   演示数据与真实数据。
6. **缺测不补零**：日内曲线缺测以空缺表示，无效值单独标记并排除在建议输入之外。

### 任务状态机

```
pending 待下发
  └─ dispatch ─→ dispatched 已下发待回执
                   ├─ accept ─→ running 执行中 ─→ finish(done) ─→ done 已完成
                   ├─ finish(failed)/fault ─→ failed 失败
                   ├─ stop ─→ stopped 已停止
                   └─ 超时未回执 ─→ unknown 结果未知（待核查）
                                        └─ review ─→ done / stopped / failed / not_executed
                                                     （结论为 unknown 时保持待核查）
```

---

## 4. 交付内容与说明

- **已实现**：后端接口与业务规则、采集终端仿真、Web 看板与移动端响应式页面、
  验收场景测试；以及原列为扩展的以下能力：
  - **AI 模型预测**（需求书 2.3）：生长（称重记录回归 → 达标日期）、
    投喂量（日投喂量对生物量+日均温回归）、水质（最近 72 小时趋势短期外推）。
    训练/预测/版本与 R²、MAE 指标见 `services/ml.py` 与 `api/model.py`；
    数据不足时明确返回原因，不返回伪预测。
  - **视觉识别**（需求书 5.5）：浏览器/终端上传相机帧（`POST /api/ponds/<id>/vision/frame`），
    OpenCV 水面漂浮物启发式检测（`cv-float-v1`）+ 画面质量检查，检出生成待确认事件；
    另提供 OCR 接口（`/vision/ocr`，需本机安装 tesseract，未装时明确提示）。
  - **交流与排行榜**（需求书 5.11）：发帖/回帖/关闭（`/api/community/posts`）、
    按饲料系数 FCR 排名（`/api/leaderboard`），数据不足的塘明确不参与排名。
  - **微信小程序**：`miniprogram/` 内置可运行的脚手架（鱼塘/单塘/交流三个页面，
    `wx.request` 直连后端），用微信开发者工具打开即可联调。
- **未实现 / 明确说明**：真实硬件接入（相机以浏览器摄像头/上传图片代接入）、
  深度学习视觉模型（当前为启发式算法，结果需人工确认）。
  未实现的能力不返回伪结果。

## 5. 已知限制

- 视觉识别为**启发式算法**（`cv-float-v1`，亮残差+连通域），非深度模型，
  对反光/水草/水花可能误报；识别结果仅作为待确认事件，需人工确认。
- OCR 依赖本机安装 tesseract-ocr（含中文包 chi_sim）；未安装时接口明确返回
  `ocr_unavailable`，不做假识别。
- AI 预测为可解释的线性/趋势模型（非黑盒深度学习），输出均带版本、样本数、
  R²/MAE 与适用边界；外推范围有限（生长 ≤180 天，水质 ≤24 小时）。
- 演示规则（`rule-v1.0`）为课堂联调用，非养殖阈值；界面已标注「演示规则」。
- 小程序未配置 AppID，真机预览需自行填入并修改 `miniprogram/utils/api.js` 的 BASE_URL。
