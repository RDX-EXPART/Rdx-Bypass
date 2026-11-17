import os, re, base64, html as H, requests

SOLVER_API = os.environ.get("BYPARR_API", "http://127.0.0.1:8191/v1")

def _solve(url):
    r = requests.post(SOLVER_API, json={"cmd": "request.get", "url": url, "maxTimeout": 60000}, timeout=90).json()
    sol = r.get("solution", {})
    return sol.get("response", ""), sol.get("status")

def _links(body):
    out = []
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", body, re.S):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        label = re.match(r"Download\s*\[([^\]]+)\]", text)
        href = re.search(r'href="([^"]+)"', m.group(1))
        if label and href and href.group(1).startswith("http"):
            out.append({"server": label.group(1).strip(), "url": H.unescape(href.group(1))})
    return out

def vcloud(url):
    page, status = _solve(url)
    tok = re.search(r"atob\(atob\('([^']+)'\)\)", page)
    if not tok:
        raise Exception(f"vcloud: token not found (HTTP {status})")
    token_url = base64.b64decode(base64.b64decode(tok.group(1))).decode()
    body, status = _solve(token_url)
    links = _links(body)
    if not links:
        raise Exception(f"vcloud: no download links (HTTP {status})")
    name = re.search(r"<title>(.*?)</title>", body, re.S)
    size = re.search(r'id="size">([^<]+)<', body)
    return {"filename": name.group(1).strip() if name else "Unknown_File", "filesize": size.group(1).strip() if size else "Unknown", "download_links": links}

if __name__ == "__main__":
    import sys, json
    print(json.dumps(vcloud(sys.argv[1]), ensure_ascii=False, indent=2))
