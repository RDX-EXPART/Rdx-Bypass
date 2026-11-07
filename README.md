# Link Extractor & Bypass API v6.0

vegamovies / nexdrive / vcloud / hubcloud / fastdl সব চেইন বাইপাস করে ডাইরেক্ট ডাউনলোড লিংক বের করে।

> [!IMPORTANT]
> কোনো pip install লাগে না — Python বিল্ট-ইন লাইব্রেরি দিয়ে চলে।

---

## ফাইলসমূহ

| ফাইল | বিবরণ |
|------|--------|
| `link_extractor.py` | মেইন API সার্ভার — vegamovies পুরো চেইন বাইপাস |
| `vcloud.py` | স্ট্যান্ডঅ্যালোন vcloud.beer বাইপাস API |
| `Procfile` | Heroku deployment file |
| `runtime.txt` | Python version for Heroku |
| `requirements.txt` | Dependencies (empty — built-in only) |

---

## মেইন API (link_extractor.py)

### লোকালহোস্ট চালানো:
```bash
python link_extractor.py
# Server: http://localhost:8080
```

### Heroku তে চালানো:
```
https://YOUR-APP.herokuapp.com/direct?url=<vegamovies_url>
```

### Endpoints:

| Endpoint | বিবরণ | ব্যবহার |
|----------|--------|---------|
| `GET /direct?url=<URL>` | **মেইন API** — পুরো চেইন বাইপাস → ক্লিন ডাইরেক্ট URL | vegamovies URL দিন |
| `GET /movie?url=<URL>` | Full chain bypass (detailed output) | vegamovies URL দিন |
| `GET /bypass?url=<URL>` | Single site bypass | nexdrive/vcloud/fastdl URL দিন |
| `GET /api/extract?url=<URL>` | পেজ থেকে ডাউনলোড লিংক extract | যেকোনো URL |
| `GET /api/resolve?url=<URL>` | Redirect follow → final URL | যেকোনো URL |

### ব্যবহারের উদাহরণ:

#### /direct — মেইন API (সবচেয়ে সহজ)
```
GET /direct?url=https://vegamovies.lease/download-heart-of-the-beast-2026-...
```
**Response:**
```json
{
  "success": true,
  "source": "https://vegamovies.lease/download-...",
  "total_direct_links": 18,
  "direct_links": [
    {
      "url": "https://pub-xxx.r2.dev/HASH?token=...",
      "server": "G-Direct Instant",
      "quality": "480P"
    },
    {
      "url": "https://video-downloads.googleusercontent.com/...",
      "server": "Server 10Gbps",
      "quality": "720P"
    }
  ]
}
```

#### /bypass — vcloud bypass
```
GET /bypass?url=https://vcloud.beer/a3gekcri0o1ygec
```

---

## VCloud Bypass (vcloud.py)

### স্ট্যান্ডঅ্যালোন API:
```bash
python vcloud.py
# Server: http://localhost:8081
```

### CLI Mode:
```bash
python vcloud.py https://vcloud.beer/HASH
```

### Endpoint:
```
GET /bypass?url=https://vcloud.beer/HASH
```

### সাপোর্টেড সার্ভার:
| সার্ভার | লিংক টাইপ |
|---------|-----------|
| FSLv2 Server | `r2.cloudflarestorage.com` (Cloudflare R2 CDN) |
| FSL Server | `pub-xxx.r2.dev` (Cloudflare R2 CDN) |
| PixelServer | `pixeldrain.dev/u/` → `pixeldrain.com/api/file/` |
| Server 10Gbps | `gpdl2.hubcloud.pw` → `workers.dev` → `cryptonewz.online/dl.php` → Google Drive |
| VegaDrive | `vegadrive.me/app/co` |
| GoFile | `gofile.io` |

