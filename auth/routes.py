import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from flask import Blueprint, request

from auth.middleware import token_required
from common.db_utils import generate_prefixed_id, initials
from common.http import failure, success
from database.connection import get_db

auth_bp = Blueprint("auth", __name__)


def _sign_token(user_id: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=int(os.getenv("JWT_EXPIRATION_HOURS", 24))),
    }
    return jwt.encode(payload, os.getenv("SECRET_KEY"), algorithm="HS256")


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return failure("Email and password are required", 400)

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM users WHERE LOWER(email) = %s", (email,))
        user = cursor.fetchone()
        if not user:
            return failure("Invalid email address. User not found.", 401)

        if user["status"] == "Disabled":
            return failure("This account has been disabled. Please contact the administrator.", 403)

        if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            return failure("Invalid password.", 401)

        token = _sign_token(user["user_id"], user["role"])
        user_response = {
            "userId": user["user_id"],
            "name": user["name"],
            "email": user["email"],
            "department": user["department"],
            "role": user["role"],
            "status": user["status"],
            "avatar": user["avatar"],
        }
        return success({"token": token, "user": user_response})
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    full_name = data.get("fullName", "").strip()
    email = data.get("email", "").strip().lower()
    phone = data.get("phone", "").strip()
    employee_id = data.get("employeeId", "").strip()
    department = data.get("department", "").strip()
    role = data.get("role", "Employee").strip() or "Employee"
    password = data.get("password", "")

    if not all([full_name, email, password, department]):
        return failure("Full name, email, password, and department are required.", 400)

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT user_id FROM users WHERE LOWER(email) = %s", (email,))
        if cursor.fetchone():
            return failure("Email is already registered.", 409)

        user_id = generate_prefixed_id("users", "user_id", "USR", 100)
        avatar = initials(full_name)
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        cursor.execute(
            """
            INSERT INTO users (user_id, name, email, department, password_hash, role, status, avatar, phone, employee_id_ext)
            VALUES (%s, %s, %s, %s, %s, %s, 'Active', %s, %s, %s)
            """,
            (user_id, full_name, email, department, password_hash, role, avatar, phone, employee_id),
        )
        cursor.execute("INSERT INTO user_settings (user_id) VALUES (%s)", (user_id,))
        conn.commit()

        token = _sign_token(user_id, role)
        return success(
            {
                "token": token,
                "user": {
                    "userId": user_id,
                    "name": full_name,
                    "email": email,
                    "department": department,
                    "role": role,
                    "status": "Active",
                    "avatar": avatar,
                },
            },
            201,
        )
    finally:
        cursor.close()
        conn.close()


@auth_bp.route("/logout", methods=["POST"])
@token_required
def logout(_current_user_id):
    return success({"message": "Logged out successfully"})
