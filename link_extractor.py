"""
Link Extractor & Bypass API v6.0
=================================
vegamovies / nexdrive.beer / fastdl.beer / filebee / vcloud.beer / hubcloud
সব চেইন বাইপাস করে ডাইরেক্ট ডাউনলোড লিংক বের করে।

Endpoints:
  GET /direct?url=<vegamovies_url>    -> ONE API — পুরো চেইন বাইপাস → শুধু ডাইরেক্ট URL
  GET /movie?url=<vegamovies_url>     -> nexdrive + bypass সব একসাথে (detailed)
  GET /bypass?url=<nexdrive_url>      -> nexdrive/fastdl/vcloud/hubcloud বাইপাস
  GET /api/extract?url=<URL>          -> পেজ থেকে ডাউনলোড লিংক এক্সট্রাক্ট
  GET /api/resolve?url=<URL>          -> রিডাইরেক্ট ফলো করে ফাইনাল URL

ব্যবহার:
  python link_extractor.py

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
from urllib.parse import urljoin, urlparse, parse_qs, quote, unquote
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener, HTTPSHandler, HTTPCookieProcessor
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer as HTTPServer, BaseHTTPRequestHandler

# ─── কনফিগ ───
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8080))  # Heroku sets PORT env var
MAX_REDIRECTS = 15
TIMEOUT = 20

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


# ─── HTTP ফেচ ───
def fetch(url, referer=None, extra_headers=None):
    """URL ফেচ করে (HTML, final_url) রিটার্ন করে"""
    req = Request(url)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    if referer:
        req.add_header("Referer", referer)
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)

    resp = urlopen(req, timeout=TIMEOUT, context=_ssl_ctx())
    final_url = resp.geturl()
    raw = resp.read()
    resp.close()

    # Encoding detect
    ct = resp.headers.get("Content-Type", "")
    charset = "utf-8"
    if "charset=" in ct:
        charset = ct.split("charset=")[-1].split(";")[0].strip()
    try:
        html = raw.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        html = raw.decode("utf-8", errors="replace")

    return html, final_url


def fetch_head(url, referer=None):
    """HEAD request করে final URL বের করে (redirect follow সহ)"""
    try:
        req = Request(url, method="HEAD")
        req.add_header("User-Agent", USER_AGENT)
        if referer:
            req.add_header("Referer", referer)
        resp = urlopen(req, timeout=TIMEOUT, context=_ssl_ctx())
        final = resp.geturl()
        resp.close()
        return final
    except HTTPError as e:
        loc = e.headers.get("Location")
        if loc:
            return urljoin(url, loc)
        return url
    except Exception:
        return url


# ─── HTML Link Parser ───
class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []
        self._in_a = False
        self._cls = ""
        self._id = ""

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            href = d.get("href", "")
            if href:
                self._in_a = True
                self._href = href
                self._text = []
                self._cls = d.get("class", "")
                self._id = d.get("id", "")

    def handle_data(self, data):
        if self._in_a:
            self._text.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self.links.append({
                "href": self._href,
                "text": " ".join(self._text).strip(),
                "class": self._cls,
                "id": self._id,
            })
            self._in_a = False


def parse_links(html, base_url):
    """HTML থেকে সব লিংক বের করে"""
    parser = LinkParser()
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
        results.append({
            "url": absolute,
            "text": item["text"],
            "class": item.get("class", ""),
            "id": item.get("id", ""),
        })
    return results


def _apply_js_overrides(html, links):
    """
    vcloud/hubcloud পেজের JavaScript ওভাররাইড (যেমন var pxl = "..." -> #pxl-1) প্রয়োগ করে।
    ডিকয় pixeldrain লিংক (negn6f) সরিয়ে আসল লিংক বসায়।
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
        updated.append({
            "url": pxl_override,
            "text": "Download [PixelServer : 2]",
            "class": "",
            "id": "pxl-1",
        })
    return updated


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
        ul = url.lower()
        if "text/html" in ct and ("r2." in ul or "googleusercontent" in ul or "pixeldrain" in ul):
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


# ════════════════════════════════════════════
#  DEEP URL RESOLVER — ইন্টারমিডিয়েট লিংক ফাইনাল করে
# ════════════════════════════════════════════

def resolve_to_final(url, referer=None):
    """
    যেকোনো ইন্টারমিডিয়েট URL থেকে ফাইনাল ডাইরেক্ট ডাউনলোড URL বের করে + 403/404 ভ্যালিডেট করে।
    dl.php?link=... → link parameter বের করে
    vegadrive.me/app → cookie session + skydrop/mirror bridge
    pixeldrain.dev/u/... → API URL বানায় + probe
    gpdl2.hubcloud.pw → workers.dev → cryptonewz.online/dl.php → probe
    """
    url_lower = url.lower()

    # dl.php?link=... থেকে link extract (fastdl, cryptonewz etc.)
    if "dl.php?" in url_lower and "link=" in url_lower:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        link_val = qs.get("link", [None])[0]
        if link_val:
            status, ok, err = _probe_download_url(link_val)
            res = {
                "final_url": link_val,
                "proxy_url": url,
                "resolved_via": "dl.php_param",
                "success": ok,
            }
            if status:
                res["http_status"] = status
            if not ok:
                res["error"] = err
            return res

    # 10Gbps Server (gpdl2.hubcloud.pw → workers.dev → cryptonewz/dl.php)
    if "gpdl" in url_lower and "hubcloud" in url_lower:
        try:
            loc1 = fetch_redirect_location(url, referer=referer)
            if loc1:
                if "dl.php" in loc1 and "link=" in loc1:
                    loc2 = loc1
                else:
                    loc2 = fetch_redirect_location(loc1, referer=url) or loc1
                if loc2 and "dl.php" in loc2 and "link=" in loc2:
                    parsed = urlparse(loc2)
                    qs = parse_qs(parsed.query)
                    link_val = qs.get("link", [None])[0]
                    if link_val:
                        status, ok, err = _probe_download_url(link_val)
                        res = {
                            "final_url": link_val,
                            "proxy_url": loc2,
                            "resolved_via": "10gbps_bypass",
                            "success": ok,
                        }
                        if status:
                            res["http_status"] = status
                        if not ok:
                            res["error"] = err
                        return res
                    return {"final_url": loc2, "proxy_url": loc2, "resolved_via": "10gbps_workers", "success": True}
                return {"final_url": loc1, "resolved_via": "10gbps_redirect", "success": True}
        except Exception:
            pass
        return {"final_url": url, "resolved_via": "10gbps_fallback", "success": False, "error": "10Gbps bypass failed"}

    # workers.dev → 10Gbps bypass ট্রাই
    if "workers.dev" in url_lower and "?id=" in url_lower:
        try:
            loc = fetch_redirect_location(url, referer=referer)
            if loc and "dl.php" in loc and "link=" in loc:
                parsed = urlparse(loc)
                qs = parse_qs(parsed.query)
                link_val = qs.get("link", [None])[0]
                if link_val:
                    status, ok, err = _probe_download_url(link_val)
                    res = {"final_url": link_val, "proxy_url": loc, "resolved_via": "workers_bypass", "success": ok}
                    if status:
                        res["http_status"] = status
                    if not ok:
                        res["error"] = err
                    return res
            return {"final_url": loc or url, "resolved_via": "workers_redirect", "success": True}
        except Exception:
            return {"final_url": url, "resolved_via": "workers_fallback", "success": True}

    # pixeldrain → direct download + probe (.com then .dev)
    if "pixeldrain" in url_lower and "/u/" in url:
        file_id = url.rstrip("/").split("/u/")[-1].split("/")[0].split("?")[0]
        candidates = [
            "https://pixeldrain.com/api/file/{}".format(file_id),
            "https://pixeldrain.dev/api/file/{}".format(file_id),
        ]
        last_code, last_err = None, ""
        for api_url in candidates:
            status, ok, err = _probe_download_url(api_url)
            last_code, last_err = status, err
            if ok:
                return {
                    "final_url": api_url,
                    "page_url": url,
                    "resolved_via": "pixeldrain_api",
                    "success": True,
                    **({"http_status": status} if status else {}),
                }
        return {
            "final_url": candidates[0],
            "page_url": url,
            "resolved_via": "pixeldrain_api",
            "success": False,
            "http_status": last_code,
            "error": last_err or "PixelDrain file not found",
        }

    # gofile → keep as-is (needs API key / browser session for CF)
    if "gofile.io" in url_lower:
        return {"final_url": url, "resolved_via": "gofile_direct", "success": True}

    # R2 CDN direct links — probe 403/404
    if "r2.cloudflarestorage.com" in url_lower or ".r2.dev/" in url_lower:
        status, ok, err = _probe_download_url(url)
        res = {"final_url": url, "resolved_via": "r2_cdn_direct", "success": ok}
        if status:
            res["http_status"] = status
        if not ok:
            res["error"] = err
        return res

    # Other CDN direct links
    if any(x in url_lower for x in ["workers.dev/", "mediafire.com/", "mega.nz/"]):
        return {"final_url": url, "resolved_via": "cdn_direct", "success": True}

    # Google Drive / GoogleUserContent
    if "googleusercontent.com" in url_lower:
        status, ok, err = _probe_download_url(url)
        res = {"final_url": url, "resolved_via": "google_direct", "success": ok}
        if status:
            res["http_status"] = status
        if not ok:
            res["error"] = err
        return res
    if "drive.google.com" in url_lower:
        return {"final_url": url, "resolved_via": "google_direct", "success": True}

    # vegadrive.me / vegadrive.app — CookieSession + skydrop/mirror bridge
    if "vegadrive" in url_lower:
        vdata = bypass_vegadrive(url, referer=referer)
        for item in vdata.get("results", []):
            if item.get("success") and item.get("final_url"):
                return {
                    "final_url": item["final_url"],
                    "resolved_via": "vegadrive_" + item.get("server", "mirror").lower().replace(" ", "_"),
                    "success": True,
                    **({"http_status": item["http_status"]} if item.get("http_status") else {}),
                }
        return {"final_url": url, "resolved_via": "vegadrive_fallback", "success": False, "error": vdata.get("error", "VegaDrive failed")}

    # Telegram
    if "t.me/" in url_lower:
        return {"final_url": url, "resolved_via": "telegram", "success": True}

    # Default: HEAD follow
    try:
        head_final = fetch_head(url, referer=referer)
        return {"final_url": head_final, "resolved_via": "head_redirect", "success": True}
    except Exception:
        return {"final_url": url, "resolved_via": "fallback", "success": True}


# ════════════════════════════════════════════
#  BYPASS LOGIC
# ════════════════════════════════════════════

def bypass_fastdl(url, referer=None):
    """
    fastdl.beer/embed.php পেজ থেকে ডাইরেক্ট ডাউনলোড URL বের করে।
    FIX: proxy_url রিটার্ন করে — ব্রাউজারে কাজ করে।
    """
    try:
        html, _ = fetch(url, referer=referer)

        # Pattern 1: var reurl = "URL";
        match = re.search(r'var\s+reurl\s*=\s*["\'](.*?)["\']', html)
        if match:
            dl_url = match.group(1)
            if "dl.php?link=" in dl_url:
                parsed = urlparse(dl_url)
                qs = parse_qs(parsed.query)
                raw_url = qs.get("link", [dl_url])[0]
                return {
                    "success": True,
                    "direct_url": dl_url,
                    "raw_url": raw_url,
                    "proxy_url": dl_url,
                    "source": "fastdl",
                }
            return {"success": True, "direct_url": dl_url, "source": "fastdl"}

        # Pattern 2: window.location = "URL"
        match = re.search(r'window\.location\s*=\s*["\'](.*?)["\']', html)
        if match:
            return {"success": True, "direct_url": match.group(1), "source": "fastdl_redirect"}

        # Pattern 3: Google Drive URL
        gdrive = re.findall(
            r'https?://[^\s"\'<>]*(?:drive\.google\.com|video-downloads\.googleusercontent\.com|docs\.google\.com)[^\s"\'<>]*',
            html,
        )
        if gdrive:
            return {"success": True, "direct_url": gdrive[0], "source": "fastdl_gdrive"}

        return {"success": False, "error": "fastdl.beer: direct URL not found in page"}
    except Exception as e:
        return {"success": False, "error": "fastdl bypass error: {}".format(str(e))}


def bypass_filebee(url, referer=None):
    """filebee পেজ থেকে ডাইরেক্ট URL বের করে"""
    try:
        html, final_url = fetch(url, referer=referer)

        for pattern in [
            r'var\s+reurl\s*=\s*["\'](.*?)["\']',
            r'window\.location(?:\.href)?\s*=\s*["\'](.*?)["\']',
            r'window\.open\(["\'](.*?)["\']',
        ]:
            match = re.search(pattern, html)
            if match:
                dl = match.group(1)
                if "dl.php?link=" in dl:
                    parsed = urlparse(dl)
                    qs = parse_qs(parsed.query)
                    raw_url = qs.get("link", [dl])[0]
                    return {"success": True, "direct_url": dl, "raw_url": raw_url, "proxy_url": dl, "source": "filebee"}
                return {"success": True, "direct_url": dl, "source": "filebee"}

        gdrive = re.findall(
            r'https?://[^\s"\'<>]*(?:drive\.google\.com|googleusercontent\.com)[^\s"\'<>]*',
            html,
        )
        if gdrive:
            return {"success": True, "direct_url": gdrive[0], "source": "filebee_gdrive"}

        dl_links = re.findall(r'href=["\'](.*?(?:download|dl\.php|file).*?)["\']', html)
        if dl_links:
            return {"success": True, "direct_url": urljoin(final_url, dl_links[0]), "source": "filebee_link"}

        return {"success": False, "error": "filebee: direct URL not found"}
    except Exception as e:
        return {"success": False, "error": "filebee bypass error: {}".format(str(e))}


# ════════════════════════════════════════════
#  VCLOUD.BEER BYPASS
# ════════════════════════════════════════════

def _decode_double_base64(encoded_str):
    """Double base64 decode: atob(atob('...'))"""
    try:
        decoded1 = base64.b64decode(encoded_str).decode("utf-8", errors="replace")
        decoded2 = base64.b64decode(decoded1).decode("utf-8", errors="replace")
        return decoded2
    except Exception:
        try:
            return base64.b64decode(encoded_str).decode("utf-8", errors="replace")
        except Exception:
            return None


def _is_server_link(text, url):
    """সার্ভার ডাউনলোড লিংক কি না চেক"""
    text_lower = text.lower()
    url_lower = url.lower()

    # Exclude junk pages (terms, contact, copyright, privacy, login, home)
    junk_kw = ["terms", "contact", "copyright", "privacy", "policy", "login",
               "signup", "register", "about", "/faq", "dmca"]
    if any(jk in url_lower for jk in junk_kw):
        return False
    if any(jk in text_lower for jk in junk_kw):
        return False

    # Don't match bare domain homepage (e.g. https://gofile.io/copyright, https://one.vegadrive.me)
    # But allow query-string URLs like gpdl2.hubcloud.pw/?id=HASH
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if (not path or path == "") and not parsed.query:
        return False  # bare domain, not a download link

    text_kw = ["server", "fsl", "pixel", "10gbps", "download", "direct",
               "gdrive", "gofile", "filepress"]
    url_kw = ["pixeldrain.com/api/", "pixeldrain.dev/u/", "gofile.io/d/",
              "filepress", "mediafire.com/file/",
              "drive.google.com/", "workers.dev/", "dl.php", "fastdl",
              "filebee", ".r2.dev/", "vegadrive.me/d/", "vegadrive.app/s/",
              "vegadrive.co/", "vcloud.beer/", "gpdl", "r2.cloudflarestorage.com",
              "hubcloud.pw/"]
    return any(kw in text_lower for kw in text_kw) or any(kw in url_lower for kw in url_kw)


def bypass_vcloud(url, referer=None):
    """
    vcloud.beer পেজ বাইপাস:
    Double base64 decode → সার্ভার লিংক → ডীপ রিজলভ → ডাইরেক্ট URL
    """
    try:
        html, final_url = fetch(url, referer=referer)
        server_links = []

        # Method 1: Double base64
        b64_match = re.search(r"atob\s*\(\s*atob\s*\(\s*['\"]([A-Za-z0-9+/=]+)['\"]\s*\)", html)
        if b64_match:
            decoded_url = _decode_double_base64(b64_match.group(1))
            if decoded_url and decoded_url.startswith("http"):
                try:
                    inner_html, inner_final = fetch(decoded_url, referer=url)
                    for link in _apply_js_overrides(inner_html, parse_links(inner_html, inner_final)):
                        if _is_server_link(link["text"], link["url"]):
                            server_links.append(link)
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
                        for link in _apply_js_overrides(inner_html, parse_links(inner_html, inner_final)):
                            if _is_server_link(link["text"], link["url"]):
                                server_links.append(link)
                except Exception:
                    pass

        # Method 3: Direct page links
        if not server_links:
            for link in _apply_js_overrides(html, parse_links(html, final_url)):
                if _is_server_link(link["text"], link["url"]) and link["url"] != url:
                    server_links.append(link)

        # Method 4: JS redirects
        for js_url in re.findall(r'(?:window\.location(?:\.href)?\s*=|href\s*=)\s*["\'](https?://[^\s"\'<>]+)["\']', html):
            jl = js_url.lower()
            if any(d in jl for d in ["pixeldrain", "gofile", "filepress", "drive.google", "workers.dev", "dl.php", "fastdl", ".r2.dev", "gpdl", "r2.cloudflarestorage"]):
                if not any(s["url"] == js_url for s in server_links):
                    server_links.append({"url": js_url, "text": "JS Redirect", "class": ""})

        # ──── ডীপ রিজলভ প্রতিটি সার্ভার লিংক ────
        results = []

        def _deep_resolve_link(link):
            link_url = link["url"]
            link_lower = link_url.lower()
            result = {"source_url": link_url, "text": link["text"], "type": "vcloud_server"}

            try:
                if "fastdl" in link_lower:
                    r = bypass_fastdl(link_url, referer=url)
                    result.update(r)
                    if r.get("success") and r.get("direct_url"):
                        resolved = resolve_to_final(r["direct_url"], referer=link_url)
                        result["final_url"] = resolved["final_url"]
                        result["success"] = resolved.get("success", True)
                        for k in ("proxy_url", "http_status", "error", "resolved_via"):
                            if resolved.get(k) is not None and resolved.get(k) != "":
                                result[k] = resolved[k]
                elif "filebee" in link_lower:
                    r = bypass_filebee(link_url, referer=url)
                    result.update(r)
                    if r.get("success") and r.get("direct_url"):
                        resolved = resolve_to_final(r["direct_url"], referer=link_url)
                        result["final_url"] = resolved["final_url"]
                        result["success"] = resolved.get("success", True)
                        for k in ("proxy_url", "http_status", "error", "resolved_via"):
                            if resolved.get(k) is not None and resolved.get(k) != "":
                                result[k] = resolved[k]
                else:
                    resolved = resolve_to_final(link_url, referer=url)
                    result["success"] = resolved.get("success", True)
                    result["direct_url"] = link_url
                    result["final_url"] = resolved["final_url"]
                    result["resolved_via"] = resolved.get("resolved_via", "")
                    for k in ("proxy_url", "page_url", "http_status", "error"):
                        if resolved.get(k) is not None and resolved.get(k) != "":
                            result[k] = resolved[k]
            except Exception as e:
                result["success"] = False
                result["error"] = str(e)
            return result

        if server_links:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for f in as_completed([pool.submit(_deep_resolve_link, l) for l in server_links]):
                    try:
                        results.append(f.result())
                    except Exception as e:
                        results.append({"success": False, "error": str(e)})

        if not results:
            for link in parse_links(html, final_url):
                ll = link["url"].lower()
                if any(x in ll for x in ["facebook", "twitter", "instagram", "javascript:", "mailto:"]):
                    continue
                if link["url"] != url and link["url"] != final_url:
                    results.append({
                        "source_url": link["url"], "text": link["text"],
                        "type": "page_link", "success": True,
                        "direct_url": link["url"], "final_url": link["url"],
                    })

        results.sort(key=lambda x: (0 if x.get("success") else 1))
        working_count = sum(1 for r in results if r.get("success"))
        return {
            "success": working_count > 0,
            "source": url,
            "type": "vcloud",
            "total_links": len(results),
            "working_links": working_count,
            "results": results,
        }
    except HTTPError as e:
        res = {"success": False, "http_status": e.code, "error": "vcloud bypass error: HTTP Error {}: {}".format(e.code, e.reason)}
        if e.code == 403:
            res["hint"] = "Cloud/Heroku AWS IP থেকে vcloud.beer Cloudflare 403 ব্লক করতে পারে; লোকালহোস্ট/রেসিডেনশিয়াল IP থেকে ঠিকমতো কাজ করে।"
        return res
    except Exception as e:
        return {"success": False, "error": "vcloud bypass error: {}".format(str(e))}


# ════════════════════════════════════════════
#  VEGADRIVE BYPASS (CookieSession + Skydrop + Bridge)
# ════════════════════════════════════════════

def bypass_vegadrive(url, referer=None):
    """
    one.vegadrive.app / one.vegadrive.me পেজ বাইপাস:
    1. CookieJar সহ /s/<ID> পেজ ফেচ করে (.app -> .me redirect সহ)
    2. skydrop থাকলে /d/<ID>/skydrop → prepareUrl poll → linkUrl → Vegadrop 10Gbps (googleusercontent)
    3. skydrop না থাকলে (যেমন .zip pack) _NoRedirect দিয়ে internal 302 ফলো করে ready mirror URLs বের করে
    """
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
        if not m:
            return {"success": False, "source": url, "type": "vegadrive", "results": [], "error": "Invalid vegadrive URL"}

        host, fid = m.group(1), m.group(2)
        title_m = re.search(r'class="file-name-head"[^>]*>(.*?)<', v_html, re.S)
        fname = title_m.group(1).strip() if title_m else ""
        results = []

        # 1. Try Vegadrop (skydrop) — direct 10Gbps GoogleUserContent URL
        if "/{}/skydrop".format(fid) in v_html or "provider=skydrop" in v_html or '["skydrop","ready"]' in v_html:
            try:
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
                            dl_url = pdata.get("download_url")
                            if dl_url:
                                status, ok, err = _probe_download_url(dl_url)
                                if ok:
                                    results.append({
                                        "source_url": sky_url,
                                        "text": "Vegadrop 10Gbps",
                                        "type": "vegadrive",
                                        "server": "Vegadrop 10Gbps",
                                        "success": True,
                                        "direct_url": dl_url,
                                        "final_url": dl_url,
                                        "resolved_via": "vegadrive_skydrop",
                                        "file_name": fname,
                                        **({"http_status": status} if status else {}),
                                    })
                                    return {
                                        "success": True,
                                        "source": url,
                                        "type": "vegadrive",
                                        "total_links": len(results),
                                        "results": results,
                                    }
                                break
                        time.sleep(max(1.5, min(5.0, (pdata.get("retry_after_ms") or 2500) / 1000.0)))
            except Exception:
                pass

        # 2. Fallback: extract ready external mirror URLs without connecting to external CF hosts
        for prov, label in [
            ("pixeldrain", "PixelServer"),
            ("gofile", "GoFile"),
            ("transferit", "TransferIt"),
            ("buzzheavier", "Buzzheavier"),
            ("vikingfile", "VikingFile"),
        ]:
            if "/{}/{}".format(fid, prov) not in v_html:
                continue
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
                            pres = resolve_to_final(loc, referer=cur)
                            results.append({
                                "source_url": cur,
                                "text": label,
                                "type": "vegadrive",
                                "server": label,
                                "success": pres.get("success", True),
                                "direct_url": loc,
                                "final_url": pres["final_url"],
                                "resolved_via": pres.get("resolved_via", "vegadrive_pixeldrain"),
                                "file_name": fname,
                                **({"http_status": pres["http_status"]} if pres.get("http_status") else {}),
                                **({"error": pres["error"]} if pres.get("error") else {}),
                            })
                        else:
                            results.append({
                                "source_url": cur,
                                "text": label,
                                "type": "vegadrive",
                                "server": label,
                                "success": True,
                                "direct_url": loc,
                                "final_url": loc,
                                "resolved_via": "vegadrive_" + prov,
                                "file_name": fname,
                            })
                        break
                    break
                except Exception:
                    break

        working = sum(1 for r in results if r.get("success"))
        return {
            "success": working > 0,
            "source": url,
            "type": "vegadrive",
            "total_links": len(results),
            "results": results,
            "error": "vegadrive: no working links found" if working == 0 else None,
        }
    except Exception as e:
        return {"success": False, "source": url, "type": "vegadrive", "results": [], "error": "vegadrive bypass error: {}".format(str(e))}


# ════════════════════════════════════════════
#  HUBCLOUD BYPASS
# ════════════════════════════════════════════

def bypass_hubcloud(url, referer=None):
    """hubcloud / hubdrive / vegadrive পেজ বাইপাস"""
    if "vegadrive" in url.lower():
        return bypass_vegadrive(url, referer=referer)
    try:
        html, final_url = fetch(url, referer=referer)
        results = []

        # base64 patterns
        decoded_url = None
        for pat in [
            r"atob\s*\(\s*atob\s*\(\s*['\"]([A-Za-z0-9+/=]+)['\"]\s*\)",
            r"atob\s*\(\s*['\"]([A-Za-z0-9+/=]{20,})['\"]\s*\)",
        ]:
            m = re.search(pat, html)
            if m:
                decoded_url = _decode_double_base64(m.group(1))
                if decoded_url and decoded_url.startswith("http"):
                    break
                try:
                    decoded_url = base64.b64decode(m.group(1)).decode("utf-8", errors="replace")
                    if decoded_url.startswith("http"):
                        break
                except Exception:
                    decoded_url = None

        if decoded_url and decoded_url.startswith("http"):
            try:
                inner_html, inner_final = fetch(decoded_url, referer=url)
                for link in _apply_js_overrides(inner_html, parse_links(inner_html, inner_final)):
                    if _is_server_link(link["text"], link["url"]):
                        resolved = resolve_to_final(link["url"], referer=decoded_url)
                        results.append({
                            "source_url": link["url"], "text": link["text"],
                            "type": "hubcloud_server",
                            "success": resolved.get("success", True),
                            "direct_url": link["url"], "final_url": resolved["final_url"],
                            **({k: resolved[k] for k in ("proxy_url", "http_status", "error") if resolved.get(k)}),
                        })
            except Exception:
                pass

        # Direct page links
        if not results:
            for link in _apply_js_overrides(html, parse_links(html, final_url)):
                if _is_server_link(link["text"], link["url"]):
                    resolved = resolve_to_final(link["url"], referer=url)
                    results.append({
                        "source_url": link["url"], "text": link["text"],
                        "type": "hubcloud_link",
                        "success": resolved.get("success", True),
                        "direct_url": link["url"], "final_url": resolved["final_url"],
                        **({k: resolved[k] for k in ("proxy_url", "http_status", "error") if resolved.get(k)}),
                    })

        # JS redirects
        for js_url in re.findall(r'window\.location(?:\.href)?\s*=\s*["\'](https?://[^\s"\'<>]+)["\']', html):
            if not any(r["source_url"] == js_url for r in results):
                resolved = resolve_to_final(js_url, referer=url)
                results.append({
                    "source_url": js_url, "text": "JS Redirect",
                    "type": "hubcloud_redirect",
                    "success": resolved.get("success", True),
                    "direct_url": js_url, "final_url": resolved["final_url"],
                    **({k: resolved[k] for k in ("proxy_url", "http_status", "error") if resolved.get(k)}),
                })

        working = sum(1 for r in results if r.get("success"))
        return {
            "success": working > 0, "source": url, "type": "hubcloud",
            "total_links": len(results), "results": results,
            "error": "hubcloud: no download links found" if not results else None,
        }
    except Exception as e:
        return {"success": False, "error": "hubcloud bypass error: {}".format(str(e))}


# ════════════════════════════════════════════
#  NEXDRIVE BYPASS (FULL CHAIN)
# ════════════════════════════════════════════

def bypass_nexdrive(url):
    """
    nexdrive.beer → fastdl/filebee/vcloud/hubcloud সব বাইপাস +
    ডীপ রিজলভ করে ফাইনাল ডাইরেক্ট URL দেয়
    """
    try:
        html, final_url = fetch(url)
        all_links = parse_links(html, final_url)

        # লিংক ক্যাটাগরি
        categorized = {"fastdl": [], "filebee": [], "vcloud": [], "vegadrive": [], "hubcloud": [], "direct": []}

        for link in all_links:
            url_lower = link["url"].lower()
            text_lower = link["text"].lower()
            if "fastdl" in url_lower:
                categorized["fastdl"].append(link)
            elif "filebee" in url_lower:
                categorized["filebee"].append(link)
            elif "vcloud" in url_lower:
                categorized["vcloud"].append(link)
            elif any(x in url_lower for x in ["vegadrive.me", "vegadrive.app", "vegadrive.co"]):
                categorized["vegadrive"].append(link)
            elif any(x in url_lower for x in ["hubcloud", "hubdrive"]):
                categorized["hubcloud"].append(link)
            elif any(x in url_lower for x in ["drive.google", "workers.dev", "pixeldrain", "mediafire", "gofile"]):
                categorized["direct"].append(link)
            elif any(kw in text_lower for kw in ["v-cloud", "v cloud", "v-drive", "v drive", "multi", "vcloud"]):
                if "vcloud" in url_lower or any(x in text_lower for x in ["v-cloud", "v cloud", "vcloud"]):
                    categorized["vcloud"].append(link)
                elif "vegadrive" in url_lower:
                    categorized["vegadrive"].append(link)
                else:
                    categorized["hubcloud"].append(link)

        results = []

        def _bypass_one(link, link_type):
            if link_type == "fastdl":
                r = bypass_fastdl(link["url"], referer=url)
                res = {"source_url": link["url"], "text": link["text"], "type": "fastdl"}
                res.update(r)
                if r.get("success") and r.get("direct_url"):
                    resolved = resolve_to_final(r["direct_url"], referer=link["url"])
                    res["final_url"] = resolved["final_url"]
                    res["success"] = resolved.get("success", True)
                    for k in ("proxy_url", "http_status", "error"):
                        if resolved.get(k) is not None and resolved.get(k) != "":
                            res[k] = resolved[k]
                return [res]

            elif link_type == "filebee":
                r = bypass_filebee(link["url"], referer=url)
                res = {"source_url": link["url"], "text": link["text"], "type": "filebee"}
                res.update(r)
                if r.get("success") and r.get("direct_url"):
                    resolved = resolve_to_final(r["direct_url"], referer=link["url"])
                    res["final_url"] = resolved["final_url"]
                    res["success"] = resolved.get("success", True)
                    for k in ("proxy_url", "http_status", "error"):
                        if resolved.get(k) is not None and resolved.get(k) != "":
                            res[k] = resolved[k]
                return [res]

            elif link_type == "vcloud":
                r = bypass_vcloud(link["url"], referer=url)
                if r.get("results"):
                    for sub in r["results"]:
                        sub["nexdrive_url"] = url
                    return r["results"]
                return [{"source_url": link["url"], "text": link["text"], "type": "vcloud", **r}]

            elif link_type == "vegadrive":
                r = bypass_vegadrive(link["url"], referer=url)
                if r.get("results"):
                    for sub in r["results"]:
                        sub["nexdrive_url"] = url
                    return r["results"]
                return [{"source_url": link["url"], "text": link["text"], "type": "vegadrive", **r}]

            elif link_type == "hubcloud":
                r = bypass_hubcloud(link["url"], referer=url)
                if r.get("results"):
                    for sub in r["results"]:
                        sub["nexdrive_url"] = url
                    return r["results"]
                return [{"source_url": link["url"], "text": link["text"], "type": "hubcloud", **r}]

            return [{"source_url": link["url"], "text": link["text"], "type": link_type, "success": False}]

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for cat_type in ["fastdl", "filebee", "vcloud", "vegadrive", "hubcloud"]:
                for link in categorized[cat_type]:
                    futures.append(pool.submit(_bypass_one, link, cat_type))
            for f in as_completed(futures):
                try:
                    results.extend(f.result())
                except Exception as e:
                    results.append({"success": False, "error": str(e)})

        for link in categorized["direct"]:
            resolved = resolve_to_final(link["url"], referer=url)
            results.append({
                "source_url": link["url"], "text": link["text"],
                "type": "direct", "success": resolved.get("success", True),
                "direct_url": link["url"], "final_url": resolved["final_url"],
                **({k: resolved[k] for k in ("proxy_url", "http_status", "error") if resolved.get(k)}),
            })

        return {
            "success": True, "source": url, "total_links": len(results),
            "categories": {k: len(v) for k, v in categorized.items()},
            "results": results,
        }
    except Exception as e:
        return {"success": False, "error": "nexdrive bypass error: {}".format(str(e))}


# ════════════════════════════════════════════
#  MOVIE ENDPOINT — vegamovies full chain (detailed)
# ════════════════════════════════════════════

EXCLUDE_PATTERNS = re.compile(
    r"facebook|twitter|instagram|telegram\.|t\.me|youtube|whatsapp|"
    r"javascript:|mailto:|\.(?:jpg|jpeg|png|gif|svg|webp|ico|css)(?:\?|$)",
    re.IGNORECASE,
)

_QUALITY_RE = re.compile(r"\b(480p|720p|1080p|2160p|4k)\b", re.IGNORECASE)


def _detect_quality_from_html(html, nexdrive_links):
    """
    HTML-এ প্রতিটি nexdrive লিংকের অব্যবহিত আগের অংশ থেকে কোয়ালিটি (480P, 720P, 1080P, 2160p (4K)) বের করে।
    প্রতিটি nexdrive লিংকের মধ্যবর্তী HTML স্লাইস ব্যবহার করায় nested tag বা cross-section mismatch হয় না।
    """
    quality_map = {}

    # প্রতিটি nexdrive URL-এর HTML position বের করা
    nexdrive_positions = []
    for link in nexdrive_links:
        pos = html.find(link["url"])
        if pos == -1:
            pos = html.find(link["url"].replace("&", "&amp;"))
        nexdrive_positions.append({"url": link["url"], "pos": pos})

    # Position অনুযায়ী sort (একই হেডিং-এর নিচে থাকা একাধিক লিংক ঠিক রাখতে)
    ordered = sorted([n for n in nexdrive_positions if n["pos"] >= 0], key=lambda x: x["pos"])
    prev_end = 0
    last_q = ""

    for nx in ordered:
        pos = nx["pos"]
        chunk = html[max(prev_end, pos - 1500):pos]
        clean = re.sub(r"<[^>]+>", " ", chunk)
        matches = _QUALITY_RE.findall(clean)
        if matches:
            q = matches[-1].upper()
            if q == "4K":
                q = "2160p (4K)"
            last_q = q
        else:
            q = last_q
        if q:
            quality_map[nx["url"]] = q
        prev_end = pos + len(nx["url"])

    return quality_map


def extract_movie(url):
    """vegamovies পেজ → nexdrive → সব বাইপাস → ডাইরেক্ট URL (detailed output)"""
    try:
        html, final_url = fetch(url)
        all_links = parse_links(html, final_url)
        nexdrive_links = [l for l in all_links if "nexdrive" in l["url"].lower()]

        # ──── কোয়ালিটি ডিটেকশন (HTML position based) ────
        quality_map = {}  # nexdrive_url -> quality
        if nexdrive_links:
            quality_map = _detect_quality_from_html(html, nexdrive_links)

        if not nexdrive_links:
            fastdl_links = [l for l in all_links if "fastdl" in l["url"].lower()]
            filebee_links = [l for l in all_links if "filebee" in l["url"].lower()]
            vcloud_links = [l for l in all_links if "vcloud" in l["url"].lower()]

            results = []
            def _bd(link, ltype):
                if ltype == "fastdl":
                    r = bypass_fastdl(link["url"], referer=url)
                elif ltype == "filebee":
                    r = bypass_filebee(link["url"], referer=url)
                elif ltype == "vcloud":
                    r = bypass_vcloud(link["url"], referer=url)
                    if r.get("results"):
                        return r["results"]
                    return [{"source_url": link["url"], "text": link["text"], "type": ltype, **r}]
                else:
                    r = {}
                return [{"source_url": link["url"], "text": link["text"], "type": ltype, **r}]

            with ThreadPoolExecutor(max_workers=10) as pool:
                futs = []
                for l in fastdl_links:
                    futs.append(pool.submit(_bd, l, "fastdl"))
                for l in filebee_links:
                    futs.append(pool.submit(_bd, l, "filebee"))
                for l in vcloud_links:
                    futs.append(pool.submit(_bd, l, "vcloud"))
                for f in as_completed(futs):
                    try:
                        items = f.result()
                        results.extend(items if isinstance(items, list) else [items])
                    except Exception as e:
                        results.append({"success": False, "error": str(e)})

            return {"success": True, "source": url, "total_links": len(results), "results": results}

        all_results = []

        def _process_nexdrive(nx_link):
            bypass_data = bypass_nexdrive(nx_link["url"])
            quality = quality_map.get(nx_link["url"], "")
            items = []
            if bypass_data.get("success") and bypass_data.get("results"):
                for r in bypass_data["results"]:
                    r["nexdrive_url"] = nx_link["url"]
                    r["nexdrive_text"] = nx_link["text"]
                    r["quality"] = quality
                    items.append(r)
            else:
                items.append({
                    "nexdrive_url": nx_link["url"], "nexdrive_text": nx_link["text"],
                    "quality": quality,
                    "success": False, "error": bypass_data.get("error", "bypass failed"),
                })
            return items

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_process_nexdrive, nx) for nx in nexdrive_links]
            for f in futures:
                try:
                    all_results.extend(f.result())
                except Exception as e:
                    all_results.append({"success": False, "error": str(e)})

        return {
            "success": True, "source": url,
            "total_nexdrive_links": len(nexdrive_links),
            "total_direct_links": sum(1 for r in all_results if r.get("success")),
            "results": all_results,
        }
    except Exception as e:
        return {"success": False, "error": "movie extract error: {}".format(str(e))}