### Response Format:
```json
{
  "success": true,
  "source": "https://vcloud.beer/a3gekcri0o1ygec",
  "total_servers": 3,
  "file_name": "Movie.Name.480p.mkv",
  "file_size": "366.94 MB",
  "mime_type": "video/matroska",
  "servers": [
    {
      "name": "FSL Server",
      "direct_url": "https://pub-xxx.r2.dev/HASH?token=...",
      "source_url": "https://pub-xxx.r2.dev/HASH?token=...",
      "success": true
    },
    {
      "name": "Server 10Gbps",
      "direct_url": "https://video-downloads.googleusercontent.com/...",
      "proxy_url": "https://cryptonewz.online/dl.php?link=...",
      "source_url": "https://gpdl2.hubcloud.pw/?id=...",
      "success": true,
      "note": "proxy_url ব্রাউজারে কাজ করে, direct_url Google Drive raw"
    }
  ]
}
```

---

## Heroku Deployment

### Step 1: GitHub Repo তৈরি
```bash
git init
git add .
git commit -m "v6.0"
git remote add origin https://github.com/YOUR-USER/YOUR-REPO.git
git push -u origin main
```

### Step 2: Heroku তে Deploy
1. [Heroku Dashboard](https://dashboard.heroku.com/) → **New** → **Create new app**
2. **Deploy** tab → **GitHub** select → repo connect
3. **Manual Deploy** → **Deploy Branch**

### Step 3: ব্যবহার
```
https://YOUR-APP.herokuapp.com/direct?url=https://vegamovies.lease/download-...
```

> [!WARNING]
> **404 Not Found আসলে:**
> - Heroku তে পুরানো কোড deploy আছে। নতুন v6 ZIP deploy করুন।
> - `/direct` endpoint শুধু v5+ এ আছে।

---

## Telegram Bot Example

```python
import requests
from telegram import Update
from telegram.ext import CommandHandler, ApplicationBuilder

async def extract(update, context):
    url = " ".join(context.args)
    if not url:
        await update.message.reply_text("URL দিন: /extract <vegamovies_url>")
        return
    
    api = f"https://YOUR-APP.herokuapp.com/direct?url={url}"
    r = requests.get(api, timeout=120).json()
    
    if r.get("success"):
        msg = f"🎬 Total: {r['total_direct_links']} links\n\n"
        for link in r["direct_links"]:
            q = link.get("quality", "")
            s = link.get("server", "")
            msg += f"📥 [{q}] {s}\n{link['url']}\n\n"
        await update.message.reply_text(msg[:4096])
    else:
        await update.message.reply_text(f"❌ Error: {r.get('error')}")

app = ApplicationBuilder().token("BOT_TOKEN").build()
app.add_handler(CommandHandler("extract", extract))
app.run_polling()
```

---

## ডোমেইন চেঞ্জ হলে কি করতে হবে?

vegamovies ডোমেইন চেঞ্জ করলে (যেমন `vegamovies.lease` → `vegamovies.new`):
- **কিছু করতে হবে না!** কোডে কোনো হার্ডকোড ডোমেইন নেই।
- শুধু নতুন URL দিয়ে API কল করুন।

nexdrive/vcloud/fastdl ডোমেইন চেঞ্জ করলে:
- `link_extractor.py` তে domain patterns আপডেট করতে হবে।

---

## Changelog

### v6.0 (Latest)
- ✅ **10Gbps Server bypass** — `gpdl2.hubcloud.pw` → `workers.dev` → `cryptonewz.online/dl.php` → Google Drive URL
- ✅ **vcloud.py** — স্ট্যান্ডঅ্যালোন vcloud bypass API
- ✅ File info extraction (name, size, MIME type)
- ✅ Fixed bare domain detection for query-string URLs

### v5.0
- ✅ Quality detection (480p, 720p, 1080p, 2160p 4K)
- ✅ `/direct` endpoint — clean output
- ✅ Deep URL resolution
- ✅ Heroku support

### v4.0
- ✅ vcloud.beer bypass (double base64)
- ✅ vegadrive.app/.co support
- ✅ Junk URL filtering
