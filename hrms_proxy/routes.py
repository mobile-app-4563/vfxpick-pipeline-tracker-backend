import re
from urllib.parse import urlparse

import requests
from flask import Blueprint, Response, request

hrms_proxy_bp = Blueprint("hrms_proxy", __name__)

UPSTREAM_BASE = "https://vfxpick.wallethr.com"
PROXY_PREFIX = "/api/hrms-proxy"
REQUEST_TIMEOUT_SECONDS = 30


def _build_upstream_url(path: str) -> str:
    cleaned = (path or "").lstrip("/")
    if not cleaned:
        return f"{UPSTREAM_BASE}/"
    return f"{UPSTREAM_BASE}/{cleaned}"


def _rewrite_location(location: str) -> str:
    if not location:
        return location
    parsed = urlparse(location)
    upstream_host = urlparse(UPSTREAM_BASE).netloc

    if parsed.netloc and parsed.netloc != upstream_host:
        return location

    path = parsed.path or "/"
    path = f"{PROXY_PREFIX}/{path.lstrip('/')}"
    if path.endswith("//"):
        path = path[:-1]

    out = path
    if parsed.query:
        out += f"?{parsed.query}"
    if parsed.fragment:
        out += f"#{parsed.fragment}"
    return out


def _rewrite_html_document(html_text: str) -> str:
    if "<head" in html_text.lower():
        html_text = re.sub(
            r"(<head[^>]*>)",
            lambda m: f'{m.group(1)}<base href="{PROXY_PREFIX}/">',
            html_text,
            count=1,
            flags=re.IGNORECASE,
        )

    for attr in ("href", "src", "action"):
        html_text = re.sub(
            rf"{attr}=\"/(?!/)",
            f'{attr}="{PROXY_PREFIX}/',
            html_text,
            flags=re.IGNORECASE,
        )
        html_text = re.sub(
            rf"{attr}='/(?!/)",
            f"{attr}='{PROXY_PREFIX}/",
            html_text,
            flags=re.IGNORECASE,
        )

    return html_text


@hrms_proxy_bp.route("", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@hrms_proxy_bp.route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@hrms_proxy_bp.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy(path: str = ""):
    if request.method == "OPTIONS":
        return Response(status=204)

    upstream_url = _build_upstream_url(path)

    forward_headers = {}
    for key, value in request.headers.items():
        lower = key.lower()
        if lower in {
            "host",
            "content-length",
            "connection",
            "accept-encoding",
            "origin",
            "referer",
        }:
            continue
        forward_headers[key] = value

    if request.query_string:
        upstream_url = f"{upstream_url}?{request.query_string.decode('utf-8', errors='ignore')}"

    upstream_response = requests.request(
        method=request.method,
        url=upstream_url,
        headers=forward_headers,
        data=request.get_data(),
        cookies=request.cookies,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    excluded_headers = {
        "content-length",
        "transfer-encoding",
        "content-encoding",
        "connection",
        "x-frame-options",
        "content-security-policy",
        "content-security-policy-report-only",
    }

    response_headers = {}
    for key, value in upstream_response.headers.items():
        if key.lower() in excluded_headers:
            continue
        if key.lower() == "location":
            response_headers[key] = _rewrite_location(value)
        else:
            response_headers[key] = value

    content_type = upstream_response.headers.get("Content-Type", "")
    if "text/html" in content_type.lower():
        body_text = upstream_response.text
        body_text = _rewrite_html_document(body_text)
        return Response(
            body_text,
            status=upstream_response.status_code,
            headers=response_headers,
            content_type=content_type,
        )

    return Response(
        upstream_response.content,
        status=upstream_response.status_code,
        headers=response_headers,
        content_type=content_type,
    )