# ════════════════════════════════════════════
#  /direct ENDPOINT — ONE API, CLEAN OUTPUT
#  vegamovies URL দিন → শুধু ফাইনাল ডাইরেক্ট URL পান
# ════════════════════════════════════════════

def extract_direct(url):
    """
    ONE API: vegamovies URL → পুরো চেইন বাইপাস → শুধু ক্লিন ডাইরেক্ট ডাউনলোড URL

    চেইন: vegamovies → nexdrive → vcloud/fastdl/filebee/hubcloud
           → FSL Server/PixelServer/10Gbps → ফাইনাল ডাইরেক্ট URL
    """
    # প্রথমে /movie chain চালাও
    movie_data = extract_movie(url)

    if not movie_data.get("success"):
        return movie_data

    # সফল results থেকে ক্লিন ডাইরেক্ট URLs বের করো
    direct_links = []
    seen_urls = set()

    for result in movie_data.get("results", []):
        if not result.get("success"):
            continue

        # final_url সবচেয়ে ডীপ রিজলভড
        final = result.get("final_url") or result.get("direct_url") or result.get("proxy_url")
        if not final:
            continue

        # ডুপ্লিকেট ফিল্টার
        if final in seen_urls:
            continue
        seen_urls.add(final)

        # Junk URL exclude (telegram, terms, contact, copyright, bare domains)
        final_lower = final.lower()
        if "t.me/" in final_lower:
            continue
        parsed_final = urlparse(final)
        path_clean = parsed_final.path.lower().rstrip("/")
        if path_clean in {"/terms", "/contact", "/copyright", "/privacy", "/policy",
                          "/login", "/signup", "/register", "/about", "/dmca", "/faq"}:
            continue
        # Bare domain homepages (e.g. https://one.vegadrive.me, https://gofile.io)
        if not path_clean and not parsed_final.query:
            continue

        # ক্লিন entry বানাও
        entry = {
            "url": final,
            "server": _detect_server_name(result, final),
            "quality": _detect_quality(result),
        }

        # proxy_url যদি থাকে (Google Drive URL-এর জন্য)
        if result.get("proxy_url") and result["proxy_url"] != final:
            entry["proxy_url"] = result["proxy_url"]
        if result.get("raw_url") and result["raw_url"] != final:
            entry["raw_url"] = result["raw_url"]

        direct_links.append(entry)

    return {
        "success": len(direct_links) > 0,
        "source": url,
        "total_direct_links": len(direct_links),
        "direct_links": direct_links,
    }


