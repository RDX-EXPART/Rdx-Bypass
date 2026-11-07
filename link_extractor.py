"""
Link Extractor & Bypass API v5.2
=================================
vegamovies / nexdrive.beer / fastdl.beer / filebee / vcloud.beer / hubcloud
সব চেইন বাইপাস করে ডাইরেক্ট ডাউনলোড লিংক বের করে।

Endpoints:
  GET /direct?url=<vegamovies_url>    -> ONE API — পুরো চেইন বাইপাস → শুধু ডাইরেক্ট URL
                                         (&fresh=1 দিলে ১০ মিনিটের ক্যাশ বাদ)
  GET /trace?url=<যেকোনো পেজ URL>      -> ডিবাগ: অ্যাঙ্কর, লুকানো URL, এপিসোড/সিজন/কোয়ালিটি ধরা পড়ছে কিনা
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
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, parse_qs, quote, unquote
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler, HTTPSHandler
import time
import threading
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:  # Python < 3.7
    ThreadingHTTPServer = HTTPServer

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


# ─── HTTP ফেচ ───
# একসাথে সর্বোচ্চ কয়টা রিকোয়েস্ট বাইরে যাবে (আগে ~৮০টা একসাথে যেত → সাইট ব্লক/রেট-লিমিট)
_NET_SEM = threading.BoundedSemaphore(int(os.environ.get("MAX_CONCURRENT_REQUESTS", "8")))


def fetch(url, referer=None, extra_headers=None, retries=3):
    """URL ফেচ করে (HTML, final_url) রিটার্ন করে — থ্রটল + রিট্রাই সহ"""
    last = None
    for attempt in range(max(1, retries)):
        try:
            with _NET_SEM:
                return _fetch_once(url, referer, extra_headers)
        except HTTPError as e:
            last = e
            if e.code in (400, 401, 404, 410):
                break
        except Exception as e:
            last = e
        if attempt < retries - 1:
            time.sleep(0.7 * (attempt + 1) + random.random() * 0.5)
    raise last


def _fetch_once(url, referer=None, extra_headers=None):
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



# ════════════════════════════════════════════
#  QUALITY LABEL v5.2 — "1080P HEVC [9.5GB]" ধরনের ভ্যারিয়েন্ট আলাদা রাখে
# ════════════════════════════════════════════
# পুরনো _detect_quality_from_html লেজি `.*?` + DOTALL ব্যবহার করত; মার্কারের position
# হতো লম্বা ব্লকের শুরুতে, তাই সব লিংক শেষ কোয়ালিটিতে (যেমন 1080p) জড়ো হতো।
# নতুন পদ্ধতি: প্রতিটি nexdrive লিংকের ঠিক আগের heading-লাইন পড়ে, তাতে একটাই
# রেজোলিউশন থাকলে সেটাই (মডিফায়ার + সাইজ সহ) লিংকের কোয়ালিটি।

_Q_TOKEN = re.compile(r"(?<![A-Za-z0-9])(2160p|1440p|1080p|720p|576p|480p|360p|4k)(?![A-Za-z0-9])", re.IGNORECASE)
_Q_MODS = [
    ("HEVC", r"hevc|x\.?265|h\.?265"),
    ("10Bit", r"10[\s\-]?bit"),
    ("HDR", r"hdr(?:10\+?)?"),
    ("DV", r"dolby[\s\-]?vision|(?<![A-Za-z])dv(?![A-Za-z])"),
    ("60FPS", r"60[\s\-]?fps"),
    ("REMUX", r"remux"),
    ("IMAX", r"imax"),
    ("SDR", r"(?<![A-Za-z])sdr(?![A-Za-z])"),
]
_Q_SIZE = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*(GB|MB)\s*(?:/\s*[A-Za-z]+)?\s*\]", re.IGNORECASE)
_BLOCK_TAG = re.compile(r"<(/?)(?:h[1-6]|p|div|br|li|tr|td|center|section|article|hr)(?=[\s>/])[^>]*>", re.IGNORECASE)


def _label_from_line(line):
    """একটা লাইনে ঠিক একটা রেজোলিউশন থাকলে "1080P HEVC [9.5GB]" রিটার্ন করে"""
    toks = {t.lower() for t in _Q_TOKEN.findall(line)}
    if len(toks) != 1:
        return None
    q = toks.pop().upper()
    mods = [name for name, pat in _Q_MODS if re.search(pat, line, re.IGNORECASE)]
    label = " ".join([q] + mods)
    size = _Q_SIZE.search(line)
    if size:
        label += " [{}{}]".format(size.group(1), size.group(2).upper())
    return label


def _plain_lines(chunk):
    chunk = _BLOCK_TAG.sub("\n", chunk)
    chunk = re.sub(r"<[^>]+>", "", chunk)
    try:
        import html as _h
        chunk = _h.unescape(chunk)
    except Exception:
        pass
    return [ln.strip() for ln in chunk.split("\n") if ln.strip()]


def _quality_for_pos(html, pos, link_text=""):
    own = _label_from_line(link_text or "")
    if own:
        return own
    chunk = html[max(0, pos - 4000):pos]
    for ln in reversed(_plain_lines(chunk)):
        lab = _label_from_line(ln)
        if lab:
            return lab
    return ""


def _detect_quality_map(html, links):
    """{url: label} — প্রতিটি লিংকের আগের heading থেকে"""
    out = {}
    for link in links:
        u = link["url"]
        pos = html.find(u)
        if pos == -1:
            pos = html.find(u.replace("&", "&amp;"))
        if pos == -1:
            continue
        lab = _quality_for_pos(html, pos, link.get("text", ""))
        if lab:
            out[u] = lab
    return out


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
                self._cls = d.get("class", "") or ""
                self._id = d.get("id", "") or ""

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
    parser.feed(html)
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
        results.append({"url": absolute, "text": item["text"], "class": item.get("class", ""), "id": item.get("id", "")})
    return results



# ════════════════════════════════════════════
#  HELPERS v5.1 — junk filter + episode detection
# ════════════════════════════════════════════

# শুধু path-segment মিলিয়ে junk ধরা হয়। আগে পুরো URL-এ substring খোঁজা হতো,
# তাই লম্বা টোকেনে "faq"/"about" থাকলে ভালো ডাউনলোড লিংকও বাদ পড়ত।
_JUNK_SEGMENTS = {
    "terms", "contact", "copyright", "privacy", "policy", "login", "signup",
    "register", "about", "faq", "dmca", "tos", "disclaimer",
}


def _is_junk_url(url):
    try:
        parsed = urlparse(url)
    except Exception:
        return True
    segs = [x.lower().rsplit(".", 1)[0] for x in parsed.path.split("/") if x]
    return any(sg in _JUNK_SEGMENTS for sg in segs)


# ─── এপিসোড / সিজন শনাক্ত (v5.2: লাইন-ভিত্তিক, একাধিক ফরম্যাট) ───
# ফরম্যাট: "-:Episode: 5:-", "Episode 5", "EP05", "S01E05", আলাদা লাইনে "E05"
_EP_PATTERNS = [
    re.compile(r"-:\s*Episodes?\s*[:\-]?\s*0*(\d{1,3})\s*:-", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])S\d{1,2}\s*E0*(\d{1,3})(?![0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])Episode\.?\s*[:\-#]?\s*0*(\d{1,3})(?![0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])Ep\.?\s*[:\-#]?\s*0*(\d{1,3})(?![0-9])", re.IGNORECASE),
]
_EP_SHORT = re.compile(r"^\s*E0*(\d{1,3})\s*$", re.IGNORECASE)
_SEASON_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])Season\s*[:\-]?\s*0*(\d{1,2})(?![0-9])", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])S0*(\d{1,2})(?=\s*E\d|[^A-Za-z0-9]|$)", re.IGNORECASE),
]
# "Episode 1-8", "Season 1 to 4" — রেঞ্জ মানে ব্যাচ/টাইটেল, নির্দিষ্ট এপিসোড নয়
_RANGE_AFTER = re.compile(r"^\s*(?:-|–|—|to|&|,|\+)\s*\d", re.IGNORECASE)
_BATCH_RE = re.compile(r"batch|zip|complete|pack|all[\s-]*episodes", re.IGNORECASE)


def _numbers_from_line(line, patterns):
    nums = set()
    for pat in patterns:
        for m in pat.finditer(line):
            if _RANGE_AFTER.match(line[m.end():]):
                continue
            nums.add(int(m.group(1)))
    return nums


def _episode_from_line(line):
    nums = _numbers_from_line(line, _EP_PATTERNS)
    m = _EP_SHORT.match(line)
    if m:
        nums.add(int(m.group(1)))
    return next(iter(nums)) if len(nums) == 1 else None


def _season_from_line(line):
    nums = _numbers_from_line(line, _SEASON_PATTERNS)
    return next(iter(nums)) if len(nums) == 1 else None


def _marker_for_pos(html, pos, link_text, from_line, window):
    """লিংকের নিজের টেক্সট, না হলে ঠিক আগের নিকটতম heading-লাইন"""
    val = from_line(link_text or "")
    if val is not None:
        return val
    for ln in reversed(_plain_lines(html[max(0, pos - window):pos])):
        val = from_line(ln)
        if val is not None:
            return val
    return None


def _detect_marker_map(html, links, from_line, window):
    out = {}
    for link in links:
        u = link["url"]
        pos = html.find(u)
        if pos == -1:
            pos = html.find(u.replace("&", "&amp;"))
        if pos == -1:
            continue
        val = _marker_for_pos(html, pos, link.get("text", ""), from_line, window)
        if val is not None:
            out[u] = val
    return out


def _detect_episode_map(html, links):
    """{url: episode}"""
    return _detect_marker_map(html, links, _episode_from_line, 3000)


def _detect_season_map(html, links):
    """{url: season} — সিজন heading অনেক আগে থাকতে পারে, তাই বড় উইন্ডো"""
    return _detect_marker_map(html, links, _season_from_line, 12000)


def _page_title(html):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    t = re.sub(r"<[^>]+>", " ", m.group(1))
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*[-|–]\s*(?:VegaMovies|Vegamovies)[^-|]*$", "", t, flags=re.IGNORECASE)
    return t[:200]


# ════════════════════════════════════════════
#  DEEP URL RESOLVER — ইন্টারমিডিয়েট লিংক ফাইনাল করে
# ════════════════════════════════════════════

def resolve_to_final(url, referer=None):
    """
    যেকোনো ইন্টারমিডিয়েট URL থেকে ফাইনাল ডাইরেক্ট ডাউনলোড URL বের করে।
    dl.php?link=... → link parameter বের করে
    vegadrive.me/d/... → ফলো করে ফাইনাল URL
    pixeldrain.dev/u/... → API URL বানায়
    cryptonewz.online/dl.php → link parameter extract
    """
    url_lower = url.lower()

    # dl.php?link=... থেকে link extract (fastdl, cryptonewz etc.)
    if "dl.php?" in url_lower and "link=" in url_lower:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        link_val = qs.get("link", [None])[0]
        if link_val:
            return {
                "final_url": link_val,
                "proxy_url": url,
                "resolved_via": "dl.php_param",
            }

    # pixeldrain /api/file/<id> আগে থেকেই ডাইরেক্ট
    if "pixeldrain" in url_lower and "/api/file/" in url_lower:
        return {"final_url": url, "resolved_via": "pixeldrain_direct"}

    # pixeldrain → direct download
    if "pixeldrain" in url_lower and "/u/" in url:
        file_id = url.rstrip("/").split("/u/")[-1]
        return {
            "final_url": "https://pixeldrain.com/api/file/{}".format(file_id),
            "page_url": url,
            "resolved_via": "pixeldrain_api",
        }

    # gofile → keep as-is (needs API key for direct)
    if "gofile.io" in url_lower:
        return {"final_url": url, "resolved_via": "gofile_direct"}

    # R2 / CDN direct links — already direct
    if any(x in url_lower for x in [".r2.dev/", "workers.dev/", "mediafire.com/", "mega.nz/"]):
        return {"final_url": url, "resolved_via": "cdn_direct"}

    # Google Drive
    if "drive.google.com" in url_lower or "googleusercontent.com" in url_lower:
        return {"final_url": url, "resolved_via": "google_direct"}

    # vegadrive.me / vegadrive.app — ফলো করে ফাইনাল URL বের করা
    if "vegadrive" in url_lower:
        try:
            html, final = fetch(url, referer=referer)
            # Check for redirect or download link in the page
            redir = re.search(r'window\.location(?:\.href)?\s*=\s*["\'](https?://[^\s"\'<>]+)["\']', html)
            if redir:
                return resolve_to_final(redir.group(1), referer=url)
            # Look for actual download links (not junk pages)
            links = parse_links(html, final)
            junk = ["terms", "contact", "copyright", "privacy", "policy", "login", "about", "dmca"]
            for link in links:
                ll = link["url"].lower()
                if any(j in ll for j in junk):
                    continue
                if any(x in ll for x in ["download", "dl.php", ".r2.dev", "pixeldrain.com/api",
                                          "pixeldrain.dev/u", "gofile.io/d/", "drive.google.com",
                                          "workers.dev", "mediafire.com/file"]):
                    return resolve_to_final(link["url"], referer=url)
            # Check for Google Drive / CDN URLs in page source
            gdrive = re.findall(
                r'https?://[^\s"\'<>]*(?:video-downloads\.googleusercontent\.com|drive\.google\.com|\.r2\.dev)[^\s"\'<>]*',
                html,
            )
            if gdrive:
                return {"final_url": gdrive[0], "resolved_via": "vegadrive_gdrive"}
            # Follow HEAD redirect
            head_final = fetch_head(url, referer=referer)
            if head_final != url:
                return resolve_to_final(head_final, referer=url)
            return {"final_url": url, "resolved_via": "vegadrive_page"}
        except Exception:
            return {"final_url": url, "resolved_via": "vegadrive_fallback"}

    # Telegram
    if "t.me/" in url_lower:
        return {"final_url": url, "resolved_via": "telegram"}

    # Default: HEAD follow
    try:
        head_final = fetch_head(url, referer=referer)
        return {"final_url": head_final, "resolved_via": "head_redirect"}
    except Exception:
        return {"final_url": url, "resolved_via": "fallback"}



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
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path or path == "":
        return False  # bare domain, not a download link

    text_kw = ["server", "fsl", "pixel", "10gbps", "download", "direct",
               "gdrive", "gofile", "filepress"]
    url_kw = ["pixeldrain.com/api/", "pixeldrain.dev/u/", "gofile.io/d/",
              "filepress", "mediafire.com/file/",
              "drive.google.com/", "workers.dev/", "dl.php", "fastdl",
              "filebee", ".r2.dev/", "vegadrive.me/d/", "vegadrive.me/s/",
              "vegadrive.app/s/", "vegadrive.co/", "fsl"]
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
                    for link in parse_links(inner_html, inner_final):
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
                        for link in parse_links(inner_html, inner_final):
                            if _is_server_link(link["text"], link["url"]):
                                server_links.append(link)
                except Exception:
                    pass

        # Method 3: Direct page links (v5.1: সবসময় চলে — আগে Method 1 কিছু পেলেই
        # এটা বাদ যেত, ফলে মূল পেজের FSL / PixelServer বাটন হারিয়ে যেত)
        for link in _apply_js_overrides(parse_links(html, final_url), html):
            if _is_server_link(link["text"], link["url"]) and link["url"] != url:
                server_links.append(link)

        # Method 4: JS redirects
        for js_url in re.findall(r'(?:window\.location(?:\.href)?\s*=|\.href\s*=)\s*["\'](https?://[^\s"\'<>]+)["\']', html):
            jl = js_url.lower()
            if any(d in jl for d in ["pixeldrain", "gofile", "filepress", "drive.google", "workers.dev", "dl.php", "fastdl", ".r2.dev"]):
                if not any(s["url"] == js_url for s in server_links):
                    server_links.append({"url": js_url, "text": "JS Redirect", "class": ""})

        # ডুপ্লিকেট সরানো (Method 1 + 3 একই লিংক দিতে পারে)
        _seen_sl, _uniq = set(), []
        for _l in server_links:
            if _l["url"] in _seen_sl or _l["url"] == url or _l["url"] == final_url:
                continue
            _seen_sl.add(_l["url"])
            _uniq.append(_l)
        server_links = _uniq

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
                elif "filebee" in link_lower:
                    r = bypass_filebee(link_url, referer=url)
                    result.update(r)
                    if r.get("success") and r.get("direct_url"):
                        resolved = resolve_to_final(r["direct_url"], referer=link_url)
                        result["final_url"] = resolved["final_url"]
                elif "pixeldrain" in link_lower and "/u/" in link_lower:
                    direct = _pixel_direct(link_url)
                    result["success"] = True
                    result["direct_url"] = link_url
                    result["final_url"] = direct
                else:
                    resolved = resolve_to_final(link_url, referer=url)
                    result["success"] = True
                    result["direct_url"] = link_url
                    result["final_url"] = resolved["final_url"]
                    result["resolved_via"] = resolved.get("resolved_via", "")
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

        # v5.2: ক্রলার দিয়ে FSL / Pixel / 10Gbps বাটন (নতুন ভি-ক্লাউড ফরম্যাট)
        results = _merge_hub(results, url, referer=referer)

        if not any(r.get("success") and not r.get("is_page") for r in results):
            ext = external_bypass(url)
            if ext:
                results.append({"source_url": url, "text": "External Bypass", "type": "vcloud_external",
                                "success": True, "direct_url": ext, "final_url": ext})

        if not results:
            return {"success": False, "source": url, "type": "vcloud",
                    "error": "vcloud: no server buttons found (page {} bytes, title: {})".format(
                        len(html), _page_title(html)[:80] or "-")}

        return {"success": True, "source": url, "type": "vcloud", "total_links": len(results), "results": results}
    except Exception as e:
        return {"success": False, "error": "vcloud bypass error: {}".format(str(e))}



# ════════════════════════════════════════════
#  HUB CRAWLER v5.2 — vegadrive / vcloud / hubcloud পেজ থেকে সার্ভার বাটন বের করে
#  (FSL Server · PixelServer · Server 10Gbps · GoFile · Download File ...)
#
#  নেক্সটড্রাইভের "V-Cloud" বাটন এখন one.vegadrive.me/s/<id> এ যায়। এই পেজ থেকে
#  ফাইনাল বাটন পর্যন্ত কয়েক ধাপ হপ লাগে, ধাপগুলোর ফরম্যাট বদলায়। তাই একটা নির্দিষ্ট
#  রেসিপির বদলে সীমিত-গভীরতার ক্রলার: প্রতিটি পেজে
#    ১) JS-এ লুকানো পরের URL (var url = '...', location = '...', atob(...))
#    ২) id="download" / "Generate / Download / Continue" ধরনের ধাপ-লিংক
#    ৩) FSL / Pixel / 10Gbps / Buzz / S3 / Download File ধরনের সার্ভার বাটন
#  খোঁজে। সার্ভার বাটন পেলে সেখানেই থামে, ধাপ-লিংক পেলে আরেক হপ যায়।
# ════════════════════════════════════════════

_HUB_FAMILY = ("vegadrive", "vcloud", "hubcloud", "hubdrive", "gamerxyt", "hubcdn")
_HUB_SERVER_TEXT = re.compile(r"fsl|pixel|10\s*-?\s*gbps|buzz|s3\s*server|download\s*file|gofile|viking|transferit|direct\s*download", re.IGNORECASE)
_HUB_STEP_TEXT = re.compile(r"generate|download|continue|get\s*link|proceed|verify|click\s*here|go\s*to|next", re.IGNORECASE)
_HUB_FINAL_HOST = re.compile(r"pixeldrain|gofile\.io|googleusercontent|\.r2\.dev|workers\.dev|mediafire|mega\.nz|drive\.google", re.IGNORECASE)
_HUB_SCRIPT_PATTERNS = [
    r"""var\s+url\s*=\s*['"]([^'"]+)['"]""",
    r"""(?:window\.location(?:\.href)?|location\.href|document\.location)\s*=\s*['"]([^'"]+)['"]""",
    r"""location\.replace\(\s*['"]([^'"]+)['"]""",
    r"""window\.open\(\s*['"]([^'"]+)['"]""",
]


def _is_hub_host(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(k in host for k in _HUB_FAMILY)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_location(url, referer=None):
    """একটাই রিকোয়েস্ট, রিডাইরেক্ট ফলো না করে Location (বা hx-redirect) রিটার্ন করে"""
    opener = build_opener(_NoRedirect, HTTPSHandler(context=_ssl_ctx()))
    req = Request(url)
    req.add_header("User-Agent", USER_AGENT)
    if referer:
        req.add_header("Referer", referer)
    try:
        resp = opener.open(req, timeout=TIMEOUT)
        loc = resp.headers.get("Location") or resp.headers.get("hx-redirect")
        resp.close()
        return loc
    except HTTPError as e:
        return e.headers.get("Location") or e.headers.get("hx-redirect")
    except Exception:
        return None


def _follow_link_param(url, referer=None, max_hops=8):
    """10Gbps বাটন: রিডাইরেক্ট চেইন ধরে ধরে `link=` প্যারামিটার (বা শেষ URL) বের করে"""
    cur = url
    for _ in range(max_hops):
        qs = parse_qs(urlparse(cur).query)
        if qs.get("link"):
            return unquote(qs["link"][0])
        loc = fetch_location(cur, referer=referer)
        if not loc:
            break
        cur = urljoin(cur, loc)
    return cur


def _leave_hub(url, referer=None, max_hops=6):
    """one.vegadrive.me/d/<id> → (redirects) → gofile.io/... : হাব-হোস্ট থেকে বের হওয়া পর্যন্ত Location ফলো"""
    cur = url
    for _ in range(max_hops):
        if not _is_hub_host(cur):
            return cur
        loc = fetch_location(cur, referer=referer)
        if not loc:
            break
        cur = urljoin(cur, loc)
    return cur


def _pixel_direct(url):
    """https://pixeldrain.(com|dev|net)/u/<id> → .../api/file/<id>?download (হোস্ট একই থাকে)"""
    m = re.match(r"(https?://[^/]*pixeldrain[^/]*)/u/([A-Za-z0-9_-]+)", url or "", re.IGNORECASE)
    if m:
        return "{}/api/file/{}?download".format(m.group(1), m.group(2))
    return url


def _js_href_overrides(html):
    """vcloud পেজের ছোট জাভাস্ক্রিপ্ট যা বাটনের href বদলায়; ব্রাউজার যা করে আমরাও তাই করি:
         • var pxl = "https://pixeldrain.dev/u/REAL"; getElementById("pxl-1").href = pxl;   (HTML-এ থাকা href ভুয়া)
         • fsl.href += '1' + new Date().getMinutes();                                        (টোকেনের শেষে মিনিট জোড়া)
       রিটার্ন: {element_id: নতুন_href_বা_সাফিক্স-টুপল}"""
    out = {}
    variables = dict(re.findall(r"""(?:var|let|const)\s+(\w+)\s*=\s*["'](https?://[^"']+)["']""", html))
    for eid, var in re.findall(r"""getElementById\(\s*["']([\w-]+)["']\s*\)\.href\s*=\s*(\w+)""", html):
        if var in variables:
            out[eid] = ("set", variables[var])
    # const fsl = document.getElementById('fsl'); ... fsl.href += '1' + new Date().getMinutes();
    for var, suffix in re.findall(r"""(\w+)\.href\s*\+=\s*["']([^"']*)["']\s*\+\s*new\s+Date\(\)\.getMinutes\(\)""", html):
        m = re.search(r"""(?:const|let|var)\s+{}\s*=\s*document\.getElementById\(\s*["']([\w-]+)["']""".format(re.escape(var)), html)
        out[m.group(1) if m else var] = ("minute", suffix)
    return out


def _apply_js_overrides(links, html):
    ov = _js_href_overrides(html)
    if not ov:
        return links
    fixed = []
    for l in links:
        rule = ov.get(l.get("id", ""))
        if rule:
            l = dict(l)
            if rule[0] == "set":
                l["url"] = rule[1]
            else:  # minute
                l["url"] = l["url"] + rule[1] + str(time.gmtime().tm_min)
        fixed.append(l)
    return fixed


def _hub_candidates(html, base):
    """পেজ থেকে [(kind, url, text)] — kind: 'step' (আরেক হপ) | 'server' (সার্ভার বাটন)"""
    found = []
    for pat in _HUB_SCRIPT_PATTERNS:
        for m in re.finditer(pat, html):
            u = m.group(1).strip()
            if u.startswith(("http://", "https://", "/")):
                found.append(("step", urljoin(base, u), "script"))
    for pat in (r"atob\s*\(\s*atob\s*\(\s*['\"]([A-Za-z0-9+/=]+)['\"]\s*\)",
                r"atob\s*\(\s*['\"]([A-Za-z0-9+/=]{20,})['\"]\s*\)"):
        for m in re.finditer(pat, html):
            dec = _decode_double_base64(m.group(1))
            if dec and dec.startswith("http"):
                found.append(("step", dec, "atob"))
    for link in _apply_js_overrides(parse_links(html, base), html):
        u, text = link["url"], link["text"]
        if EXCLUDE_PATTERNS.search(u) or _is_junk_url(u):
            continue
        parsed = urlparse(u)
        if not parsed.path.rstrip("/") and not parsed.query:
            continue
        if re.search(r"telegram", text, re.IGNORECASE) or "t.me/" in u:
            continue
        if _HUB_SERVER_TEXT.search(text) and not re.fullmatch(r"\s*(?:download|next|continue)\s*", text, re.IGNORECASE):
            found.append(("server", u, text))
        elif _HUB_FINAL_HOST.search(u):
            found.append(("server", u, text or "Direct"))
        elif link.get("id") == "download" or _HUB_STEP_TEXT.search(text):
            found.append(("step", u, text))
    return found


def hub_crawl(start_url, referer=None, max_pages=6, max_depth=3, deadline=18):
    """সীমিত গভীরতায় ক্রল করে [{'url','text'}] সার্ভার বাটন রিটার্ন করে"""
    t0 = time.time()
    seen = {start_url}
    queue = [(start_url, referer, 0)]
    servers, pages = [], 0
    while queue and pages < max_pages and time.time() - t0 < deadline:
        cur, ref, depth = queue.pop(0)
        try:
            html, final = fetch(cur, referer=ref)
        except Exception:
            continue
        pages += 1
        for kind, url, text in _hub_candidates(html, final):
            if url in seen:
                continue
            seen.add(url)
            if kind == "server":
                servers.append({"url": url, "text": text})
            elif depth < max_depth and not _HUB_FINAL_HOST.search(url):
                queue.append((url, final, depth + 1))
    return servers


def hub_servers(start_url, referer=None):
    """hub_crawl + প্রতিটি বাটনকে ফাইনাল ডাইরেক্ট URL-এ রিজলভ → results-ফরম্যাট লিস্ট"""
    out, seen = [], set()
    try:
        crawled = hub_crawl(start_url, referer=referer)
    except Exception:
        return out
    for srv in crawled:
        url, text = srv["url"], srv["text"]
        tl = text.lower()
        final = url
        try:
            if re.search(r"10\s*-?\s*gbps", tl):
                final = _follow_link_param(url, referer=start_url)
            elif "pixel" in tl or "pixeldrain" in url.lower():
                final = _pixel_direct(url)
            elif re.search(r"gofile|viking|transferit", tl):
                final = _leave_hub(url, referer=start_url)
            elif "buzz" in tl:
                loc = fetch_location(url.rstrip("/") + "/download", referer=url)
                final = urljoin(url, loc) if loc else url
        except Exception:
            final = url
        if final in seen:
            continue
        seen.add(final)
        out.append({
            "source_url": url, "text": text, "type": "hub_server", "success": True,
            "direct_url": url, "final_url": final,
        })
    return out


def external_bypass(url):
    """OPTIONAL last resort: another bypass API (env EXTERNAL_BYPASS_URL [+ EXTERNAL_BYPASS_TOKEN]).
    Empty by default — your links are sent to that service only if you set it yourself.
    Expects {"direct_link": "..."} like GET <base>/bypass?url=<url>."""
    base = os.environ.get("EXTERNAL_BYPASS_URL", "").strip().rstrip("/")
    if not base:
        return None
    try:
        headers = {"User-Agent": USER_AGENT}
        token = os.environ.get("EXTERNAL_BYPASS_TOKEN", "").strip()
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request("{}/bypass?url={}".format(base, quote(url, safe="")), headers=headers)
        with urlopen(req, timeout=25, context=_ssl_ctx()) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        link = data.get("direct_link") if isinstance(data, dict) else None
        return link if isinstance(link, str) and link.startswith("http") else None
    except Exception:
        return None


def _merge_hub(results, start_url, referer=None):
    """results-এ হাব-সার্ভারগুলো মিশিয়ে দেয়; অমীমাংসিত হাব-পেজ is_page চিহ্নিত করে"""
    known = {r.get("final_url") for r in results}
    for c in hub_servers(start_url, referer=referer):
        if c["final_url"] not in known:
            results.append(c)
            known.add(c["final_url"])
    for r in results:
        if _is_hub_host(r.get("final_url") or ""):
            r["is_page"] = True
    return results


# ════════════════════════════════════════════
#  HUBCLOUD BYPASS
# ════════════════════════════════════════════

def bypass_hubcloud(url, referer=None):
    """hubcloud / hubdrive / vegadrive পেজ বাইপাস"""
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
                for link in parse_links(inner_html, inner_final):
                    if _is_server_link(link["text"], link["url"]):
                        resolved = resolve_to_final(link["url"], referer=decoded_url)
                        results.append({
                            "source_url": link["url"], "text": link["text"],
                            "type": "hubcloud_server", "success": True,
                            "direct_url": link["url"], "final_url": resolved["final_url"],
                        })
            except Exception:
                pass

        # Direct page links
        if not results:
            for link in parse_links(html, final_url):
                if _is_server_link(link["text"], link["url"]):
                    resolved = resolve_to_final(link["url"], referer=url)
                    results.append({
                        "source_url": link["url"], "text": link["text"],
                        "type": "hubcloud_link", "success": True,
                        "direct_url": link["url"], "final_url": resolved["final_url"],
                    })

        # JS redirects
        for js_url in re.findall(r'window\.location(?:\.href)?\s*=\s*["\'](https?://[^\s"\'<>]+)["\']', html):
            if not any(r["source_url"] == js_url for r in results):
                resolved = resolve_to_final(js_url, referer=url)
                results.append({
                    "source_url": js_url, "text": "JS Redirect",
                    "type": "hubcloud_redirect", "success": True,
                    "direct_url": js_url, "final_url": resolved["final_url"],
                })

        # v5.2: ক্রলার দিয়ে FSL / Pixel / 10Gbps বাটন বের করা
        results = _merge_hub(results, url, referer=referer)
        return {
            "success": bool(results), "source": url, "type": "hubcloud",
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
        categorized = {"fastdl": [], "filebee": [], "vcloud": [], "hubcloud": [], "direct": []}

        for link in all_links:
            url_lower = link["url"].lower()
            text_lower = link["text"].lower()
            if "fastdl" in url_lower:
                categorized["fastdl"].append(link)
            elif "filebee" in url_lower:
                categorized["filebee"].append(link)
            elif "vcloud" in url_lower:
                categorized["vcloud"].append(link)
            elif any(x in url_lower for x in ["hubcloud", "hubdrive"]):
                categorized["hubcloud"].append(link)
            elif any(x in url_lower for x in ["vegadrive.me", "vegadrive.app", "vegadrive.co"]):
                categorized["hubcloud"].append(link)
            elif any(x in url_lower for x in ["drive.google", "workers.dev", "pixeldrain", "mediafire", "gofile"]):
                categorized["direct"].append(link)
            elif any(kw in text_lower for kw in ["v-cloud", "v cloud", "v-drive", "v drive", "multi", "vcloud"]):
                if any(x in url_lower for x in ["vcloud"]) or any(x in text_lower for x in ["v-cloud", "v cloud", "vcloud"]):
                    categorized["vcloud"].append(link)
                else:
                    categorized["hubcloud"].append(link)

        results = []

        # v5.1: প্রতিটি লিংক কোন এপিসোডের — পেজের "-:Episode N:-" মার্কার থেকে
        episode_map = _detect_episode_map(html, all_links)
        season_map = _detect_season_map(html, all_links)

        # v5.3: পেজে "Episode N" মার্কার না পেলেও একই বাটন (যেমন 7টা "G-Direct [Instant]")
        # বারবার থাকলে সেটা এপিসোড-তালিকা; পেজের ক্রম অনুযায়ী 1,2,3… ধরা হয়
        guessed = set()
        _cat_urls = {l["url"] for v in categorized.values() for l in v}
        _groups = {}
        for l in all_links:
            if l["url"] in _cat_urls:
                key = re.sub(r"[^a-z]+", " ", l["text"].lower()).strip()
                _groups.setdefault(key, []).append(l)
        for key, items in _groups.items():
            uniq = list({i["url"]: i for i in items}.values())
            if (len(uniq) > 1 and not _BATCH_RE.search(key)
                    and not any(i["url"] in episode_map for i in uniq)):
                for n, i in enumerate(uniq, 1):
                    episode_map[i["url"]] = n
                    guessed.add(i["url"])

        def _tag(items, link):
            ep = episode_map.get(link["url"])
            se = season_map.get(link["url"])
            for it in items:
                if ep is not None:
                    it["episode"] = ep
                    if link["url"] in guessed:
                        it["episode_guess"] = True
                if se is not None:
                    it["season"] = se
                it["nexdrive_link_text"] = link["text"]
            return items

        def _bypass_one(link, link_type):
            return _tag(_bypass_one_raw(link, link_type), link)

        def _bypass_one_raw(link, link_type):
            if link_type == "fastdl":
                r = bypass_fastdl(link["url"], referer=url)
                res = {"source_url": link["url"], "text": link["text"], "type": "fastdl"}
                res.update(r)
                if r.get("success") and r.get("direct_url"):
                    resolved = resolve_to_final(r["direct_url"], referer=link["url"])
                    res["final_url"] = resolved["final_url"]
                    if resolved.get("proxy_url"):
                        res["proxy_url"] = resolved["proxy_url"]
                return [res]

            elif link_type == "filebee":
                r = bypass_filebee(link["url"], referer=url)
                res = {"source_url": link["url"], "text": link["text"], "type": "filebee"}
                res.update(r)
                if r.get("success") and r.get("direct_url"):
                    resolved = resolve_to_final(r["direct_url"], referer=link["url"])
                    res["final_url"] = resolved["final_url"]
                return [res]

            elif link_type == "vcloud":
                r = bypass_vcloud(link["url"], referer=url)
                if r.get("success") and r.get("results"):
                    for sub in r["results"]:
                        sub["nexdrive_url"] = url
                    return r["results"]
                return [{"source_url": link["url"], "text": link["text"], "type": "vcloud", **r}]

            elif link_type == "hubcloud":
                r = bypass_hubcloud(link["url"], referer=url)
                if r.get("success") and r.get("results"):
                    for sub in r["results"]:
                        sub["nexdrive_url"] = url
                    return r["results"]
                return [{"source_url": link["url"], "text": link["text"], "type": "hubcloud", **r}]

            return [{"source_url": link["url"], "text": link["text"], "type": link_type, "success": False}]

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for cat_type in ["fastdl", "filebee", "vcloud", "hubcloud"]:
                for link in categorized[cat_type]:
                    futures.append(pool.submit(_bypass_one, link, cat_type))
            for f in futures:  # v5.3: জমা দেওয়ার ক্রমে (as_completed হলে এপিসোড-ক্রম এলোমেলো হতো)
                try:
                    results.extend(f.result())
                except Exception as e:
                    results.append({"success": False, "error": str(e)})

        for link in categorized["direct"]:
            resolved = resolve_to_final(link["url"], referer=url)
            results.append(_tag([{
                "source_url": link["url"], "text": link["text"],
                "type": "direct", "success": True,
                "direct_url": link["url"], "final_url": resolved["final_url"],
            }], link)[0])

        return {
            "success": True, "source": url, "total_links": len(results),
            "categories": {k: len(v) for k, v in categorized.items()},
            "episodes_found": sorted(set(episode_map.values())),
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

# কোয়ালিটি সেকশন প্যাটার্ন (vegamovies পেজে h3/h5/strong ট্যাগে থাকে)
_QUALITY_SECTION_RE = re.compile(
    r'<(?:h[1-6]|strong|b|p)[^>]*>.*?'
    r'(480p|720p|1080p|2160p|4[kK])'
    r'.*?</(?:h[1-6]|strong|b|p)>',
    re.IGNORECASE | re.DOTALL,
)

_QUALITY_SIMPLE_RE = re.compile(
    r'(?:download|quality|print)[^<]{0,60}(480p|720p|1080p|2160p|4[kK])',
    re.IGNORECASE,
)


def _detect_quality_from_html(html, nexdrive_links):
    """
    HTML-এ nexdrive লিংকগুলোর পজিশনের আগে কোন কোয়ালিটি মার্কার আছে তা বের করে।
    vegamovies পেজে কোয়ালিটি heading-এ থাকে: 480p, 720p, 1080p, 2160p, 4K
    প্রতিটি heading-এর পরে ঐ কোয়ালিটির nexdrive লিংক থাকে।
    """
    quality_map = {}

    # প্রতিটি nexdrive URL-এর HTML position বের করা
    nexdrive_positions = []
    for link in nexdrive_links:
        # HTML-এ href="nexdrive_url" খুঁজে position বের করা
        pos = html.find(link["url"])
        if pos == -1:
            # URL encode ভার্সনেও ট্রাই
            pos = html.find(link["url"].replace("&", "&amp;"))
        nexdrive_positions.append({"url": link["url"], "pos": pos})

    # সব কোয়ালিটি মার্কারের position বের করা (শুধু ডাউনলোড সেকশনের)
    quality_markers = []

    # Method 1: heading/strong ট্যাগে কোয়ালিটি
    for m in _QUALITY_SECTION_RE.finditer(html):
        q = m.group(1).upper()
        if q == "4K":
            q = "2160p (4K)"
        quality_markers.append({"quality": q, "pos": m.start()})

    # Method 2: "Download" টেক্সটের কাছে কোয়ালিটি
    for m in _QUALITY_SIMPLE_RE.finditer(html):
        q = m.group(1).upper()
        if q == "4K":
            q = "2160p (4K)"
        # ডুপ্লিকেট এড়ানো (±200 chars)
        if not any(abs(m.start() - qm["pos"]) < 200 for qm in quality_markers):
            quality_markers.append({"quality": q, "pos": m.start()})

    # Position অনুযায়ী sort
    quality_markers.sort(key=lambda x: x["pos"])

    # ডাউনলোড সেকশনের কোয়ালিটি ফিল্টার:
    # শুধু সেই markers রাখো যেগুলো nexdrive লিংকের আগে (তবে খুব বেশি দূরে না)
    if nexdrive_positions and nexdrive_positions[0]["pos"] > 0:
        first_nexdrive_pos = nexdrive_positions[0]["pos"]
        # শুধু download section markers রাখো (nexdrive-এর 5000 chars আগে থেকে)
        quality_markers = [qm for qm in quality_markers if qm["pos"] > first_nexdrive_pos - 5000]

    # প্রতিটি nexdrive লিংকের জন্য সবচেয়ে কাছের আগের কোয়ালিটি মার্কার বের করা
    for nx in nexdrive_positions:
        if nx["pos"] < 0:
            continue
        best_quality = ""
        for qm in quality_markers:
            if qm["pos"] < nx["pos"]:
                best_quality = qm["quality"]
            else:
                break
        if best_quality:
            quality_map[nx["url"]] = best_quality

    return quality_map


def extract_movie(url):
    """vegamovies পেজ → nexdrive → সব বাইপাস → ডাইরেক্ট URL (detailed output)"""
    try:
        html, final_url = fetch(url)
        all_links = parse_links(html, final_url)
        nexdrive_links = [l for l in all_links if "nexdrive" in l["url"].lower()]
        page_title = _page_title(html)
        # vegamovies পেজেই যদি প্রতি এপিসোডের আলাদা nexdrive লিংক থাকে
        parent_episode_map = _detect_episode_map(html, nexdrive_links) if nexdrive_links else {}
        # মাল্টি-সিজন পেজে সিজন আসে vegamovies পেজের heading থেকে, এপিসোড আসে nexdrive পেজ থেকে
        parent_season_map = _detect_season_map(html, nexdrive_links) if nexdrive_links else {}

        # ──── কোয়ালিটি ডিটেকশন (HTML position based) ────
        quality_map = {}  # nexdrive_url -> quality
        if nexdrive_links:
            # v5.2: heading-লাইন ভিত্তিক লেবেল (HEVC/10Bit/সাইজ সহ); না পেলে পুরনো পদ্ধতি
            quality_map = _detect_quality_map(html, nexdrive_links)
            _old_map = _detect_quality_from_html(html, nexdrive_links)
            for _u, _q in _old_map.items():
                quality_map.setdefault(_u, _q)

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

            return {"success": True, "source": url, "title": page_title,
                    "total_links": len(results), "results": results}

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
                    # নিজের পেজে এপিসোড না পেলে vegamovies পেজের মার্কার থেকে নাও
                    if "episode" not in r and nx_link["url"] in parent_episode_map:
                        r["episode"] = parent_episode_map[nx_link["url"]]
                    if "season" not in r and nx_link["url"] in parent_season_map:
                        r["season"] = parent_season_map[nx_link["url"]]
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
            "success": True, "source": url, "title": page_title,
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

_DIRECT_CACHE = {}
_DIRECT_LOCKS = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = int(os.environ.get("DIRECT_CACHE_TTL", 600))


def extract_direct(url, fresh=False):
    """
    ক্যাশ + একই URL-এর দ্বৈত কাজ ঠেকানো। Heroku-র ৩০ সেকেন্ডের router timeout-এ প্রথম
    রিকোয়েস্ট কেটে গেলেও কাজ শেষ হয়ে ক্যাশে থাকে; বটের রিট্রাই তখন সঙ্গে সঙ্গে উত্তর পায়।
    `&fresh=1` দিলে ক্যাশ বাদ।
    """
    now = time.time()
    with _CACHE_LOCK:
        hit = _DIRECT_CACHE.get(url)
        if hit and not fresh and now - hit[0] < _CACHE_TTL:
            return hit[1]
        lock = _DIRECT_LOCKS.setdefault(url, threading.Lock())
    with lock:
        hit = _DIRECT_CACHE.get(url)
        if hit and not fresh and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]
        data = _extract_direct_uncached(url)
        if data.get("success") and data.get("direct_links"):
            with _CACHE_LOCK:
                _DIRECT_CACHE[url] = (time.time(), data)
        return data


def trace_page(url):
    """ডিবাগ: একটা পেজ ফেচ করে অ্যাঙ্কর, স্ক্রিপ্টের লুকানো URL আর ক্রলার-ক্যান্ডিডেট দেখায়"""
    try:
        html, final = fetch(url)
    except Exception as e:
        return {"success": False, "url": url, "error": str(e)}
    links = parse_links(html, final)
    info = {
        "success": True, "url": url, "final_url": final, "length": len(html),
        "title": _page_title(html),
        "anchors": [{"text": l["text"], "url": l["url"], "class": l.get("class", ""), "id": l.get("id", "")} for l in links[:80]],
        "candidates": [{"kind": k, "url": u, "text": t} for k, u, t in _hub_candidates(html, final)[:40]],
        "scripts": [sc.strip()[:400] for sc in re.findall(r"<script[^>]*>(.*?)</script>", html, re.IGNORECASE | re.DOTALL) if sc.strip()][:6],
    }
    # v5.2: সিরিজ ডিবাগ — প্রতিটি ডাউনলোড-সম্ভাব্য লিংকে কোন এপিসোড/সিজন/কোয়ালিটি ধরা হচ্ছে
    marker_rows = []
    for l in links:
        if not re.search(r"nexdrive|vcloud|fastdl|vegadrive|hubcloud|filebee|gofile|pixeldrain", l["url"], re.IGNORECASE):
            continue
        pos = html.find(l["url"])
        if pos < 0:
            continue
        marker_rows.append({
            "url": l["url"][:140], "text": l["text"][:60],
            "episode": _marker_for_pos(html, pos, l["text"], _episode_from_line, 3000),
            "season": _marker_for_pos(html, pos, l["text"], _season_from_line, 12000),
            "quality": _quality_for_pos(html, pos, l["text"]),
        })
        if len(marker_rows) >= 60:
            break
    if marker_rows:
        info["link_markers"] = marker_rows
    nex = [l for l in links if "nexdrive" in l["url"].lower()]
    if nex:
        rows = []
        for l in nex:
            pos = html.find(l["url"])
            rows.append({
                "url": l["url"], "text": l["text"],
                "quality": _quality_for_pos(html, pos, l["text"]) if pos >= 0 else "",
                "lines_before": _plain_lines(html[max(0, pos - 4000):pos])[-3:] if pos >= 0 else [],
            })
        info["nexdrive_quality"] = rows
    return info


def _extract_direct_uncached(url):
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

    failures = []
    for result in movie_data.get("results", []):
        if not result.get("success"):
            # v5.2: কেন বাদ পড়ল — রেসপন্সেই দেখা যাবে (আগে চুপচাপ হারিয়ে যেত)
            if len(failures) < 40:
                failures.append({
                    "quality": result.get("quality", ""),
                    "link": result.get("source_url") or result.get("nexdrive_url") or "",
                    "text": result.get("text") or result.get("nexdrive_text") or "",
                    "type": result.get("type", ""),
                    "error": result.get("error", "unknown"),
                })
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
        # v5.1: path-segment মিলিয়ে — লম্বা টোকেনের ভেতরের "faq"/"about" আর বাদ পড়বে না
        final_lower = final.lower()
        if "t.me/" in final_lower:
            continue
        if _is_junk_url(final):
            continue
        # Bare domain homepages (e.g. https://one.vegadrive.me, https://gofile.io)
        parsed_final = urlparse(final)
        if not parsed_final.path or parsed_final.path.rstrip("/") == "":
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

        # v5.2: অমীমাংসিত হাব-পেজ (vegadrive /s/ ...) ডাউনলোড ফাইল নয় — চিহ্নিত থাকে
        if result.get("is_page") or _is_hub_host(final):
            entry["is_page"] = True

        # v5.1: সিরিজের এপিসোড নম্বর / ব্যাচ-জিপ চিহ্ন
        if result.get("season") is not None:
            entry["season"] = result["season"]
        if result.get("episode") is not None:
            entry["episode"] = result["episode"]
            if result.get("episode_guess"):
                entry["episode_guess"] = True
        elif _BATCH_RE.search((result.get("nexdrive_text", "") or "") + " " + (result.get("text", "") or "")):
            entry["batch"] = True

        direct_links.append(entry)

    episodes = sorted({e["episode"] for e in direct_links if "episode" in e})
    return {
        "success": True,
        "source": url,
        "title": movie_data.get("title", ""),
        "type": "series" if episodes else "movie",
        "is_series": bool(episodes),
        "seasons": sorted({e["season"] for e in direct_links if "season" in e}),
        "episodes": episodes,
        "total_direct_links": len(direct_links),
        "failure_count": len(failures),
        "failures": failures,
        "direct_links": direct_links,
    }


def _detect_server_name(result, url):
    """সার্ভারের নাম ডিটেক্ট"""
    text = result.get("text", "").lower()
    url_lower = url.lower()

    if "fsl" in text:
        return "FSL Server"
    if "pixel" in text or "pixeldrain" in url_lower or "pixelserver" in text:
        return "PixelServer"
    if "10gbps" in text or "cryptonewz" in url_lower:
        return "Server 10Gbps"
    if "vegadrop" in text or "vegadrive" in url_lower or "skydrop" in url_lower:
        return "Vegadrop 10Gbps"
    if "buzz" in text:
        return "BuzzServer"
    if "s3" in text and "server" in text:
        return "S3 Server"
    if "gofile" in text or "gofile" in url_lower:
        return "GoFile"
    if "filepress" in text or "filebee" in url_lower:
        return "Filepress"
    if "g-direct" in text or "instant" in text or "fastdl" in url_lower:
        return "G-Direct Instant"
    if ".r2.dev" in url_lower:
        return "CDN Direct"
    if "drive.google" in url_lower or "googleusercontent" in url_lower:
        return "Google Drive"
    if "download file" in text:
        return "Direct File"
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

    def _get_flag(self, name):
        params = parse_qs(urlparse(self.path).query)
        return params.get(name, ["0"])[0].lower() in ("1", "true", "yes")

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
            self._send_json(extract_direct(url, fresh=self._get_flag("fresh")))

        # /trace — ডিবাগ
        elif path == "/trace":
            if not url:
                self._send_json({"success": False, "error": "url parameter required"}, 400)
                return
            self._send_json(trace_page(url))

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
                "version": "5.2.0",
                "endpoints": {
                    "/direct?url=<vegamovies_url>": "ONE API - full chain bypass -> clean direct download URLs only",
                    "/trace?url=<any page url>": "debug: anchors, hidden script urls, crawler candidates, quality lines",
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

    server = ThreadingHTTPServer((HOST, PORT), APIHandler)
    print("=" * 60)
    print("  Link Extractor & Bypass API v5.2")
    print("=" * 60)
    print("  Server: http://localhost:{}".format(PORT))
    print()
    print("  Endpoints:")
    print("    /direct?url=<vegamovies_url> -> ONE API (clean output)")
    print("    /trace?url=<page url>        -> Debug trace")
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
