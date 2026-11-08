"""
Link Extractor & Bypass API v6.3
=================================
vegamovies / nexdrive.beer / fastdl.beer / filebee / vcloud.beer / vegadrive / hubcloud
সব চেইন বাইপাস করে ক্লিন ডাইরেক্ট ডাউনলোড লিংক বের করে (কোনো dl.php র‍্যাপার ছাড়া)।
মুভি ও সিরিজ (Season/Episode pack + ?full=1) দুইটাই সাপোর্ট করে।

Endpoints:
  GET /bypass?url=<URL>               -> ONE API — vegamovies/nexdrive/vcloud/fastdl/vegadrive সব বাইপাস
  GET /direct?url=<URL>               -> পুরো চেইন বাইপাস → শুধু ক্লিন ডাইরেক্ট URL (&full=1 দিয়ে ফুল সিরিজ)
  GET /movie?url=<vegamovies_url>     -> nexdrive + bypass সব একসাথে (detailed)
  GET /api/extract?url=<URL>          -> পেজ থেকে ডাউনলোড লিংক এক্সট্রাক্ট
  GET /api/resolve?url=<URL>          -> রিডাইরেক্ট ফলো করে ফাইনাল URL

ব্যবহার:
  python link_extractor.py

কোনো pip install লাগে না — Python বিল্ট-ইন লাইব্রেরি দিয়ে চলে।
"""

import base64
import html as _html
import json
import os
import re
import ssl
import sys
import io
import time
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, parse_qs, quote, unquote
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener, HTTPSHandler, HTTPCookieProcessor
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


# ─── SSL & Opener Helpers ───
def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _build_opener(*extra_handlers):
    """PROXY_URL / HTTPS_PROXY / HTTP_PROXY এবং SSL context সহ opener বানায়।"""
    handlers = [HTTPSHandler(context=_ssl_ctx()), *extra_handlers]
    proxy = os.environ.get("PROXY_URL", "").strip()
    if proxy:
        handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
    return build_opener(*handlers)


def _unwrap_dl_php(url):
    """dl.php?link=... র‍্যাপার থাকলে আসল ফাইনাল ডাউনলোড URL বের করে।"""
    if not url:
        return url
    ul = url.lower()
    if "dl.php" in ul and "link=" in ul:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        link_val = qs.get("link", [None])[0]
        if link_val:
            return link_val
    return url


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
    opener = _build_opener(_NoRedirect())

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
def fetch(url, referer=None, extra_headers=None, retries=1):
    """URL ফেচ করে (HTML, final_url) রিটার্ন করে (transient timeout/5xx হলে ১ বার রিট্রাই সহ)"""
    last_err = None
    for attempt in range(retries + 1):
        req = Request(url)
        for k, v in HEADERS.items():
            req.add_header(k, v)
        if referer:
            req.add_header("Referer", referer)
        if extra_headers:
            for k, v in extra_headers.items():
                req.add_header(k, v)

        try:
            resp = _build_opener().open(req, timeout=TIMEOUT)
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
        except HTTPError as e:
            if e.code in (403, 404, 410) or attempt >= retries:
                raise
            last_err = e
            time.sleep(0.8)
        except Exception as e:
            last_err = e
            if attempt >= retries:
                raise
            time.sleep(0.8)
    raise last_err


def fetch_head(url, referer=None):
    """HEAD request করে final URL বের করে (redirect follow সহ)"""
    try:
        req = Request(url, method="HEAD")
        req.add_header("User-Agent", USER_AGENT)
        if referer:
            req.add_header("Referer", referer)
        resp = _build_opener().open(req, timeout=TIMEOUT)
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


