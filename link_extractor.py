"""
Link Extractor & Bypass API
============================
vegamovies / nexdrive.beer / fastdl.beer / filebee চেইন বাইপাস করে
ডাইরেক্ট ডাউনলোড লিংক (Google Drive etc.) বের করে।

Endpoints:
  GET /movie?url=<vegamovies_url>     -> nexdrive + bypass সব একসাথে
  GET /bypass?url=<nexdrive_url>      -> nexdrive/fastdl বাইপাস করে ডাইরেক্ট লিংক
  GET /api/extract?url=<URL>          -> পেজ থেকে ডাউনলোড লিংক এক্সট্রাক্ট
  GET /api/resolve?url=<URL>          -> রিডাইরেক্ট ফলো করে ফাইনাল URL

ব্যবহার:
  python link_extractor.py

কোনো pip install লাগে না — Python বিল্ট-ইন লাইব্রেরি দিয়ে চলে।
"""

import json
import os
import re
import ssl
import sys
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, parse_qs, quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── কনফিগ ───
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8080))  # Heroku sets PORT env var
MAX_REDIRECTS = 15
TIMEOUT = 15

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
def fetch(url, referer=None):
    """URL ফেচ করে (HTML, final_url) রিটার্ন করে"""
    req = Request(url)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    if referer:
        req.add_header("Referer", referer)

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


# ─── HTML Link Parser ───
class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []
        self._in_a = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            href = d.get("href", "")
            if href:
                self._in_a = True
                self._href = href
                self._text = []

    def handle_data(self, data):
        if self._in_a:
            self._text.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self.links.append({"href": self._href, "text": " ".join(self._text).strip()})
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
        results.append({"url": absolute, "text": item["text"]})
    return results


# ════════════════════════════════════════════
#  BYPASS LOGIC
# ════════════════════════════════════════════

def bypass_fastdl(url, referer=None):
    """
    fastdl.beer/embed.php পেজ থেকে ডাইরেক্ট ডাউনলোড URL বের করে।
    JavaScript-এ var reurl = "..." থাকে — সেটা parse করে।
    """
    try:
        html, _ = fetch(url, referer=referer)

        # Pattern 1: var reurl = "URL";
        match = re.search(r'var\s+reurl\s*=\s*["\']([^"\']+)["\']', html)
        if match:
            dl_url = match.group(1)
            # fastdl.beer/dl.php?link=... থেকে আসল URL বের করা
            if "dl.php?link=" in dl_url:
                parsed = urlparse(dl_url)
                qs = parse_qs(parsed.query)
                if "link" in qs:
                    return {
                        "success": True,
                        "direct_url": qs["link"][0],
                        "proxy_url": dl_url,
                        "source": "fastdl",
                    }
            return {"success": True, "direct_url": dl_url, "source": "fastdl"}

        # Pattern 2: window.location = "URL"
        match = re.search(r'window\.location\s*=\s*["\']([^"\']+)["\']', html)
        if match:
            return {"success": True, "direct_url": match.group(1), "source": "fastdl_redirect"}

        # Pattern 3: Any Google Drive / googleusercontent URL in the page
        gdrive = re.findall(r'https?://[^\s"\'<>]*(?:drive\.google\.com|video-downloads\.googleusercontent\.com|docs\.google\.com)[^\s"\'<>]*', html)
        if gdrive:
            return {"success": True, "direct_url": gdrive[0], "source": "fastdl_gdrive"}

        return {"success": False, "error": "fastdl.beer: direct URL not found in page"}

    except Exception as e:
        return {"success": False, "error": f"fastdl.beer bypass error: {str(e)}"}


def bypass_filebee(url, referer=None):
    """filebee.xyz / filebee.top / filebee.live পেজ থেকে ডাইরেক্ট URL বের করে"""
    try:
        html, final_url = fetch(url, referer=referer)

        # Pattern 1: var reurl / window.location
        for pattern in [
            r'var\s+reurl\s*=\s*["\']([^"\']+)["\']',
            r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
            r'window\.open\(["\']([^"\']+)["\']',
        ]:
            match = re.search(pattern, html)
            if match:
                dl = match.group(1)
                if "dl.php?link=" in dl:
                    parsed = urlparse(dl)
                    qs = parse_qs(parsed.query)
                    if "link" in qs:
                        return {"success": True, "direct_url": qs["link"][0], "proxy_url": dl, "source": "filebee"}
                return {"success": True, "direct_url": dl, "source": "filebee"}

        # Pattern 2: Google Drive links
        gdrive = re.findall(r'https?://[^\s"\'<>]*(?:drive\.google\.com|googleusercontent\.com)[^\s"\'<>]*', html)
        if gdrive:
            return {"success": True, "direct_url": gdrive[0], "source": "filebee_gdrive"}

        # Pattern 3: download button href
        dl_links = re.findall(r'href=["\']([^"\']*(?:download|dl\.php|file)[^"\']*)["\']', html)
        if dl_links:
            return {"success": True, "direct_url": urljoin(final_url, dl_links[0]), "source": "filebee_link"}

        return {"success": False, "error": "filebee: direct URL not found"}

    except Exception as e:
        return {"success": False, "error": f"filebee bypass error: {str(e)}"}


