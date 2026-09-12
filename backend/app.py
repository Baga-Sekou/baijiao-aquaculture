"""白蕉水产养殖管理平台 · 后端应用入口。

启动：
    python backend/app.py
或（在 backend 目录下）：
    flask --app app run --port 5000

鉴权：课程项目采用轻量身份传递——请求头 X-User: <username>
     服务端据此校验鱼塘访问与控制权限（非功能需求 8.1）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, g, jsonify, render_template, request
from flask_cors import CORS

from database import SessionLocal
from services.common import current_user, fail

from api.ponds import bp as ponds_bp
from api.env import bp as env_bp
from api.feeding import bp as feeding_bp
from api.monitor import bp as monitor_bp
from api.ops import bp as ops_bp


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_AS_ASCII"] = False
    CORS(app)

    @app.before_request
    def _load_user():
        g.db = SessionLocal()
        username = request.headers.get("X-User") or request.args.get("user")
        g.user = current_user(g.db, username)

    @app.teardown_request
    def _close_db(exc):
        db = getattr(g, "db", None)
        if db is not None:
            db.close()

    @app.errorhandler(404)
    def _404(e):
        return fail("接口不存在", 404)

    @app.errorhandler(500)
    def _500(e):
        return fail("服务端错误", 500)

    app.register_blueprint(ponds_bp, url_prefix="/api")
    app.register_blueprint(env_bp, url_prefix="/api")
    app.register_blueprint(feeding_bp, url_prefix="/api")
    app.register_blueprint(monitor_bp, url_prefix="/api")
    app.register_blueprint(ops_bp, url_prefix="/api")

    # ---------------- 页面（Web 看板 / 移动端响应式） ----------------
    @app.route("/")
    def page_index():
        return render_template("index.html")

    @app.route("/pond/<int:pond_id>")
    def page_pond(pond_id):
        return render_template("pond.html", pond_id=pond_id)

    @app.route("/compare")
    def page_compare():
        return render_template("compare.html")

    @app.route("/review")
    def page_review():
        return render_template("review.html")

    @app.route("/alerts")
    def page_alerts():
        return render_template("alerts.html")

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "service": "baijiao-aquaculture", "version": "1.0"})

    return app


app = create_app()

if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5000"))
    from database import IS_SQLITE, DB_URL
    kind = "SQLite" if IS_SQLITE else "MySQL"
    print("=" * 60)
    print("  白蕉水产养殖管理平台 · 后端已启动")
    print(f"  数据库：{kind}")
    if IS_SQLITE:
        print(f"  数据库文件：{DB_URL.replace('sqlite:///', '')}")
    print(f"  访问地址：http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        print("  （已监听 0.0.0.0，同一 WiFi 下手机可用本机局域网 IP 访问）")
    print("=" * 60)
    app.run(host=host, port=port, debug=True, use_reloader=False)
