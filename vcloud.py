"""
VCloud Bypass API v1.0
======================
vcloud.beer / hubcloud / fastdl.beer লিংক বাইপাস করে ডাইরেক্ট ডাউনলোড URL বের করে।

সাপোর্টেড সার্ভার:
  - FSLv2 Server  (r2.cloudflarestorage.com)
  - FSL Server    (pub-xxx.r2.dev)
  - PixelServer   (pixeldrain.dev → pixeldrain.com/api/file/)
  - Server 10Gbps (gpdl2.hubcloud.pw → workers.dev → cryptonewz.online/dl.php → Google Drive)
  - Server:1      (অন্যান্য সার্ভার)

ব্যবহার:
  # স্ট্যান্ডঅ্যালোন:
  python vcloud.py

  # API:
  GET http://localhost:8081/bypass?url=https://vcloud.beer/HASH
  GET http://localhost:8081/bypass?url=https://hubcloud.DOMAIN/HASH

  # CLI:
  python vcloud.py https://vcloud.beer/HASH

কোনো pip install লাগে না — Python বিল্ট-ইন লাইব্রেরি দিয়ে চলে।
"""

import base64
import json
import os
import re
import ssl
import sys
import io
import time
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, parse_qs, unquote
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener, HTTPSHandler, HTTPCookieProcessor
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer as HTTPServer, BaseHTTPRequestHandler

# ─── কনফিগ ───
HOST = "0.0.0.0"
PORT = int(os.environ.get("VCLOUD_PORT", os.environ.get("PORT", 8081)))
TIMEOUT = 25
MAX_WORKERS = 8

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}