def bypass_nexdrive(url):
    """
    nexdrive.beer পেজ থেকে সব ডাউনলোড লিংক বের করে এবং
    প্রতিটি fastdl.beer/filebee লিংক বাইপাস করে ডাইরেক্ট URL দেয়।
    """
    try:
        html, final_url = fetch(url)

        # পেজ থেকে সব লিংক বের করা
        all_links = parse_links(html, final_url)

        # Episode info extract
        episodes = []
        episode_blocks = re.findall(
            r'-:Episode[:\s]*(\d+):-.*?(?=(?:-:Episode|$))',
            html, re.DOTALL | re.IGNORECASE
        )

        # fastdl এবং filebee লিংক ফিল্টার
        fastdl_links = []
        filebee_links = []
        other_download_links = []

        for link in all_links:
            url_lower = link["url"].lower()
            if "fastdl" in url_lower:
                fastdl_links.append(link)
            elif "filebee" in url_lower:
                filebee_links.append(link)
            elif any(x in url_lower for x in ["drive.google", "workers.dev", "pixeldrain", "mediafire", "gofile"]):
                other_download_links.append(link)

        # প্রতিটি লিংক বাইপাস করা — PARALLEL (ThreadPool)
        results = []

        def _bypass_one(link, link_type, referer):
            if link_type == "fastdl":
                r = bypass_fastdl(link["url"], referer=referer)
            else:
                r = bypass_filebee(link["url"], referer=referer)
            return {"source_url": link["url"], "text": link["text"], "type": link_type, **r}

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for link in fastdl_links:
                futures.append(pool.submit(_bypass_one, link, "fastdl", url))
            for link in filebee_links:
                futures.append(pool.submit(_bypass_one, link, "filebee", url))
            for f in as_completed(futures):
                try:
                    results.append(f.result())
                except Exception as e:
                    results.append({"success": False, "error": str(e)})

        for link in other_download_links:
            results.append({
                "source_url": link["url"],
                "text": link["text"],
                "type": "direct",
                "success": True,
                "direct_url": link["url"],
            })

        return {
            "success": True,
            "source": url,
            "total_links": len(results),
            "results": results,
        }

    except Exception as e:
        return {"success": False, "error": f"nexdrive bypass error: {str(e)}"}


# ════════════════════════════════════════════
#  MOVIE ENDPOINT — vegamovies full chain
# ════════════════════════════════════════════

# vegamovies filter patterns
EXCLUDE_PATTERNS = re.compile(
    r"facebook|twitter|instagram|telegram\.|t\.me|youtube|whatsapp|"
    r"javascript:|mailto:|\.(?:jpg|jpeg|png|gif|svg|webp|ico|css)(?:\?|$)",
    re.IGNORECASE,
)