def _probe_download_url(url, timeout=6):
    """
    ফাইনাল ডাউনলোড URL আসলেই ডাউনলোডযোগ্য কিনা (403/404 দেয় কিনা) তা চেক করে।
    Range: bytes=0-15 ব্যবহার করে (টোকেন খরচ হয় না এবং খুব দ্রুত)।
    রিটার্ন: (http_status, is_working, error_msg)
    """
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-15"})
        resp = _build_opener().open(req, timeout=timeout)
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
    dl.php?link=... → আসল ফাইনাল URL বের করে (dl.php র‍্যাপার ছাড়া)
    vegadrive.me/app → cookie session + skydrop/mirror bridge
    pixeldrain.dev/u/... → API URL বানায় + probe
    gpdl2.hubcloud.pw → workers.dev → Google Drive raw URL → probe
    """
    url_lower = url.lower()

    # dl.php?link=... থেকে আসল link extract (fastdl, cryptonewz etc.)
    if "dl.php?" in url_lower and "link=" in url_lower:
        link_val = _unwrap_dl_php(url)
        if link_val and link_val != url:
            status, ok, err = _probe_download_url(link_val)
            res = {
                "final_url": link_val,
                "resolved_via": "dl.php_param",
                "success": ok,
            }
            if status:
                res["http_status"] = status
            if not ok:
                res["error"] = err
            return res

    # 10Gbps Server (gpdl2.hubcloud.pw → workers.dev → dl.php?link=GOOGLE_DRIVE)
    if "gpdl" in url_lower and "hubcloud" in url_lower:
        try:
            cur = url
            ref = referer
            for _ in range(4):
                nxt = fetch_redirect_location(cur, referer=ref)
                if not nxt:
                    break
                nxt = urljoin(cur, nxt)
                if nxt == cur:
                    break
                if "dl.php" in nxt.lower() and "link=" in nxt.lower():
                    link_val = _unwrap_dl_php(nxt)
                    if link_val and link_val != nxt:
                        status, ok, err = _probe_download_url(link_val)
                        res = {
                            "final_url": link_val,
                            "resolved_via": "10gbps_bypass",
                            "success": ok,
                        }
                        if status:
                            res["http_status"] = status
                        if not ok:
                            res["error"] = err
                        return res
                ref = cur
                cur = nxt
            if cur != url:
                clean_cur = _unwrap_dl_php(cur)
                status, ok, err = _probe_download_url(clean_cur)
                res = {"final_url": clean_cur, "resolved_via": "10gbps_redirect", "success": ok}
                if status:
                    res["http_status"] = status
                if not ok:
                    res["error"] = err
                return res
        except Exception:
            pass
        return {"final_url": url, "resolved_via": "10gbps_fallback", "success": False, "error": "10Gbps bypass failed"}

    # workers.dev → 10Gbps bypass ট্রাই
    if "workers.dev" in url_lower and "?id=" in url_lower:
        try:
            loc = fetch_redirect_location(url, referer=referer)
            if loc and "dl.php" in loc.lower() and "link=" in loc.lower():
                link_val = _unwrap_dl_php(loc)
                if link_val and link_val != loc:
                    status, ok, err = _probe_download_url(link_val)
                    res = {"final_url": link_val, "resolved_via": "workers_bypass", "success": ok}
                    if status:
                        res["http_status"] = status
                    if not ok:
                        res["error"] = err
                    return res
            return {"final_url": _unwrap_dl_php(loc) if loc else url, "resolved_via": "workers_redirect", "success": True}
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

    # gofile & direct mirrors → keep as-is
    if "gofile.io" in url_lower:
        return {"final_url": url, "resolved_via": "gofile_direct", "success": True}
    if any(x in url_lower for x in ["vikingfile.", "transfer.it/", "buzzheavier", "bzzhr.co"]):
        return {"final_url": url, "resolved_via": "mirror_direct", "success": True}

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
    fastdl.beer/embed.php পেজ থেকে ক্লিন ডাইরেক্ট ডাউনলোড URL বের করে (dl.php র‍্যাপার ছাড়া)।
    """
    try:
        html, _ = fetch(url, referer=referer)

        def _finish(found_url, src):
            final = _unwrap_dl_php(found_url)
            res = {"success": True, "direct_url": final, "final_url": final, "source": src}
            fl = final.lower()
            if final != found_url or "googleusercontent.com" in fl or ".r2." in fl:
                status, ok, err = _probe_download_url(final)
                res["success"] = ok
                if status:
                    res["http_status"] = status
                if not ok:
                    res["error"] = err
            return res

        # Pattern 1: var reurl = "URL";
        match = re.search(r'var\s+reurl\s*=\s*["\'](.*?)["\']', html)
        if match:
            return _finish(match.group(1), "fastdl")

        # Pattern 2: window.location = "URL"
        match = re.search(r'window\.location\s*=\s*["\'](.*?)["\']', html)
        if match:
            return _finish(match.group(1), "fastdl_redirect")

        # Pattern 3: Google Drive URL
        gdrive = re.findall(
            r'https?://[^\s"\'<>]*(?:drive\.google\.com|video-downloads\.googleusercontent\.com|docs\.google\.com)[^\s"\'<>]*',
            html,
        )
        if gdrive:
            return _finish(gdrive[0], "fastdl_gdrive")

        return {"success": False, "error": "fastdl.beer: direct URL not found in page"}
    except Exception as e:
        return {"success": False, "error": "fastdl bypass error: {}".format(str(e))}


