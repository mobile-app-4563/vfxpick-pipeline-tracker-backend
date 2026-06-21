import os
from functools import wraps

import jwt
from flask import request

from common.http import failure


def token_required(handler):
    @wraps(handler)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()

        if not token:
            return failure("Authentication token is missing", 401)

        try:
            payload = jwt.decode(token, os.getenv("SECRET_KEY"), algorithms=["HS256"])
            current_user_id = payload.get("user_id")
            if not current_user_id:
                return failure("Token is invalid", 401)
        except jwt.ExpiredSignatureError:
            return failure("Token has expired", 401)
        except jwt.InvalidTokenError:
            return failure("Token is invalid", 401)

        return handler(current_user_id, *args, **kwargs)

    return decorated