# ─── SSL Context ───
def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ─── NoRedirect Handler (10Gbps bypass এ লাগে) ───
class _NoRedirect(HTTPRedirectHandler):
    """Redirect follow না করে Location header ধরে।"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ─── HTTP ফেচ ───
def fetch(url, referer=None, allow_redirects=True):
    """URL ফেচ করে (HTML, final_url) রিটার্ন করে"""
    req = Request(url)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    if referer:
        req.add_header("Referer", referer)

    ctx = _ssl_ctx()
    resp = urlopen(req, timeout=TIMEOUT, context=ctx)
    final_url = resp.geturl()
    raw = resp.read()
    resp.close()

    ct = resp.headers.get("Content-Type", "")
    charset = "utf-8"
    if "charset=" in ct:
        charset = ct.split("charset=")[-1].split(";")[0].strip()
    try:
        html = raw.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        html = raw.decode("utf-8", errors="replace")

    return html, final_url


def fetch_redirect_location(url, referer=None):
    """
    Redirect follow না করে শুধু Location header রিটার্ন করে।
    10Gbps server bypass এ ব্যবহৃত।
    """
    ctx = _ssl_ctx()
    opener = build_opener(_NoRedirect, HTTPSHandler(context=ctx))

    req = Request(url)
    req.add_header("User-Agent", USER_AGENT)
    if referer:
        req.add_header("Referer", referer)

    try:
        resp = opener.open(req, timeout=TIMEOUT)
        return resp.geturl()
    except HTTPError as e:
        loc = e.headers.get("Location")
        if loc:
            return loc
        raise


# ─── HTML Link Parser ───
class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []
        self._id = ""
        self._in_a = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            href = d.get("href", "")
            if href:
                self._in_a = True
                self._href = href
                self._id = d.get("id", "")
                self._text = []

    def handle_data(self, data):
        if self._in_a:
            self._text.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self.links.append({
                "href": self._href,
                "id": self._id,
                "text": " ".join(self._text).strip(),
            })
            self._in_a = False


def parse_links(html, base_url):
    """HTML থেকে সব লিংক বের করে"""
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    results = []
    seen = set()
    for item in parser.links:
        href = item["href"].strip()
        if not href:
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen or not absolute.startswith(("http://", "https://")):
            continue
        seen.add(absolute)
        results.append({"url": absolute, "text": item["text"], "id": item.get("id", "")})
    return results


def _apply_js_overrides(html, links):
    """
    ভিক্লাউড পেজের JavaScript ওভাররাইড (যেমন var pxl = "..." -> #pxl-1) প্রয়োগ করে।
    ডিকয়/ডামি pixeldrain লিংক (negn6f) সরিয়ে আসল লিংক বসায়।
    """
    pxl_override = None
    m = re.search(r'(?:var|let|const)\s+pxl\s*=\s*["\'](https?://[^"\']+)["\']', html)
    if not m:
        m = re.search(r'getElementById\s*\(\s*["\']pxl-1["\']\s*\)\.href\s*=\s*["\'](https?://[^"\']+)["\']', html)
    if m:
        pxl_override = m.group(1).replace("\\/", "/").strip()

    if not pxl_override:
        return links

    updated = []
    replaced = False
    for link in links:
        l_copy = dict(link)
        if l_copy.get("id") == "pxl-1" or "pixeldrain" in l_copy["url"].lower():
            l_copy["url"] = pxl_override
            replaced = True
        updated.append(l_copy)

    if not replaced:
        updated.append({"url": pxl_override, "text": "Download [PixelServer : 2]", "id": "pxl-1"})
    return updated


# ════════════════════════════════════════════════════════════
#  SERVER LINK DETECTION
# ════════════════════════════════════════════════════════════

# Junk URL keywords — এগুলা ডাউনলোড লিংক না
_JUNK_KEYWORDS = [
    "terms", "contact", "copyright", "privacy", "policy", "login",
    "signup", "register", "about", "/faq", "dmca", "google.com/search",
    "t.me/", "telegram"
]

# Download server keywords (text-এ)
_SERVER_TEXT_KW = [
    "server", "fsl", "pixel", "10gbps", "download", "direct",
    "gdrive", "gofile", "filepress"
]

# Download server keywords (URL-এ)
_SERVER_URL_KW = [
    "pixeldrain.com/api/", "pixeldrain.dev/u/", "gofile.io/d/",
    "filepress", "mediafire.com/file/", "drive.google.com/",
    "workers.dev/", "dl.php", "fastdl", "filebee", ".r2.dev/",
    "vegadrive.me/", "vegadrive.app/", "vegadrive.co/",
    "vcloud.beer/", "gpdl", "hubcloud", "r2.cloudflarestorage.com"
]


def _is_server_link(text, url):
    """ডাউনলোড সার্ভার লিংক কি না চেক"""
    text_lower = text.lower()
    url_lower = url.lower()

    # Junk exclude
    if any(jk in url_lower for jk in _JUNK_KEYWORDS):
        return False
    if any(jk in text_lower for jk in _JUNK_KEYWORDS):
        return False

    # Bare domain homepage exclude (but allow ?id= query strings like gpdl2)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path and not parsed.query:
        return False

    return (
        any(kw in text_lower for kw in _SERVER_TEXT_KW)
        or any(kw in url_lower for kw in _SERVER_URL_KW)
    )


def _detect_server_name(text, url):
    """সার্ভারের নাম ডিটেক্ট"""
    tl = text.lower()
    ul = url.lower()

    if "fslv2" in tl or "r2.cloudflarestorage.com" in ul:
        return "FSLv2 Server"
    if "fsl" in tl and "v2" not in tl:
        return "FSL Server"
    if ".r2.dev/" in ul:
        return "FSL Server"
    if "pixel" in tl or "pixeldrain" in ul:
        return "PixelServer"
    if "10gbps" in tl or "gpdl" in ul or "cryptonewz" in ul:
        return "Server 10Gbps"
    if "vegadrop" in tl or "vegadrive" in ul:
        return "Vegadrop"
    if "gofile" in tl or "gofile" in ul:
        return "GoFile"
    if "transferit" in tl or "transfer.it" in ul:
        return "TransferIt"
    if "buzzheavier" in tl or "bzzhr.co" in ul or "buzzheavier" in ul:
        return "Buzzheavier"
    if "vikingfile" in tl or "vikingfile" in ul:
        return "VikingFile"
    if "filepress" in tl or "filebee" in ul:
        return "Filepress"
    if "drive.google" in ul or "googleusercontent" in ul:
        return "Google Drive"
    if "mediafire" in ul:
        return "MediaFire"

    # Server:1 বা Unknown
    server_match = re.search(r'Server\s*[:\s]*(\S+)', text, re.IGNORECASE)
    if server_match:
        return "Server " + server_match.group(1)
    if "download" in tl:
        return "Download Server"
    return "Unknown"


# ════════════════════════════════════════════════════════════
#  RESOLVE FUNCTIONS — প্রতিটি সার্ভার টাইপের জন্য
# ════════════════════════════════════════════════════════════

def _probe_download_url(url, timeout=10):
    """
    ফাইনাল ডাউনলোড URL আসলেই ডাউনলোডযোগ্য কিনা (403/404 দেয় কিনা) তা চেক করে।
    Range: bytes=0-15 ব্যবহার করে (টোকেন খরচ হয় না এবং খুব দ্রুত)।
    রিটার্ন: (http_status, is_working, error_msg)
    """
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-15"})
        resp = urlopen(req, timeout=timeout, context=_ssl_ctx())
        status = resp.status
        ct = resp.headers.get("Content-Type", "").lower()
        resp.read(16)
        resp.close()
        if "text/html" in ct and ("r2." in url.lower() or "googleusercontent" in url.lower() or "pixeldrain" in url.lower()):
            return status, False, "Server returned HTML page instead of file"
        return status, True, ""
    except HTTPError as e:
        code = e.code
        if code == 403 and "pixeldrain" in url.lower():
            try:
                body = e.read(400).decode("utf-8", "replace").lower()
                if "rate_limit" in body or "captcha" in body or "limit" in body:
                    return code, True, ""
            except Exception:
                pass
        if code in (403, 404, 410):
            return code, False, "HTTP {} (লিংকটি বর্তমানে বন্ধ বা ব্লকড)".format(code)
        return code, True, ""
    except Exception:
        return None, True, ""


def resolve_pixeldrain(url):
    """pixeldrain.dev/u/ID → pixeldrain.com/api/file/ID (ডাইরেক্ট ডাউনলোড + চেক)"""
    file_id = url.rstrip("/").split("/u/")[-1].split("/")[0].split("?")[0]
    candidates = [
        "https://pixeldrain.com/api/file/{}".format(file_id),
        "https://pixeldrain.dev/api/file/{}".format(file_id),
    ]
    last_code = None
    last_err = ""
    for api_url in candidates:
        status, ok, err = _probe_download_url(api_url)
        last_code = status
        last_err = err
        if ok:
            return {
                "success": True,
                "direct_url": api_url,
                "page_url": url,
                "server": "PixelServer",
                **({"http_status": status} if status else {}),
            }
    return {
        "success": False,
        "direct_url": candidates[0],
        "page_url": url,
        "server": "PixelServer",
        "http_status": last_code,
        "error": last_err or "PixelDrain file not found",
    }


def resolve_10gbps(url, referer=None):
    """
    10Gbps server bypass:
    gpdl2.hubcloud.pw/?id=HASH
      → 302 → gpdl2.rohitkiskk.workers.dev/?id=HASH
        → 302 → cryptonewz.online/dl.php?link=GOOGLE_DRIVE_URL

    dl.php?link= থেকে link প্যারামিটার extract করে direct URL দেয়।
    """
    try:
        # Step 1: gpdl2.hubcloud.pw → workers.dev redirect
        loc1 = fetch_redirect_location(url, referer=referer)
        if not loc1:
            return {"success": False, "error": "10Gbps: no redirect from hubcloud"}

        # Step 2: workers.dev → cryptonewz.online/dl.php?link=...
        loc2 = fetch_redirect_location(loc1, referer=url)
        if not loc2:
            # হয়তো loc1-ই ফাইনাল
            loc2 = loc1

        # dl.php?link= থেকে Google Drive URL extract
        if "dl.php" in loc2 and "link=" in loc2:
            parsed = urlparse(loc2)
            qs = parse_qs(parsed.query)
            link_val = qs.get("link", [None])[0]
            if link_val:
                status, ok, err = _probe_download_url(link_val)
                res = {
                    "success": ok,
                    "direct_url": link_val,
                    "proxy_url": loc2,
                    "server": "Server 10Gbps",
                    "note": "proxy_url ব্রাউজারে কাজ করে, direct_url Google Drive raw",
                }
                if status:
                    res["http_status"] = status
                if not ok:
                    res["error"] = err
                return res

        # workers.dev ডাইরেক্ট redirect
        if "workers.dev" in loc2 or "cryptonewz" in loc2:
            return {
                "success": True,
                "direct_url": loc2,
                "server": "Server 10Gbps",
            }

        return {
            "success": True,
            "direct_url": loc1,
            "server": "Server 10Gbps",
        }
    except Exception as e:
        return {"success": False, "error": "10Gbps bypass error: {}".format(str(e))}


def resolve_r2_direct(url):
    """R2 CDN / Cloudflare Storage — ডাইরেক্ট লিংক ভ্যালিডেট সহ"""
    server = "FSLv2 Server" if "r2.cloudflarestorage.com" in url else "FSL Server"
    status, ok, err = _probe_download_url(url)
    res = {
        "success": ok,
        "direct_url": url,
        "server": server,
    }
    if status:
        res["http_status"] = status
    if not ok:
        res["error"] = err
    return res


def resolve_dl_php(url):
    """dl.php?link=... থেকে link প্যারামিটার extract + ভ্যালিডেট"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    link_val = qs.get("link", [None])[0]
    if link_val:
        status, ok, err = _probe_download_url(link_val)
        res = {
            "success": ok,
            "direct_url": link_val,
            "proxy_url": url,
            "server": "Download Proxy",
        }
        if status:
            res["http_status"] = status
        if not ok:
            res["error"] = err
        return res
    return {"success": True, "direct_url": url, "server": "Download Proxy"}


def resolve_gofile(url):
    """GoFile — keep as-is"""
    return {"success": True, "direct_url": url, "server": "GoFile"}


def resolve_server_link(url, text="", referer=None):
    """
    সার্ভার লিংক resolve করে ডাইরেক্ট URL বের করে।
    URL দেখে বুঝে নেয় কোন resolver ব্যবহার করতে হবে।
    """
    ul = url.lower()

    # PixelDrain
    if "pixeldrain" in ul and "/u/" in url:
        return resolve_pixeldrain(url)

    # 10Gbps Server (gpdl2.hubcloud.pw)
    if "gpdl" in ul and "hubcloud" in ul:
        return resolve_10gbps(url, referer=referer)

    # R2 CDN direct links
    if "r2.cloudflarestorage.com" in ul or ".r2.dev/" in ul:
        return resolve_r2_direct(url)

    # dl.php proxy
    if "dl.php" in ul and "link=" in ul:
        return resolve_dl_php(url)

    # GoFile
    if "gofile.io" in ul:
        return resolve_gofile(url)

    # Google Drive / GoogleUserContent
    if "googleusercontent.com" in ul:
        status, ok, err = _probe_download_url(url)
        res = {"success": ok, "direct_url": url, "server": "Google Drive"}
        if status:
            res["http_status"] = status
        if not ok:
            res["error"] = err
        return res
    if "drive.google.com" in ul:
        return {"success": True, "direct_url": url, "server": "Google Drive"}

    # Workers.dev → সরাসরি 10Gbps bypass ট্রাই
    if "workers.dev" in ul:
        try:
            loc = fetch_redirect_location(url, referer=referer)
            if loc and "dl.php" in loc and "link=" in loc:
                return resolve_dl_php(loc)
            return {"success": True, "direct_url": loc or url, "server": "CDN Workers"}
        except Exception:
            return {"success": True, "direct_url": url, "server": "CDN Workers"}

    # VegaDrive page (skydrop + cookie session)
    if "vegadrive" in ul:
        try:
            cj = http.cookiejar.CookieJar()
            op_follow = build_opener(HTTPSHandler(context=_ssl_ctx()), HTTPCookieProcessor(cj))
            op_nr = build_opener(_NoRedirect, HTTPSHandler(context=_ssl_ctx()), HTTPCookieProcessor(cj))

            def _vg(u, ref=None, accept="text/html,*/*"):
                req = Request(u, headers={"User-Agent": USER_AGENT, "Accept": accept})
                if ref:
                    req.add_header("Referer", ref)
                r = op_follow.open(req, timeout=TIMEOUT)
                return r.read().decode("utf-8", "replace"), r.geturl()

            v_html, v_final = _vg(url, ref=referer)
            m = re.search(r"https?://([^/]+)/(?:s|d)/([A-Za-z0-9]+)", v_final)
            if m:
                host, fid = m.group(1), m.group(2)
                if "/{}/skydrop".format(fid) in v_html or "provider=skydrop" in v_html or '["skydrop","ready"]' in v_html:
                    sky_url = "https://{}/d/{}/skydrop".format(host, fid)
                    sky_html, _ = _vg(sky_url, ref=v_final)
                    prep_m = re.search(r'prepareUrl\s*=\s*"([^"]+)"', sky_html)
                    link_m = re.search(r'linkUrl\s*=\s*"([^"]+)"', sky_html)
                    if prep_m and link_m:
                        prep = prep_m.group(1).replace("\\/", "/")
                        link_ep = link_m.group(1).replace("\\/", "/")
                        for _ in range(18):
                            pdata = json.loads(_vg(prep, ref=sky_url, accept="application/json")[0])
                            if pdata.get("ready") is True:
                                if not pdata.get("download_url"):
                                    pdata = json.loads(_vg(link_ep, ref=sky_url, accept="application/json")[0])
                                if pdata.get("download_url"):
                                    return {"success": True, "direct_url": pdata["download_url"], "server": "Vegadrop 10Gbps"}
                            time.sleep(max(1.5, min(5.0, (pdata.get("retry_after_ms") or 2500) / 1000.0)))
                # Fallback: external mirror redirect without touching external CF host
                for prov, label in [
                    ("pixeldrain", "PixelServer"),
                    ("gofile", "GoFile"),
                    ("transferit", "TransferIt"),
                    ("buzzheavier", "Buzzheavier"),
                    ("vikingfile", "VikingFile"),
                ]:
                    if "/{}/{}".format(fid, prov) in v_html:
                        cur = "https://{}/d/{}/{}".format(host, fid, prov)
                        for _ in range(5):
                            try:
                                op_nr.open(Request(cur, headers={"User-Agent": USER_AGENT, "Referer": v_final}), timeout=15)
                                break
                            except HTTPError as he:
                                loc = he.headers.get("Location")
                                if he.code in (301, 302, 303, 307, 308) and loc:
                                    loc = urljoin(cur, loc)
                                    if "vegadrive" in loc.lower():
                                        cur = loc
                                        continue
                                    if "pixeldrain" in loc.lower() and "/u/" in loc:
                                        return resolve_pixeldrain(loc)
                                    return {"success": True, "direct_url": loc, "server": label}
                                break
            return {"success": False, "direct_url": url, "error": "VegaDrive direct link not ready", "server": "VegaDrive"}
        except Exception as e:
            return {"success": False, "direct_url": url, "error": str(e), "server": "VegaDrive"}

    # Default: URL-ই direct
    server = _detect_server_name(text, url)
    return {"success": True, "direct_url": url, "server": server}


# ════════════════════════════════════════════════════════════
#  DOUBLE BASE64 DECODE
# ════════════════════════════════════════════════════════════

def _decode_double_base64(encoded):
    """Double base64 decode: atob(atob('...'))"""
    try:
        d1 = base64.b64decode(encoded).decode("utf-8", errors="replace")
        d2 = base64.b64decode(d1).decode("utf-8", errors="replace")
        return d2
    except Exception:
        try:
            return base64.b64decode(encoded).decode("utf-8", errors="replace")
        except Exception:
            return None


# ════════════════════════════════════════════════════════════
#  MAIN BYPASS FUNCTION
# ════════════════════════════════════════════════════════════

def bypass_vcloud(url):
    """
    vcloud.beer / hubcloud / fastdl পেজ বাইপাস:
    1. পেজ ফেচ করে
    2. Double base64 decode করে token URL বের করে
    3. Token URL ফেচ করে সার্ভার লিংক বের করে (+ JS var pxl ওভাররাইড)
    4. প্রতিটি সার্ভার লিংক resolve + ভ্যালিডেট করে ডাইরেক্ট URL দেয়
    """
    try:
        html, final_url = fetch(url)

        # ──── ফাইল ইনফো extract (filename, size, mimetype) ────
        file_info = _extract_file_info(html)

        # ──── সার্ভার লিংক খোঁজা ────
        server_links = []

        # Method 1: Double base64 decode → token URL
        b64_match = re.search(r"atob\s*\(\s*atob\s*\(\s*['\"]([A-Za-z0-9+/=]+)['\"]\s*\)", html)
        if b64_match:
            decoded_url = _decode_double_base64(b64_match.group(1))
            if decoded_url and decoded_url.startswith("http"):
                try:
                    inner_html, inner_final = fetch(decoded_url, referer=url)
                    parsed = _apply_js_overrides(inner_html, parse_links(inner_html, inner_final))
                    for link in parsed:
                        if _is_server_link(link["text"], link["url"]):
                            server_links.append(link)
                    # Inner page তেও file info থাকতে পারে
                    if not file_info.get("file_name"):
                        file_info = _extract_file_info(inner_html)
                except Exception:
                    pass

        # Method 2: Single base64
        if not server_links:
            b64_single = re.search(r"atob\s*\(\s*['\"]([A-Za-z0-9+/=]{20,})['\"]\s*\)", html)
            if b64_single:
                try:
                    decoded = base64.b64decode(b64_single.group(1)).decode("utf-8", errors="replace")
                    if decoded.startswith("http"):
                        inner_html, inner_final = fetch(decoded, referer=url)
                        parsed = _apply_js_overrides(inner_html, parse_links(inner_html, inner_final))
                        for link in parsed:
                            if _is_server_link(link["text"], link["url"]):
                                server_links.append(link)
                        if not file_info.get("file_name"):
                            file_info = _extract_file_info(inner_html)
                except Exception:
                    pass

        # Method 3: Direct page links
        if not server_links:
            parsed = _apply_js_overrides(html, parse_links(html, final_url))
            for link in parsed:
                if _is_server_link(link["text"], link["url"]) and link["url"] != url:
                    server_links.append(link)

        # Method 4: JS redirects
        for js_url in re.findall(
            r'(?:window\.location(?:\.href)?\s*=|href\s*=)\s*["\'](https?://[^\s"\'<>]+)["\']', html
        ):
            jl = js_url.lower()
            if any(d in jl for d in [
                "pixeldrain", "gofile", "filepress", "drive.google",
                "workers.dev", "dl.php", "fastdl", ".r2.dev",
                "gpdl", "r2.cloudflarestorage"
            ]):
                if not any(s["url"] == js_url for s in server_links):
                    server_links.append({"url": js_url, "text": "JS Redirect"})

        if not server_links:
            return {
                "success": False,
                "source": url,
                "error": "কোনো সার্ভার লিংক পাওয়া যায়নি",
                **file_info,
            }

        # ──── প্রতিটি সার্ভার লিংক resolve (parallel) ────
        results = []

        def _resolve_one(link):
            try:
                res = resolve_server_link(link["url"], link["text"], referer=url)
                return {
                    "name": res.get("server", _detect_server_name(link["text"], link["url"])),
                    "direct_url": res.get("direct_url", link["url"]),
                    "source_url": link["url"],
                    "success": res.get("success", True),
                    **({k: res[k] for k in ["http_status", "proxy_url", "page_url", "note", "error"]
                        if k in res and res[k] is not None and res[k] != ""}),
                }
            except Exception as e:
                return {
                    "name": _detect_server_name(link["text"], link["url"]),
                    "source_url": link["url"],
                    "success": False,
                    "error": str(e),
                }

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(_resolve_one, link) for link in server_links]
            for f in as_completed(futures):
                try:
                    results.append(f.result())
                except Exception as e:
                    results.append({"name": "Unknown", "success": False, "error": str(e)})

        # সফল results সামনে
        results.sort(key=lambda x: (0 if x.get("success") else 1))
        working_count = sum(1 for r in results if r.get("success"))

        return {
            "success": working_count > 0,
            "source": url,
            "total_servers": len(results),
            "working_servers": working_count,
            "servers": results,
            **file_info,
        }
    except HTTPError as e:
        res = {"success": False, "source": url, "http_status": e.code, "error": "VCloud bypass error: HTTP Error {}: {}".format(e.code, e.reason)}
        if e.code == 403:
            res["hint"] = "Cloud/Heroku AWS IP থেকে vcloud.beer Cloudflare 403 ব্লক করতে পারে; লোকালহোস্ট/রেসিডেনশিয়াল IP থেকে ঠিকমতো কাজ করে।"
        return res
    except Exception as e:
        return {"success": False, "source": url, "error": "VCloud bypass error: {}".format(str(e))}