def bypass_filebee(url, referer=None):
    """filebee পেজ থেকে ক্লিন ডাইরেক্ট URL বের করে"""
    try:
        html, final_url = fetch(url, referer=referer)

        def _finish(found_url, src):
            final = _unwrap_dl_php(found_url)
            res = {"success": True, "direct_url": final, "final_url": final, "source": src}
            fl = final.lower()
            if final != found_url or "googleusercontent.com" in fl or ".r2." in fl:
                status, ok, err = _probe_download_url(final)
                res["success"] = ok
                if status:
                    res["http_status"] = status
                if not ok:
                    res["error"] = err
            return res

        for pattern in [
            r'var\s+reurl\s*=\s*["\'](.*?)["\']',
            r'window\.location(?:\.href)?\s*=\s*["\'](.*?)["\']',
            r'window\.open\(["\'](.*?)["\']',
        ]:
            match = re.search(pattern, html)
            if match:
                return _finish(match.group(1), "filebee")

        gdrive = re.findall(
            r'https?://[^\s"\'<>]*(?:drive\.google\.com|googleusercontent\.com)[^\s"\'<>]*',
            html,
        )
        if gdrive:
            return _finish(gdrive[0], "filebee_gdrive")

        dl_links = re.findall(r'href=["\'](.*?(?:download|dl\.php|file).*?)["\']', html)
        if dl_links:
            return _finish(urljoin(final_url, dl_links[0]), "filebee_link")

        return {"success": False, "error": "filebee: direct URL not found"}
    except HTTPError as e:
        res = {"success": False, "http_status": e.code, "error": "filebee bypass error: HTTP Error {}: {}".format(e.code, e.reason)}
        if e.code == 403:
            res["hint"] = "filebee Cloudflare-সুরক্ষিত (HTTP 403)।"
        return res
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
                    if r.get("success") and r.get("direct_url") and r.get("http_status") is None:
                        resolved = resolve_to_final(r["direct_url"], referer=link_url)
                        result["direct_url"] = resolved["final_url"]
                        result["final_url"] = resolved["final_url"]
                        result["success"] = resolved.get("success", True)
                        for k in ("http_status", "error", "resolved_via"):
                            if resolved.get(k) is not None and resolved.get(k) != "":
                                result[k] = resolved[k]
                elif "filebee" in link_lower:
                    r = bypass_filebee(link_url, referer=url)
                    result.update(r)
                    if r.get("success") and r.get("direct_url") and r.get("http_status") is None:
                        resolved = resolve_to_final(r["direct_url"], referer=link_url)
                        result["direct_url"] = resolved["final_url"]
                        result["final_url"] = resolved["final_url"]
                        result["success"] = resolved.get("success", True)
                        for k in ("http_status", "error", "resolved_via"):
                            if resolved.get(k) is not None and resolved.get(k) != "":
                                result[k] = resolved[k]
                else:
                    resolved = resolve_to_final(link_url, referer=url)
                    result["success"] = resolved.get("success", True)
                    result["direct_url"] = resolved["final_url"]
                    result["final_url"] = resolved["final_url"]
                    result["resolved_via"] = resolved.get("resolved_via", "")
                    for k in ("page_url", "http_status", "error"):
                        if resolved.get(k) is not None and resolved.get(k) != "":
                            result[k] = resolved[k]
            except Exception as e:
                result["success"] = False
                result["error"] = str(e)
            return result

        if server_links:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(_deep_resolve_link, l) for l in server_links]
                for f in futures:
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