def _detect_server_name(result, url):
    """সার্ভারের নাম ডিটেক্ট"""
    explicit = result.get("server", "")
    if explicit and explicit != "Unknown":
        return explicit

    text = result.get("text", "").lower()
    url_lower = url.lower()

    if "fslv2" in text or "r2.cloudflarestorage.com" in url_lower:
        return "FSLv2 Server"
    if "fsl" in text:
        return "FSL Server"
    if "pixel" in text or "pixeldrain" in url_lower or "pixelserver" in text:
        return "PixelServer"
    if "10gbps" in text or "cryptonewz" in url_lower or "gpdl" in url_lower:
        return "Server 10Gbps"
    if "vegadrop" in text or "vegadrive" in url_lower or "skydrop" in url_lower or result.get("resolved_via") == "vegadrive_skydrop":
        return "Vegadrop 10Gbps"
    if "gofile" in text or "gofile.io" in url_lower:
        return "GoFile"
    if "transferit" in text or "transfer.it" in url_lower:
        return "TransferIt"
    if "buzzheavier" in text or "bzzhr.co" in url_lower or "buzzheavier" in url_lower:
        return "Buzzheavier"
    if "vikingfile" in text or "vikingfile" in url_lower:
        return "VikingFile"
    if "filepress" in text or "filebee" in url_lower:
        return "Filepress"
    if "g-direct" in text or "instant" in text or "fastdl" in url_lower:
        return "G-Direct Instant"
    if ".r2.dev" in url_lower:
        return "CDN Direct"
    if "drive.google" in url_lower or "googleusercontent" in url_lower:
        return "Google Drive"
    if "mediafire" in url_lower:
        return "MediaFire"
    if "mega.nz" in url_lower:
        return "Mega"

    return result.get("type", "Unknown")


