from flask import jsonify


def success(payload: dict, status_code: int = 200):
    body = {"success": True}
    body.update(payload)
    return jsonify(body), status_code


def failure(message: str, status_code: int = 400):
    return jsonify({"success": False, "error": message}), status_code