def bypass_vegadrive(url, referer=None, max_polls=6):
    """
    one.vegadrive.app / one.vegadrive.me পেজ বাইপাস:
    1. CookieJar সহ /s/<ID> পেজ ফেচ করে (.app -> .me redirect সহ)
    2. skydrop থাকলে /d/<ID>/skydrop → prepareUrl poll → linkUrl → Vegadrop 10Gbps (googleusercontent)
    3. skydrop না থাকলে (যেমন .zip pack) _NoRedirect দিয়ে internal 302 ফলো করে ready mirror URLs বের করে
    """
    try:
        cj = http.cookiejar.CookieJar()
        op_follow = _build_opener(HTTPCookieProcessor(cj))
        op_nr = _build_opener(_NoRedirect(), HTTPCookieProcessor(cj))

        vg_timeout = 12 if max_polls <= 3 else TIMEOUT

        def _vg(u, ref=None, accept="text/html,*/*"):
            req = Request(u, headers={"User-Agent": USER_AGENT, "Accept": accept})
            if ref:
                req.add_header("Referer", ref)
            r = op_follow.open(req, timeout=vg_timeout)
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
                    for attempt in range(max_polls):
                        try:
                            pdata = json.loads(_vg(prep, ref=sky_url, accept="application/json")[0])
                        except Exception:
                            if attempt + 1 < max_polls:
                                time.sleep(1.5)
                            continue
                        if pdata.get("ready") is True:
                            if not pdata.get("download_url"):
                                try:
                                    pdata = json.loads(_vg(link_ep, ref=sky_url, accept="application/json")[0])
                                except Exception:
                                    if attempt + 1 < max_polls:
                                        time.sleep(1.5)
                                    continue
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
                        if attempt + 1 < max_polls:
                            time.sleep(max(1.5, min(3.0, (pdata.get("retry_after_ms") or 2000) / 1000.0)))
            except Exception:
                pass

        # 2. Fallback: extract ready external mirror URLs in parallel without connecting to external CF hosts
        def _resolve_vg_mirror(prov, label):
            cur = "https://{}/d/{}/{}".format(host, fid, prov)
            for _ in range(5):
                try:
                    op_nr.open(Request(cur, headers={"User-Agent": USER_AGENT, "Referer": v_final}), timeout=8)
                    return None
                except HTTPError as he:
                    loc = he.headers.get("Location")
                    if he.code in (301, 302, 303, 307, 308) and loc:
                        loc = urljoin(cur, loc)
                        if "vegadrive" in loc.lower():
                            cur = loc
                            continue
                        if "pixeldrain" in loc.lower() and "/u/" in loc:
                            pres = resolve_to_final(loc, referer=cur)
                            return {
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
                            }
                        return {
                            "source_url": cur,
                            "text": label,
                            "type": "vegadrive",
                            "server": label,
                            "success": True,
                            "direct_url": loc,
                            "final_url": loc,
                            "resolved_via": "vegadrive_" + prov,
                            "file_name": fname,
                        }
                    return None
                except Exception:
                    return None
            return None

        mirrors_present = [
            (prov, label) for prov, label in [
                ("pixeldrain", "PixelServer"),
                ("gofile", "GoFile"),
                ("transferit", "TransferIt"),
                ("buzzheavier", "Buzzheavier"),
                ("vikingfile", "VikingFile"),
            ] if "/{}/{}".format(fid, prov) in v_html
        ]
        if mirrors_present:
            with ThreadPoolExecutor(max_workers=len(mirrors_present)) as mpool:
                mfuts = [mpool.submit(_resolve_vg_mirror, prov, label) for prov, label in mirrors_present]
                for mf in mfuts:
                    try:
                        mres = mf.result()
                        if mres:
                            results.append(mres)
                    except Exception:
                        pass

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
                            "direct_url": resolved["final_url"], "final_url": resolved["final_url"],
                            **({k: resolved[k] for k in ("http_status", "error") if resolved.get(k)}),
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
                        "direct_url": resolved["final_url"], "final_url": resolved["final_url"],
                        **({k: resolved[k] for k in ("http_status", "error") if resolved.get(k)}),
                    })

        # JS redirects
        for js_url in re.findall(r'window\.location(?:\.href)?\s*=\s*["\'](https?://[^\s"\'<>]+)["\']', html):
            if not any(r["source_url"] == js_url for r in results):
                resolved = resolve_to_final(js_url, referer=url)
                results.append({
                    "source_url": js_url, "text": "JS Redirect",
                    "type": "hubcloud_redirect",
                    "success": resolved.get("success", True),
                    "direct_url": resolved["final_url"], "final_url": resolved["final_url"],
                    **({k: resolved[k] for k in ("http_status", "error") if resolved.get(k)}),
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

_EPISODE_RE = re.compile(r"(?:Episodes?|Ep\.?)\s*[:\-]?\s*0*(\d+)\b", re.IGNORECASE)


def _detect_episode_from_html(html, links):
    """nexdrive পেজে '-:Episodes: 1:-' ইত্যাদি থেকে প্রতিটি লিংকের এপিসোড নম্বর বের করে।"""
    ep_map = {}
    positions = []
    for link in links:
        pos = html.find(link["url"])
        if pos == -1:
            pos = html.find(link["url"].replace("&", "&amp;"))
        if pos >= 0:
            positions.append({"url": link["url"], "pos": pos})
    positions.sort(key=lambda x: x["pos"])
    prev_end = 0
    last_ep = ""
    for item in positions:
        pos = item["pos"]
        chunk = html[max(prev_end, pos - 400):pos]
        clean = re.sub(r"<[^>]+>", " ", chunk)
        matches = _EPISODE_RE.findall(clean)
        if matches:
            last_ep = "Episode {}".format(int(matches[-1]))
        if last_ep:
            ep_map[item["url"]] = last_ep
        prev_end = pos + len(item["url"])
    return ep_map


def _ep_sort_key(item):
    ep = item.get("episode", "")
    m = re.search(r"(\d+)", ep) if ep else None
    return int(m.group(1)) if m else 999999


def _clean_direct_results(raw_results, sort_by_episode=False):
    """
    বাইপাস রেজাল্ট থেকে শুধু ক্লিন ওয়ার্কিং ডাইরেক্ট URLs বের করে (কোনো dl.php বা ডেড লিংক ছাড়া)
    এবং 403 ওয়ার্নিং সংগ্রহ করে।
    """
    direct_links = []
    seen_urls = set()
    warnings = []
    seen_warnings = set()

    ordered_results = raw_results
    if sort_by_episode:
        ordered_results = [
            r for _, r in sorted(
                enumerate(raw_results),
                key=lambda pair: (_ep_sort_key(pair[1]), pair[0]),
            )
        ]

    for result in ordered_results:
        if not result.get("success"):
            if result.get("http_status") == 403 and "vcloud" in (result.get("source_url", "") + result.get("type", "")).lower():
                w = result.get("error") or result.get("hint") or "vcloud.beer HTTP 403"
                if result.get("hint") and result["hint"] not in w:
                    w = "{} — {}".format(w, result["hint"])
                if w not in seen_warnings:
                    seen_warnings.add(w)
                    warnings.append(w)
            continue

        # final_url সবচেয়ে ডীপ রিজলভড; dl.php?link= থাকলে unwrap নিশ্চিত করো
        raw_candidate = result.get("final_url") or result.get("direct_url")
        final = _unwrap_dl_php(raw_candidate)
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

        entry = {
            "url": final,
            "server": _detect_server_name(result, final),
        }
        q = _detect_quality(result)
        if q:
            entry["quality"] = q
        if result.get("season"):
            entry["season"] = result["season"]
        if result.get("episode"):
            entry["episode"] = result["episode"]

        direct_links.append(entry)

    return direct_links, warnings


def bypass_nexdrive(url):
    """
    nexdrive.beer → fastdl/filebee/vcloud/vegadrive/hubcloud সব বাইপাস +
    ডীপ রিজলভ করে ফাইনাল ডাইরেক্ট URL দেয় (সিরিজের এপিসোড নম্বর সহ)
    """
    try:
        html, final_url = fetch(url)
        all_links = parse_links(html, final_url)
        ep_map = _detect_episode_from_html(html, all_links)

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
            elif any(x in url_lower for x in [
                "drive.google", "workers.dev", "pixeldrain", "mediafire",
                "gofile", "vikingfile.", "buzzheavier", "bzzhr.co", "transfer.it",
            ]):
                categorized["direct"].append(link)
            elif any(kw in text_lower for kw in ["v-cloud", "v cloud", "v-drive", "v drive", "multi", "vcloud"]):
                if "vcloud" in url_lower or any(x in text_lower for x in ["v-cloud", "v cloud", "vcloud"]):
                    categorized["vcloud"].append(link)
                elif "vegadrive" in url_lower:
                    categorized["vegadrive"].append(link)
                else:
                    categorized["hubcloud"].append(link)

        results = []
        vd_polls = 2 if len(categorized["vegadrive"]) > 4 else (3 if len(categorized["vegadrive"]) > 1 else 6)

        def _bypass_one(link, link_type):
            ep = ep_map.get(link["url"], "")
            if link_type == "fastdl":
                r = bypass_fastdl(link["url"], referer=url)
                res = {"source_url": link["url"], "text": link["text"], "type": "fastdl"}
                if ep:
                    res["episode"] = ep
                res.update(r)
                if r.get("success") and r.get("direct_url") and r.get("http_status") is None:
                    resolved = resolve_to_final(r["direct_url"], referer=link["url"])
                    res["direct_url"] = resolved["final_url"]
                    res["final_url"] = resolved["final_url"]
                    res["success"] = resolved.get("success", True)
                    for k in ("http_status", "error"):
                        if resolved.get(k) is not None and resolved.get(k) != "":
                            res[k] = resolved[k]
                return [res]

            elif link_type == "filebee":
                r = bypass_filebee(link["url"], referer=url)
                res = {"source_url": link["url"], "text": link["text"], "type": "filebee"}
                if ep:
                    res["episode"] = ep
                res.update(r)
                if r.get("success") and r.get("direct_url") and r.get("http_status") is None:
                    resolved = resolve_to_final(r["direct_url"], referer=link["url"])
                    res["direct_url"] = resolved["final_url"]
                    res["final_url"] = resolved["final_url"]
                    res["success"] = resolved.get("success", True)
                    for k in ("http_status", "error"):
                        if resolved.get(k) is not None and resolved.get(k) != "":
                            res[k] = resolved[k]
                return [res]

            elif link_type == "vcloud":
                r = bypass_vcloud(link["url"], referer=url)
                if r.get("results"):
                    for sub in r["results"]:
                        sub["nexdrive_url"] = url
                        if ep:
                            sub["episode"] = ep
                    return r["results"]
                item = {"source_url": link["url"], "text": link["text"], "type": "vcloud", **r}
                if ep:
                    item["episode"] = ep
                return [item]

            elif link_type == "vegadrive":
                r = bypass_vegadrive(link["url"], referer=url, max_polls=vd_polls)
                if r.get("results"):
                    for sub in r["results"]:
                        sub["nexdrive_url"] = url
                        if ep:
                            sub["episode"] = ep
                    return r["results"]
                item = {"source_url": link["url"], "text": link["text"], "type": "vegadrive", **r}
                if ep:
                    item["episode"] = ep
                return [item]

            elif link_type == "hubcloud":
                r = bypass_hubcloud(link["url"], referer=url)
                if r.get("results"):
                    for sub in r["results"]:
                        sub["nexdrive_url"] = url
                        if ep:
                            sub["episode"] = ep
                    return r["results"]
                item = {"source_url": link["url"], "text": link["text"], "type": "hubcloud", **r}
                if ep:
                    item["episode"] = ep
                return [item]

            item = {"source_url": link["url"], "text": link["text"], "type": link_type, "success": False}
            if ep:
                item["episode"] = ep
            return [item]

        total_jobs = sum(len(categorized[c]) for c in ["fastdl", "filebee", "vcloud", "vegadrive", "hubcloud"])
        workers = min(28, max(10, total_jobs))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for cat_type in ["fastdl", "filebee", "vcloud", "vegadrive", "hubcloud"]:
                for link in categorized[cat_type]:
                    futures.append(pool.submit(_bypass_one, link, cat_type))
            for f in futures:
                try:
                    results.extend(f.result())
                except Exception as e:
                    results.append({"success": False, "error": str(e)})

        for link in categorized["direct"]:
            resolved = resolve_to_final(link["url"], referer=url)
            ep = ep_map.get(link["url"], "")
            item = {
                "source_url": link["url"], "text": link["text"],
                "type": "direct", "success": resolved.get("success", True),
                "direct_url": resolved["final_url"], "final_url": resolved["final_url"],
                **({k: resolved[k] for k in ("http_status", "error") if resolved.get(k)}),
            }
            if ep:
                item["episode"] = ep
            results.append(item)

        if any(r.get("episode") for r in results):
            results = [
                r for _, r in sorted(
                    enumerate(results),
                    key=lambda pair: (_ep_sort_key(pair[1]), pair[0]),
                )
            ]

        direct_links, warnings = _clean_direct_results(results)
        out = {
            "success": True,
            "source": url,
            "total_direct_links": len(direct_links),
            "total_links": len(results),
            "categories": {k: len(v) for k, v in categorized.items()},
            "direct_links": direct_links,
            "results": results,
        }
        if warnings:
            out["warnings"] = warnings
        return out
    except Exception as e:
        return {"success": False, "error": "nexdrive bypass error: {}".format(str(e))}


# ════════════════════════════════════════════
#  MOVIE / SERIES ENDPOINT — vegamovies full chain (detailed)
# ════════════════════════════════════════════

EXCLUDE_PATTERNS = re.compile(
    r"facebook|twitter|instagram|telegram\.|t\.me|youtube|whatsapp|"
    r"javascript:|mailto:|\.(?:jpg|jpeg|png|gif|svg|webp|ico|css)(?:\?|$)",
    re.IGNORECASE,
)

_QUALITY_RE = re.compile(r"\b(480p|720p|1080p|2160p|4k)\b", re.IGNORECASE)
_SEASON_RE = re.compile(r"\bSeason\s*0*(\d+)\b", re.IGNORECASE)
_AUDIO_TAG_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9\s\-+]{1,35})\}")


