import os

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_compress import Compress

from common.cache_instance import cache
from config import Config

# Initialize extensions at module level
compress = Compress()


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # Prevent OOM from massive uploads (50 MB limit)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    # CRITICAL: with debug=True Flask propagates unhandled exceptions past the
    # after_request hook → error responses ship WITHOUT CORS headers and browsers
    # report an opaque "Failed to fetch" instead of the real error. Force error
    # handlers to run so responses always carry CORS headers.
    app.config["PROPAGATE_EXCEPTIONS"] = False

    # Initialize Flask-Compress for gzip/brotli response compression
    compress.init_app(app)

    # Initialize Flask-Caching for in-memory caching
    cache.init_app(app)

    # CORS Configuration - Allow localhost (any port) + specific external origins
    DEBUG_MODE = os.getenv("FLASK_DEBUG", "0") == "1" or app.debug

    def is_allowed_origin(origin):
        """Check if origin is allowed"""
        if not origin:
            return False
        # DEV: allow ANY origin (Flutter web dev server uses random ports,
        # and may be reached via localhost, 127.0.0.1, or a LAN IP)
        if DEBUG_MODE:
            return True
        # Allow any localhost / 127.0.0.1 origin on any port
        if origin.startswith(("http://localhost:", "http://127.0.0.1:")) or origin in (
            "http://localhost",
            "http://127.0.0.1",
        ):
            return True
        # Allow LAN origins (frontend served from a local IP)
        if origin.startswith(("http://192.168.", "http://10.", "http://172.")):
            return True
        # Allow dev tunnel
        if origin == "https://jdtf4ztk-3000.inc1.devtunnels.ms":
            return True
        return False

    # CORS configuration with dynamic origin checking (flask-cors 4.x takes a
    # static list; the after_request hook below does the real per-request work)
    CORS(
        app,
        origins=[
            "http://localhost",
            "http://127.0.0.1",
            "http://192.168.1.15",
            "https://jdtf4ztk-3000.inc1.devtunnels.ms",
            *Config.CORS_ORIGINS,
        ],
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        max_age=3600,
    )

    @app.after_request
    def after_request(response):
        # Get origin from request and set if allowed (handles dynamic localhost ports)
        origin = request.headers.get("Origin")
        if origin and is_allowed_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        
        # Always set these headers for preflight requests
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Requested-With"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        response.headers["Cache-Control"] = "no-cache"

        # Close the per-request DB connection (if any)
        from database.connection import close_request_connection
        close_request_connection()

        return response

    # Register Blueprints
    from auth.routes import auth_bp
    from bidding.routes import bidding_bp
    from dashboard.routes import dashboard_bp
    from projects.routes import projects_bp
    from tasks.routes import tasks_bp
    from teams.routes import teams_bp
    from review.routes import review_bp
    from reports.routes import reports_bp
    from assets.routes import assets_bp
    from chat.routes import chat_bp
    from notifications.routes import notifications_bp
    from access.routes import access_bp
    from hrms_proxy.routes import hrms_proxy_bp
    from inventory.routes import inventory_bp
    from feedback.routes import feedback_bp
    from production.routes import production_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(bidding_bp, url_prefix="/api/bidding")
    app.register_blueprint(dashboard_bp, url_prefix="/api/dashboard")
    app.register_blueprint(projects_bp, url_prefix="/api/projects")
    app.register_blueprint(tasks_bp, url_prefix="/api/tasks")
    app.register_blueprint(teams_bp, url_prefix="/api/teams")
    
    app.register_blueprint(review_bp, url_prefix="/api/review")
    app.register_blueprint(reports_bp, url_prefix="/api/reports")
    app.register_blueprint(assets_bp, url_prefix="/api/assets")
    app.register_blueprint(chat_bp, url_prefix="/api/chat")
    app.register_blueprint(notifications_bp, url_prefix="/api/notifications")
    app.register_blueprint(access_bp, url_prefix="/api/access")
    app.register_blueprint(hrms_proxy_bp, url_prefix="/api/hrms-proxy")
    app.register_blueprint(inventory_bp, url_prefix="/api/inventory")
    app.register_blueprint(feedback_bp, url_prefix="/api/feedback")
    app.register_blueprint(production_bp, url_prefix="/api/production")

    # Health Check Endpoint
    @app.route("/")
    def health():
        return jsonify(
            {
                "success": True,
                "message": "API Server Running",
            }
        )

    # Error Handlers
    @app.errorhandler(404)
    def not_found(_error):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Resource not found",
                }
            ),
            404,
        )

    @app.errorhandler(Exception)
    def unhandled_exception(error):
        """Catch-all: log the traceback and return JSON.

        Because PROPAGATE_EXCEPTIONS is False, after_request still runs, so the
        response includes CORS headers and the browser shows the real error
        instead of an opaque "Failed to fetch".
        """
        app.logger.exception("Unhandled exception: %s", error)
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Internal server error",
                    "detail": str(error) if DEBUG_MODE else None,
                }
            ),
            500,
        )

    return app


if __name__ == "__main__":
    flask_app = create_app()

    flask_app.run(
        host="0.0.0.0",
        port=3000,
        debug=True,
    )