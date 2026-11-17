#!/usr/bin/env python3
"""RDX V-Cloud bypass host.

Runs as a small HTTP service (Heroku `web` dyno friendly) and can also be used
from the command line:

    python vcloud_host.py <vcloud-url>          # CLI, prints JSON
    python vcloud_host.py                       # starts the web service

Endpoints
    GET /                    -> service info
    GET /health              -> {"status": "ok"}
    GET /bypass?url=<link>   -> resolved links (JSON)

Config (environment variables)
    BYPARR_API   solver URL (Byparr / FlareSolverr, e.g. https://host:8191/v1)
    API_KEY      optional; if set, requests must send header `X-API-Key`
    PORT         set automatically by Heroku (default 8000)
    CACHE_TTL    seconds to cache a result (default 300, 0 disables)
"""
import base64
import html as H
import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

SOLVER_API = os.environ.get("BYPARR_API", "http://127.0.0.1:8191/v1")
API_KEY = os.environ.get("API_KEY", "").strip()
CACHE_TTL = int(os.environ.get("CACHE_TTL", "300") or 0)
SOLVER_RETRIES = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vcloud")


class BypassError(Exception):
    """Expected failure with a message that is safe to show the caller."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- solver
def _solve(url):
    """Fetch `url` through the Cloudflare solver. Returns (html, http_status)."""
    payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
    last = None
    for attempt in range(1, SOLVER_RETRIES + 1):
        try:
            r = requests.post(SOLVER_API, json=payload, timeout=90)
            data = r.json()
        except requests.exceptions.ConnectionError as e:
            raise BypassError(
                "Solver unreachable. Set the BYPARR_API config var to a running "
                "Byparr/FlareSolverr URL.", 503) from e
        except requests.exceptions.Timeout as e:
            last = e
            log.warning("solver timeout (attempt %s) for %s", attempt, url)
            continue
        except ValueError as e:
            last = e
            continue
        sol = data.get("solution") or {}
        if sol.get("response"):
            return sol["response"], sol.get("status")
        last = BypassError(data.get("message") or "Solver returned an empty response")
        time.sleep(1)
    if isinstance(last, BypassError):
        raise last
    raise BypassError(f"Solver failed: {last.__class__.__name__}", 504)


# --------------------------------------------------------------------------- parsing
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _links(body):
    out, seen = [], set()
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", body, re.S | re.I):
        text = _WS.sub(" ", _TAG.sub(" ", m.group(2))).strip()
        label = re.match(r"Download\s*\[([^\]]+)\]", text, re.I)
        href = re.search(r'href=["\']([^"\']+)["\']', m.group(1))
        if not (label and href):
            continue
        url = H.unescape(href.group(1))
        if url.startswith("http") and url not in seen:
            seen.add(url)
            out.append({"server": label.group(1).strip(), "url": url})
    return out


def _is_valid_url(url):
    p = urlparse(url or "")
    return p.scheme in ("http", "https") and bool(p.netloc)


# --------------------------------------------------------------------------- core
def vcloud(url):
    if not _is_valid_url(url):
        raise BypassError("Invalid URL", 400)

    page, status = _solve(url)
    tok = re.search(r"atob\(atob\('([^']+)'\)\)", page)
    if not tok:
        raise BypassError(f"Token not found on page (HTTP {status})")
    try:
        token_url = base64.b64decode(base64.b64decode(tok.group(1))).decode()
    except Exception as e:
        raise BypassError("Token could not be decoded") from e
    if not _is_valid_url(token_url):
        raise BypassError("Token did not contain a valid URL")

    body, status = _solve(token_url)
    links = _links(body)
    if not links:
        raise BypassError(f"No download links found (HTTP {status})")

    name = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
    size = re.search(r'id=["\']size["\'][^>]*>([^<]+)<', body)
    filename = H.unescape(name.group(1)).strip() if name else "Unknown_File"
    filesize = size.group(1).strip() if size else "Unknown"
    return {
        "success": True,
        "filename": filename,
        "filesize": filesize,
        "download_links": links,
        # Shape the RDX bot's proxy-API client already understands.
        "file_name": filename,
        "file_size": filesize,
        "servers": [{"name": l["server"], "direct_url": l["url"], "success": True} for l in links],
    }


# --------------------------------------------------------------------------- cache
_cache, _cache_lock = {}, threading.Lock()


def cached_vcloud(url):
    if CACHE_TTL <= 0:
        return vcloud(url)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    result = vcloud(url)
    with _cache_lock:
        _cache[url] = (now, result)
        for k in [k for k, v in _cache.items() if now - v[0] >= CACHE_TTL]:
            _cache.pop(k, None)
    return result


# --------------------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "RDXBypass/1.0"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self._send(200, {"status": "ok"})
        if parsed.path == "/":
            return self._send(200, {"service": "RDX V-Cloud bypass", "usage": "/bypass?url=<vcloud link>"})
        if parsed.path != "/bypass":
            return self._send(404, {"success": False, "error": "Not found"})
        if API_KEY and self.headers.get("X-API-Key", "") != API_KEY:
            return self._send(401, {"success": False, "error": "Invalid or missing API key"})

        # Keep any '&' that belongs to the target link: take everything after 'url='.
        raw = parsed.query
        target = parse_qs(raw).get("url", [""])[0]
        if raw.startswith("url=") and "&" in raw:
            target = requests.utils.unquote(raw[4:])
        try:
            self._send(200, cached_vcloud(target.strip()))
        except BypassError as e:
            log.warning("bypass failed for %s: %s", target, e)
            self._send(e.status, {"success": False, "error": str(e)})
        except Exception as e:  # never kill the server on one bad request
            log.exception("unexpected error for %s", target)
            self._send(500, {"success": False, "error": f"Internal error: {e.__class__.__name__}"})

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)


def serve():
    port = int(os.environ.get("PORT", "8000"))
    log.info("Listening on 0.0.0.0:%s (solver: %s)", port, SOLVER_API)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            print(json.dumps(vcloud(sys.argv[1]), ensure_ascii=False, indent=2))
        except BypassError as e:
            print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
    else:
        serve()