def _detect_season_quality_from_html(html_text, nexdrive_links):
    """
    HTML-এ প্রতিটি nexdrive লিংকের অব্যবহিত আগের অংশ থেকে কোয়ালিটি (480P, 720P, 1080P, 2160p (4K)),
    সিজন (Season 1, Season 2 ...) ও অডিও/ল্যাঙ্গুয়েজ ট্যাগ ({Hindi-English} ইত্যাদি) বের করে।
    প্রতিটি nexdrive লিংকের মধ্যবর্তী HTML স্লাইস ব্যবহার করায় nested tag বা cross-section mismatch হয় না।
    রিটার্ন: (quality_map, season_map, meta_map)
    """
    quality_map = {}
    season_map = {}
    meta_map = {}

    nexdrive_positions = []
    for link in nexdrive_links:
        pos = html_text.find(link["url"])
        if pos == -1:
            pos = html_text.find(link["url"].replace("&", "&amp;"))
        nexdrive_positions.append({"url": link["url"], "pos": pos})

    ordered = sorted([n for n in nexdrive_positions if n["pos"] >= 0], key=lambda x: x["pos"])
    prev_end = 0
    last_q = ""
    last_season = ""
    last_audio = ""

    for nx in ordered:
        pos = nx["pos"]
        a_start = html_text.rfind("<a", prev_end, pos)
        end_slice = a_start if a_start >= 0 else pos
        chunk = html_text[max(prev_end, end_slice - 900):end_slice]
        chunk_no_a = re.sub(r"<a\b[^>]*>.*?</a>", " ", chunk, flags=re.S | re.I)
        clean = " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", chunk_no_a)).split())

        q_matches = _QUALITY_RE.findall(clean)
        if q_matches:
            q = q_matches[-1].upper()
            if q == "4K":
                q = "2160p (4K)"
            last_q = q
        else:
            q = last_q

        s_matches = _SEASON_RE.findall(clean)
        season_changed = False
        if s_matches:
            s = "Season {}".format(int(s_matches[-1]))
            if s != last_season:
                season_changed = True
            last_season = s
        else:
            s = last_season

        a_matches = _AUDIO_TAG_RE.findall(clean)
        if a_matches:
            audio = a_matches[-1].strip()
            last_audio = audio
        else:
            if season_changed:
                last_audio = ""
            audio = last_audio

        if q:
            quality_map[nx["url"]] = q
        if s:
            season_map[nx["url"]] = s
        meta_map[nx["url"]] = {
            "quality": q,
            "season": s,
            "audio": audio,
        }

        a_close = html_text.find("</a>", pos)
        prev_end = (a_close + 4) if a_close >= 0 else (pos + len(nx["url"]))

    return quality_map, season_map, meta_map