def extract_movie(url):
    """
    vegamovies পেজ থেকে পুরো চেইন:
    1. vegamovies পেজ → nexdrive.beer লিংক খুঁজে বের করা
    2. প্রতিটি nexdrive লিংক → fastdl/filebee → ডাইরেক্ট ডাউনলোড URL
    সব PARALLEL-এ প্রসেস হয় — দ্রুত!
    """
    try:
        html, final_url = fetch(url)
        all_links = parse_links(html, final_url)

        # nexdrive লিংক ফিল্টার
        nexdrive_links = [l for l in all_links if "nexdrive" in l["url"].lower()]

        if not nexdrive_links:
            # nexdrive না পেলে সরাসরি fastdl/filebee খুঁজি
            fastdl_links = [l for l in all_links if "fastdl" in l["url"].lower()]
            filebee_links = [l for l in all_links if "filebee" in l["url"].lower()]

            results = []
            def _bypass_direct(link, ltype):
                if ltype == "fastdl":
                    r = bypass_fastdl(link["url"], referer=url)
                else:
                    r = bypass_filebee(link["url"], referer=url)
                return {"source_url": link["url"], "text": link["text"], "type": ltype, **r}

            with ThreadPoolExecutor(max_workers=10) as pool:
                futs = []
                for l in fastdl_links:
                    futs.append(pool.submit(_bypass_direct, l, "fastdl"))
                for l in filebee_links:
                    futs.append(pool.submit(_bypass_direct, l, "filebee"))
                for f in as_completed(futs):
                    try:
                        results.append(f.result())
                    except Exception as e:
                        results.append({"success": False, "error": str(e)})

            return {
                "success": True,
                "source": url,
                "total_links": len(results),
                "results": results,
            }

        # প্রতিটি nexdrive লিংক বাইপাস — PARALLEL
        all_results = []

        def _process_nexdrive(nx_link):
            bypass_data = bypass_nexdrive(nx_link["url"])
            items = []
            if bypass_data.get("success") and bypass_data.get("results"):
                for r in bypass_data["results"]:
                    r["nexdrive_url"] = nx_link["url"]
                    r["nexdrive_text"] = nx_link["text"]
                    items.append(r)
            else:
                items.append({
                    "nexdrive_url": nx_link["url"],
                    "nexdrive_text": nx_link["text"],
                    "success": False,
                    "error": bypass_data.get("error", "bypass failed"),
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
            "success": True,
            "source": url,
            "total_nexdrive_links": len(nexdrive_links),
            "total_direct_links": sum(1 for r in all_results if r.get("success")),
            "results": all_results,
        }

    except Exception as e:
        return {"success": False, "error": f"movie extract error: {str(e)}"}


def extract_download_links(url):
    """পেজ থেকে ডাউনলোড লিংক এক্সট্রাক্ট (ফিল্টার সহ)"""
    try:
        html, final_url = fetch(url)
        all_links = parse_links(html, final_url)

        download_links = []
        dl_keywords_url = re.compile(
            r"nexdrive|hubcloud|hubdrive|gdtot|filepress|filebee|fastdl|"
            r"gdflix|gofile|mediafire|mega\.nz|pixeldrain|"
            r"drive\.google|workers\.dev|"
            r"\.mkv|\.mp4|\.zip|\.rar|/download|/dl\?",
            re.IGNORECASE,
        )
        dl_keywords_text = [
            "download", "direct", "480p", "720p", "1080p", "2160p", "4k",
            "g-direct", "instant", "batch", "zip", "gdrive",
        ]

        for link in all_links:
            if EXCLUDE_PATTERNS.search(link["url"]):
                continue
            is_dl = bool(dl_keywords_url.search(link["url"]))
            if not is_dl:
                is_dl = any(kw in link["text"].lower() for kw in dl_keywords_text)
            if is_dl:
                download_links.append(link)

        return {
            "success": True,
            "source_url": url,
            "total_links_found": len(all_links),
            "download_links_count": len(download_links),
            "download_links": download_links,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def follow_redirects(url):
    """URL-এর রিডাইরেক্ট ফলো করে ফাইনাল URL বের করে"""
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

    def do_GET(self):
        path = urlparse(self.path).path

        # Home
        if path == "/":
            self._send_json({
                "app": "Link Extractor & Bypass API",
                "version": "2.0.0",
                "endpoints": {
                    "/movie?url=<vegamovies_url>": "vegamovies full chain bypass -> direct download links",
                    "/bypass?url=<nexdrive_url>": "nexdrive/fastdl/filebee bypass -> direct link",
                    "/api/extract?url=<URL>": "page download links extract",
                    "/api/resolve?url=<URL>": "follow redirects -> final URL",
                },
            })
            return

        url = self._get_url_param()
        if not url and path != "/":
            self._send_json({"success": False, "error": "url parameter required"}, 400)
            return

        # /movie — vegamovies full chain
        if path == "/movie":
            self._send_json(extract_movie(url))

        # /bypass — nexdrive/fastdl/filebee bypass
        elif path == "/bypass":
            url_lower = url.lower()
            if "nexdrive" in url_lower:
                self._send_json(bypass_nexdrive(url))
            elif "fastdl" in url_lower:
                self._send_json(bypass_fastdl(url))
            elif "filebee" in url_lower:
                self._send_json(bypass_filebee(url))
            else:
                # Generic: try fetching and finding direct URLs
                self._send_json(bypass_nexdrive(url))

        # /api/extract
        elif path == "/api/extract":
            self._send_json(extract_download_links(url))

        # /api/resolve
        elif path == "/api/resolve":
            self._send_json(follow_redirects(url))

        else:
            self._send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        url = self._get_post_url()
        if not url:
            self._send_json({"success": False, "error": "url field required in JSON body"}, 400)
            return

        if path == "/movie":
            self._send_json(extract_movie(url))
        elif path == "/bypass":
            url_lower = url.lower()
            if "nexdrive" in url_lower:
                self._send_json(bypass_nexdrive(url))
            elif "fastdl" in url_lower:
                self._send_json(bypass_fastdl(url))
            elif "filebee" in url_lower:
                self._send_json(bypass_filebee(url))
            else:
                self._send_json(bypass_nexdrive(url))
        elif path == "/api/extract":
            self._send_json(extract_download_links(url))
        elif path == "/api/resolve":
            self._send_json(follow_redirects(url))
        else:
            self._send_json({"error": "Not Found"}, 404)

    def log_message(self, format, *args):
        print(f"  [{self.log_date_time_string()}] {args[0]}")


# ─── Start Server ───
def start_server():
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    server = HTTPServer((HOST, PORT), APIHandler)
    print("=" * 60)
    print("  Link Extractor & Bypass API v2.0")
    print("=" * 60)
    print(f"  Server: http://localhost:{PORT}")
    print()
    print("  Endpoints:")
    print(f"    /movie?url=<vegamovies_url>  -> Full chain bypass")
    print(f"    /bypass?url=<nexdrive_url>   -> Nexdrive/Fastdl bypass")
    print(f"    /api/extract?url=<URL>       -> Extract download links")
    print(f"    /api/resolve?url=<URL>       -> Follow redirects")
    print()
    print("  Example:")
    print(f"    http://localhost:{PORT}/movie?url=https://vegamovies.lease/download-...")
    print(f"    http://localhost:{PORT}/bypass?url=https://nexdrive.beer/genxfm...")
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
