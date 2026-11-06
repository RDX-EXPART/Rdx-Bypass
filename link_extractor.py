"""
Link Extractor & Bypass API v5.0
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, parse_qs, quote, unquote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
from http.server import HTTPServer, BaseHTTPRequestHandler

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

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            href = d.get("href", "")
            if href:
                self._in_a = True
                self._href = href
                self._text = []
                self._cls = d.get("class", "")

    def handle_data(self, data):
        if self._in_a:
            self._text.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self.links.append({
                "href": self._href,
                "text": " ".join(self._text).strip(),
                "class": self._cls,
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
        results.append({"url": absolute, "text": item["text"], "class": item.get("class", "")})
    return results


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

    # vegadrive.me — ফলো করে ফাইনাল URL বের করা
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
              "filebee", ".r2.dev/", "vegadrive.me/d/"]
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

        # Method 3: Direct page links
        if not server_links:
            for link in parse_links(html, final_url):
                if _is_server_link(link["text"], link["url"]) and link["url"] != url:
                    server_links.append(link)

        # Method 4: JS redirects
        for js_url in re.findall(r'(?:window\.location(?:\.href)?\s*=|href\s*=)\s*["\'](https?://[^\s"\'<>]+)["\']', html):
            jl = js_url.lower()
            if any(d in jl for d in ["pixeldrain", "gofile", "filepress", "drive.google", "workers.dev", "dl.php", "fastdl", ".r2.dev"]):
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
                elif "filebee" in link_lower:
                    r = bypass_filebee(link_url, referer=url)
                    result.update(r)
                    if r.get("success") and r.get("direct_url"):
                        resolved = resolve_to_final(r["direct_url"], referer=link_url)
                        result["final_url"] = resolved["final_url"]
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

        return {"success": True, "source": url, "type": "vcloud", "total_links": len(results), "results": results}
    except Exception as e:
        return {"success": False, "error": "vcloud bypass error: {}".format(str(e))}


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
            elif any(x in url_lower for x in ["drive.google", "workers.dev", "pixeldrain", "mediafire", "gofile"]):
                categorized["direct"].append(link)
            elif any(kw in text_lower for kw in ["v-cloud", "v cloud", "v-drive", "v drive", "multi"]):
                if "vcloud" in url_lower or "v-cloud" in text_lower or "v cloud" in text_lower:
                    categorized["vcloud"].append(link)
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
            for f in as_completed(futures):
                try:
                    results.extend(f.result())
                except Exception as e:
                    results.append({"success": False, "error": str(e)})

        for link in categorized["direct"]:
            resolved = resolve_to_final(link["url"], referer=url)
            results.append({
                "source_url": link["url"], "text": link["text"],
                "type": "direct", "success": True,
                "direct_url": link["url"], "final_url": resolved["final_url"],
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
            futures = {pool.submit(_process_nexdrive, nx): nx for nx in nexdrive_links}
            for f in as_completed(futures):
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
        junk_kw = ["terms", "contact", "copyright", "privacy", "policy",
                   "login", "signup", "about", "dmca", "faq"]
        if any(jk in final_lower for jk in junk_kw):
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

        direct_links.append(entry)

    return {
        "success": True,
        "source": url,
        "total_direct_links": len(direct_links),
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
                "version": "5.0.0",
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
    print("  Link Extractor & Bypass API v5.0")
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