def _detect_quality_from_html(html_text, nexdrive_links):
    quality_map, _, _ = _detect_season_quality_from_html(html_text, nexdrive_links)
    return quality_map


def extract_movie(url, _prefetched=None):
    """vegamovies পেজ → nexdrive → সব বাইপাস → ডাইরেক্ট URL (detailed output)"""
    try:
        if _prefetched is not None:
            html_text, final_url, all_links = _prefetched
        else:
            html_text, final_url = fetch(url)
            all_links = parse_links(html_text, final_url)
        nexdrive_links = [l for l in all_links if "nexdrive" in l["url"].lower()]

        # ──── কোয়ালিটি ও সিজন ডিটেকশন (HTML position based) ────
        quality_map = {}
        season_map = {}
        if nexdrive_links:
            quality_map, season_map, _ = _detect_season_quality_from_html(html_text, nexdrive_links)

        if not nexdrive_links:
            fastdl_links = [l for l in all_links if "fastdl" in l["url"].lower()]
            filebee_links = [l for l in all_links if "filebee" in l["url"].lower()]
            vcloud_links = [l for l in all_links if "vcloud" in l["url"].lower()]
            vegadrive_links = [l for l in all_links if "vegadrive" in l["url"].lower()]

            results = []
            def _bd(link, ltype):
                if ltype == "fastdl":
                    r = bypass_fastdl(link["url"], referer=url)
                elif ltype == "filebee":
                    r = bypass_filebee(link["url"], referer=url)
                elif ltype == "vegadrive":
                    r = bypass_vegadrive(link["url"], referer=url)
                    if r.get("results"):
                        return r["results"]
                    return [{"source_url": link["url"], "text": link["text"], "type": ltype, **r}]
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
                for l in vegadrive_links:
                    futs.append(pool.submit(_bd, l, "vegadrive"))
                for l in vcloud_links:
                    futs.append(pool.submit(_bd, l, "vcloud"))
                for f in futs:
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
            season = season_map.get(nx_link["url"], "")
            items = []
            if bypass_data.get("success") and bypass_data.get("results"):
                for r in bypass_data["results"]:
                    r["nexdrive_url"] = nx_link["url"]
                    r["nexdrive_text"] = nx_link["text"]
                    r["quality"] = quality
                    if season:
                        r["season"] = season
                    items.append(r)
            else:
                item = {
                    "nexdrive_url": nx_link["url"], "nexdrive_text": nx_link["text"],
                    "quality": quality,
                    "success": False, "error": bypass_data.get("error", "bypass failed"),
                }
                if season:
                    item["season"] = season
                items.append(item)
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
            "is_series": bool(season_map),
            "total_nexdrive_links": len(nexdrive_links),
            "total_direct_links": sum(1 for r in all_results if r.get("success")),
            "results": all_results,
        }
    except Exception as e:
        return {"success": False, "error": "movie extract error: {}".format(str(e))}


