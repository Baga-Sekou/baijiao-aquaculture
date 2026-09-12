@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   白蕉水产养殖管理平台 · 一键启动
echo ============================================================
echo.

REM ---------- 1. 检查 Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有找到 Python。
    echo        请先安装 Python 3.10 或更高版本：https://www.python.org/downloads/
    echo        安装时务必勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [1/4] 已找到 Python !PYVER!

REM ---------- 2. 检查依赖 ----------
python -c "import flask, sqlalchemy, dotenv, pymysql" >nul 2>nul
if errorlevel 1 (
    echo [2/4] 首次运行，正在安装依赖（需要联网，约 1-2 分钟）...
    python -m pip install -r requirements.txt -q
    if errorlevel 1 (
        echo [错误] 依赖安装失败。请检查网络，或手动执行：
        echo        python -m pip install -r requirements.txt
        pause
        exit /b 1
    )
) else (
    echo [2/4] 依赖已就绪
)

REM ---------- 3. 检查数据库 ----------
if not exist "data\baijiao.db" (
    echo [3/4] 首次运行，正在初始化数据库并写入示例数据...
    python scripts\init_db.py --seed --quiet
    if errorlevel 1 (
        echo [错误] 数据库初始化失败。
        pause
        exit /b 1
    )
    echo       正在载入课堂测试数据（两年 CSV）...
    python scripts\import_csv.py --dir data\sample >nul 2>nul
    python scripts\seed_demo.py >nul 2>nul
    echo       初始化完成
) else (
    echo [3/4] 数据库已存在，跳过初始化
)

REM ---------- 4. 启动服务 ----------
echo [4/4] 正在启动服务...
echo.
echo     服务地址：http://127.0.0.1:5000
echo     浏览器将在几秒后自动打开；关闭本窗口即停止服务。
echo.
REM 用后台 cmd 延迟打开浏览器，避免服务尚未就绪就访问
start "" cmd /c "timeout /t 5 >nul & start "" http://127.0.0.1:5000"
python backend\app.py

pause