def _extract_file_info(html):
    """HTML থেকে ফাইলের নাম, সাইজ, MIME type extract করে"""
    info = {}

    # File name — .mkv/.mp4/.zip/.rar ইত্যাদি দিয়ে শেষ হওয়া pattern (সবচেয়ে reliable)
    fn_match = re.search(
        r'([A-Za-z0-9._\-\[\]()+ ]{5,}\.(?:mkv|mp4|zip|rar|avi|webm|flv|wmv|m4v|ts|srt))',
        html, re.IGNORECASE
    )
    if fn_match:
        info["file_name"] = fn_match.group(1).strip()

    # File size
    size_match = re.search(
        r'(?:Size|ফাইল সাইজ)\s*(?:</[^>]+>)?\s*(?:<[^>]+>)?\s*'
        r'([\d.,]+\s*(?:KB|MB|GB|TB|B))',
        html, re.IGNORECASE
    )
    if size_match:
        info["file_size"] = size_match.group(1).strip()

    # MIME type
    mime_match = re.search(
        r'(?:MimeType|Content.?Type)\s*(?:</[^>]+>)?\s*(?:<[^>]+>)?\s*'
        r'([a-z]+/[a-z0-9._\-+]+)',
        html, re.IGNORECASE
    )
    if mime_match:
        info["mime_type"] = mime_match.group(1).strip()

    return info