# ════════════════════════════════════════════
#  /direct ENDPOINT — ONE API, CLEAN OUTPUT
#  vegamovies / nexdrive URL দিন → শুধু ফাইনাল ডাইরেক্ট URL পান
# ════════════════════════════════════════════

def extract_direct(url, full=False):
    """
    ONE API: vegamovies / nexdrive URL → পুরো চেইন বাইপাস → শুধু ক্লিন ডাইরেক্ট ডাউনলোড URL

    - মুভি বা ছোট সিজন: এক ধাপেই সব ক্লিন ডাইরেক্ট URL দেয়।
    - বড় সিরিজ (>4 প্যাক ও Season হেডিং, বা >8 প্যাক): দ্রুত (~1-2s) Season/Quality packs লিস্ট দেয়
      যাতে Heroku 30s টাইমআউট না খায়; পছন্দের প্যাকটির bypass_url কল করলে সব এপিসোড বের হয়।
      সব প্যাক একসাথে চাইলে ?full=1 ব্যবহার করুন।
    """
    if "nexdrive" in url.lower():
        nx_data = bypass_nexdrive(url)
        if not nx_data.get("success") and not nx_data.get("results"):
            return nx_data
        direct_links = nx_data.get("direct_links")
        warnings = nx_data.get("warnings")
        if direct_links is None:
            direct_links, warnings = _clean_direct_results(nx_data.get("results", []), sort_by_episode=True)
        out = {
            "success": len(direct_links) > 0,
            "source": url,
            "total_direct_links": len(direct_links),
            "direct_links": direct_links,
        }
        if warnings:
            out["warnings"] = warnings
        return out

    try:
        html_text, final_url = fetch(url)
        all_links = parse_links(html_text, final_url)
    except Exception as e:
        return {"success": False, "error": "movie extract error: {}".format(str(e))}

    nexdrive_links = [l for l in all_links if "nexdrive" in l["url"].lower()]
    quality_map, season_map, meta_map = ({}, {}, {})
    if nexdrive_links:
        quality_map, season_map, meta_map = _detect_season_quality_from_html(html_text, nexdrive_links)

    has_seasons = bool(season_map)
    if nexdrive_links and not full and ((has_seasons and len(nexdrive_links) > 4) or len(nexdrive_links) > 8):
        packs = []
        for idx, nx in enumerate(nexdrive_links, 1):
            meta = meta_map.get(nx["url"], {})
            s = meta.get("season", "")
            audio = meta.get("audio", "")
            q = meta.get("quality", "")
            parts = [p for p in [s, audio, q, nx["text"]] if p]
            packs.append({
                "index": idx,
                "season": s,
                "audio": audio,
                "quality": q,
                "label": " | ".join(parts) if parts else nx["text"],
                "text": nx["text"],
                "url": nx["url"],
                "bypass_url": "/bypass?url=" + quote(nx["url"], safe=""),
            })
        return {
            "success": True,
            "is_series": has_seasons or True,
            "source": url,
            "total_packs": len(packs),
            "packs": packs,
            "note": (
                "এটি একটি সিরিজ/মাল্টি-প্যাক পেজ ({}টি প্যাক)। দ্রুত রেজাল্টের জন্য পছন্দের প্যাকটির "
                "bypass_url (বা /bypass?url=<pack_url>) কল করুন — প্রতিটি এপিসোডের ডাইরেক্ট লিংক পাবেন। "
                "সব প্যাক একসাথে বাইপাস করতে &full=1 যোগ করুন।"
            ).format(len(packs)),
        }

    # মুভি বা কমপ্যাক্ট প্যাক বা full=1 → সরাসরি /movie chain চালাও
    movie_data = extract_movie(url, _prefetched=(html_text, final_url, all_links))
    if not movie_data.get("success"):
        return movie_data

    direct_links, warnings = _clean_direct_results(movie_data.get("results", []))
    out = {
        "success": len(direct_links) > 0,
        "source": url,
        "total_direct_links": len(direct_links),
        "direct_links": direct_links,
    }
    if has_seasons:
        out["is_series"] = True
    if warnings:
        out["warnings"] = warnings
    return out