def _detect_quality(result):
    """কোয়ালিটি ডিটেক্ট — quality ফিল্ড থাকলে সেটা ব্যবহার, না হলে text থেকে"""
    # সরাসরি quality ফিল্ড থাকলে (HTML position based)
    if result.get("quality"):
        return result["quality"]
    # Text/nexdrive_text থেকে ডিটেক্ট
    text = (result.get("text", "") + " " + result.get("nexdrive_text", "")).lower()
    for q in ["2160p", "4k", "1080p", "720p", "480p", "360p"]:
        if q in text:
            return q.upper() if q == "4k" else q
    return ""


# ════════════════════════════════════════════
#  OTHER ENDPOINTS
# ════════════════════════════════════════════

def extract_download_links(url):
    """পেজ থেকে ডাউনলোড লিংক এক্সট্রাক্ট"""
    try:
        html, final_url = fetch(url)
        all_links = parse_links(html, final_url)
        download_links = []
        dl_kw_url = re.compile(
            r"nexdrive|hubcloud|hubdrive|gdtot|filepress|filebee|fastdl|"
            r"gdflix|gofile|mediafire|mega\.nz|pixeldrain|vcloud|"
            r"drive\.google|workers\.dev|"
            r"\.mkv|\.mp4|\.zip|\.rar|/download|/dl\?",
            re.IGNORECASE,
        )
        dl_kw_text = [
            "download", "direct", "480p", "720p", "1080p", "2160p", "4k",
            "g-direct", "instant", "batch", "zip", "gdrive",
            "v-cloud", "v cloud", "multi", "hubcloud",
            "fsl server", "pixelserver", "server", "gofile", "filepress",
        ]
        for link in all_links:
            if EXCLUDE_PATTERNS.search(link["url"]):
                continue
            is_dl = bool(dl_kw_url.search(link["url"]))
            if not is_dl:
                is_dl = any(kw in link["text"].lower() for kw in dl_kw_text)
            if is_dl:
                download_links.append(link)
        return {
            "success": True, "source_url": url,
            "total_links_found": len(all_links),
            "download_links_count": len(download_links),
            "download_links": download_links,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def follow_redirects(url):
    """URL-এর রিডাইরেক্ট ফলো করে"""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    chain = [url]
    current = url
    ctx = _ssl_ctx()
    for _ in range(MAX_REDIRECTS):
        try:
            req = Request(current, method="HEAD")
            req.add_header("User-Agent", USER_AGENT)
            try:
                resp = urlopen(req, timeout=TIMEOUT, context=ctx)
                final = resp.geturl()
                resp.close()
            except HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    loc = e.headers.get("Location")
                    final = urljoin(current, loc) if loc else None
                else:
                    break
            if final and final != current:
                chain.append(final)
                current = final
            else:
                break
        except Exception:
            break
    return {"success": True, "final_url": current, "chain": chain, "redirects": len(chain) - 1}


# ════════════════════════════════════════════
#  API SERVER
# ════════════════════════════════════════════

class APIHandler(BaseHTTPRequestHandler):

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

    def _get_post_url(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len else "{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
        return data.get("url")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle(self, url):
        path = urlparse(self.path).path

        # /direct — ONE API, পুরো চেইন, ক্লিন আউটপুট
        if path == "/direct":
            if not url:
                self._send_json({"success": False, "error": "url parameter required"}, 400)
                return
            self._send_json(extract_direct(url))

        # /movie — detailed output
        elif path == "/movie":
            if not url:
                self._send_json({"success": False, "error": "url parameter required"}, 400)
                return
            self._send_json(extract_movie(url))

        # /bypass
        elif path == "/bypass":
            if not url:
                self._send_json({"success": False, "error": "url parameter required"}, 400)
                return
            ul = url.lower()
            if "nexdrive" in ul:
                self._send_json(bypass_nexdrive(url))
            elif "fastdl" in ul:
                self._send_json(bypass_fastdl(url))
            elif "filebee" in ul:
                self._send_json(bypass_filebee(url))
            elif "vcloud" in ul:
                self._send_json(bypass_vcloud(url))
            elif "vegadrive" in ul:
                self._send_json(bypass_vegadrive(url))
            elif any(x in ul for x in ["hubcloud", "hubdrive"]):
                self._send_json(bypass_hubcloud(url))
            else:
                self._send_json(bypass_nexdrive(url))

        # /api/extract
        elif path == "/api/extract":
            if not url:
                self._send_json({"success": False, "error": "url parameter required"}, 400)
                return
            self._send_json(extract_download_links(url))

        # /api/resolve
        elif path == "/api/resolve":
            if not url:
                self._send_json({"success": False, "error": "url parameter required"}, 400)
                return
            self._send_json(follow_redirects(url))

        else:
            self._send_json({"error": "Not Found"}, 404)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send_json({
                "app": "Link Extractor & Bypass API",
                "version": "6.0.0",
                "endpoints": {
                    "/direct?url=<vegamovies_url>": "ONE API - full chain bypass -> clean direct download URLs only",
                    "/movie?url=<vegamovies_url>": "full chain bypass (detailed output)",
                    "/bypass?url=<url>": "nexdrive/fastdl/filebee/vcloud/hubcloud bypass",
                    "/api/extract?url=<URL>": "page download links extract",
                    "/api/resolve?url=<URL>": "follow redirects -> final URL",
                },
                "supported_sites": [
                    "vegamovies.lease", "nexdrive.beer", "fastdl.beer",
                    "filebee.xyz/top", "vcloud.beer", "hubcloud",
                ],
            })
            return
        self._handle(self._get_url_param())

    def do_POST(self):
        url = self._get_post_url() or self._get_url_param()
        self._handle(url)

    def log_message(self, format, *args):
        print("  [{}] {}".format(self.log_date_time_string(), args[0]))


# ─── Start Server ───
def start_server():
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    server = HTTPServer((HOST, PORT), APIHandler)
    print("=" * 60)
    print("  Link Extractor & Bypass API v6.0")
    print("=" * 60)
    print("  Server: http://localhost:{}".format(PORT))
    print()
    print("  Endpoints:")
    print("    /direct?url=<vegamovies_url> -> ONE API (clean output)")
    print("    /movie?url=<vegamovies_url>  -> Full chain (detailed)")
    print("    /bypass?url=<nexdrive_url>   -> Single bypass")
    print("    /api/extract?url=<URL>       -> Extract links")
    print("    /api/resolve?url=<URL>       -> Follow redirects")
    print()
    print("  Supported: nexdrive, fastdl, filebee, vcloud, hubcloud")
    print()
    print("  Stop: Ctrl+C")
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server shutting down...")
        server.shutdown()


if __name__ == "__main__":
    start_server()