# ════════════════════════════════════════════════════════════
#  API SERVER
# ════════════════════════════════════════════════════════════

class VCloudHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_url_param(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        return params.get("url", [None])[0]

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            self._send_json({
                "app": "VCloud Bypass API",
                "version": "1.0.0",
                "endpoints": {
                    "/bypass?url=<vcloud_url>": "vcloud/hubcloud bypass -> direct download URLs",
                },
                "supported": [
                    "vcloud.beer", "hubcloud", "fastdl.beer",
                    "gpdl2.hubcloud.pw (10Gbps)", "pixeldrain",
                    "R2 CDN (FSL/FSLv2)", "vegadrive", "gofile"
                ],
            })
            return

        if path == "/bypass":
            url = self._get_url_param()
            if not url:
                self._send_json({"success": False, "error": "url parameter required"}, 400)
                return
            self._send_json(bypass_vcloud(url))
            return

        self._send_json({"error": "Not Found. Use /bypass?url=<vcloud_url>"}, 404)

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}
        url = data.get("url") or self._get_url_param()
        if not url:
            self._send_json({"success": False, "error": "url parameter required"}, 400)
            return
        self._send_json(bypass_vcloud(url))

    def log_message(self, format, *args):
        print("  [{}] {}".format(self.log_date_time_string(), args[0]))


