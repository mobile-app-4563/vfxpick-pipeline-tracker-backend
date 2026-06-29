from flask import Flask, jsonify
from flask_cors import CORS

from config import Config


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # CORS Configuration
    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": app.config.get(
                    "CORS_ORIGINS",
                    [
                        "http://localhost:3000",
                        "http://localhost:5000",
                        "http://localhost:8080",
                        "*",
                    ],
                )
            }
        },
        supports_credentials=True,
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Requested-With",
        ],
        methods=[
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        ],
    )

    @app.after_request
    def after_request(response):
        origin = app.config.get("CORS_ORIGIN", "*")

        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Requested-With"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Allow-Credentials"] = "true"

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

    @app.errorhandler(500)
    def internal_error(_error):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Internal server error",
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