def _detect_server_name(result, url):
    """সার্ভারের নাম ডিটেক্ট"""
    explicit = result.get("server", "")
    if explicit and explicit != "Unknown":
        return explicit

    text = result.get("text", "").lower()
    url_lower = url.lower()
    rtype = result.get("type", "").lower()
    rsource = result.get("source", "").lower()
    via = result.get("resolved_via", "").lower()

    if rtype == "fastdl" or rsource.startswith("fastdl") or "g-direct" in text or "instant" in text or "fastdl" in url_lower:
        return "G-Direct Instant"
    if "vegadrop" in text or "vegadrive" in url_lower or "skydrop" in url_lower or via == "vegadrive_skydrop":
        return "Vegadrop 10Gbps"
    if "fslv2" in text or "r2.cloudflarestorage.com" in url_lower:
        return "FSLv2 Server"
    if "fsl" in text or ".r2.dev/" in url_lower:
        return "FSL Server"
    if "pixel" in text or "pixeldrain" in url_lower or "pixelserver" in text:
        return "PixelServer"
    if "10gbps" in text or "cryptonewz" in url_lower or "gpdl" in url_lower or "10gbps" in via:
        return "Server 10Gbps"
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
    opener = _build_opener()
    for _ in range(MAX_REDIRECTS):
        try:
            req = Request(current, method="HEAD")
            req.add_header("User-Agent", USER_AGENT)
            try:
                resp = opener.open(req, timeout=TIMEOUT)
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

    def _get_query_params(self):
        return parse_qs(urlparse(self.path).query)

    def _get_url_param(self):
        return self._get_query_params().get("url", [None])[0]

    def _get_bool_param(self, *names):
        params = self._get_query_params()
        for name in names:
            val = params.get(name, [None])[0]
            if val is not None:
                return str(val).strip().lower() in ("1", "true", "yes")
        return False

    def _get_post_data(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len else "{}"
        try:
            data = json.loads(body)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _handle(self, url, full=False):
        path = urlparse(self.path).path

        # /direct — ONE API, পুরো চেইন, ক্লিন আউটপুট
        if path == "/direct":
            if not url:
                self._send_json({"success": False, "error": "url parameter required"}, 400)
                return
            self._send_json(extract_direct(url, full=full))

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
                # vegamovies বা অন্য যেকোনো মুভি/সিরিজ পেজ → পুরো চেইন সলভ করে ক্লিন ডাইরেক্ট লিংক/প্যাক লিস্ট দেয়
                self._send_json(extract_direct(url, full=full))

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
                "version": "6.3.0",
                "endpoints": {
                    "/bypass?url=<URL>": "ONE API - vegamovies (movie & series)/nexdrive/vcloud/fastdl/vegadrive/hubcloud bypass",
                    "/direct?url=<URL>": "clean direct download URLs (series packs + ?full=1 support)",
                    "/movie?url=<vegamovies_url>": "full chain bypass (detailed output)",
                    "/api/extract?url=<URL>": "page download links extract",
                    "/api/resolve?url=<URL>": "follow redirects -> final URL",
                },
                "supported_sites": [
                    "vegamovies.*", "nexdrive.beer", "fastdl.beer",
                    "filebee.live", "vcloud.beer", "vegadrive.me", "hubcloud",
                ],
            })
            return
        full = self._get_bool_param("full", "all")
        self._handle(self._get_url_param(), full=full)

    def do_POST(self):
        data = self._get_post_data()
        url = data.get("url") or self._get_url_param()
        full = bool(data.get("full") or data.get("all") or self._get_bool_param("full", "all"))
        self._handle(url, full=full)

    def log_message(self, format, *args):
        print("  [{}] {}".format(self.log_date_time_string(), args[0]))


# ─── Start Server ───
def start_server():
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    server = HTTPServer((HOST, PORT), APIHandler)
    print("=" * 60)
    print("  Link Extractor & Bypass API v6.3")
    print("=" * 60)
    print("  Server: http://localhost:{}".format(PORT))
    print()
    print("  Endpoints:")
    print("    /bypass?url=<URL>            -> ONE API (vegamovies/series/nexdrive/vcloud)")
    print("    /direct?url=<URL>            -> Clean direct URLs only (&full=1)")
    print("    /movie?url=<vegamovies_url>  -> Full chain (detailed)")
    print("    /api/extract?url=<URL>       -> Extract links")
    print("    /api/resolve?url=<URL>       -> Follow redirects")
    print()
    print("  Supported: vegamovies, nexdrive, fastdl, filebee, vcloud, vegadrive, hubcloud")
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