# ─── CLI Mode ───
def cli_bypass(url):
    """কমান্ড লাইন থেকে bypass করা"""
    result = bypass_vcloud(url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


# ─── Start Server ───
def start_server():
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    server = HTTPServer((HOST, PORT), VCloudHandler)
    print("=" * 55)
    print("  VCloud Bypass API v1.0")
    print("=" * 55)
    print("  Server: http://localhost:{}".format(PORT))
    print()
    print("  Endpoints:")
    print("    GET  /bypass?url=<vcloud_url>")
    print("    POST /bypass  (body: {\"url\": \"...\"})")
    print()
    print("  Supported servers:")
    print("    FSLv2, FSL, PixelServer, 10Gbps, VegaDrive, GoFile")
    print()
    print("  Stop: Ctrl+C")
    print("=" * 55)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server shutting down...")
        server.shutdown()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        url = sys.argv[1]
        if url.startswith("http"):
            # CLI mode: python vcloud.py <url>
            cli_bypass(url)
        elif url in ("--server", "-s", "server"):
            start_server()
        else:
            print("Usage:")
            print("  python vcloud.py <vcloud_url>       # CLI bypass")
            print("  python vcloud.py --server            # API server start")
            print("  python vcloud.py                     # API server start (default)")
    else:
        start_server()
