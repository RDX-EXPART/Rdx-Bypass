# Link Extractor & Bypass API v6.3

vegamovies (Movie & Series) / nexdrive / vcloud / vegadrive / hubcloud / fastdl সব চেইন বাইপাস করে ক্লিন ডাইরেক্ট ডাউনলোড লিংক বের করে (কোনো `dl.php?link=` র‍্যাপার ছাড়া)।

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
| `GET /bypass?url=<URL>` | **ONE API** — vegamovies / nexdrive / vcloud / vegadrive / fastdl সব বাইপাস | vegamovies বা যেকোনো হোস্ট URL দিন |
| `GET /direct?url=<URL>` | পুরো চেইন বাইপাস → শুধু ক্লিন ডাইরেক্ট URL | vegamovies URL দিন |
| `GET /movie?url=<URL>` | Full chain bypass (detailed output) | vegamovies URL দিন |
| `GET /api/extract?url=<URL>` | পেজ থেকে ডাউনলোড লিংক extract | যেকোনো URL |
| `GET /api/resolve?url=<URL>` | Redirect follow → final URL | যেকোনো URL |

### ব্যবহারের উদাহরণ:

#### /bypass — সিঙ্গেল API (vegamovies → সব ডাইরেক্ট লিংক)
```
GET /bypass?url=https://vegamovies.lease/download-the-odyssey-2026-hindi-org-english-480p-720p-1080p-bluray/
```
**Response:**
```json
{
  "success": true,
  "source": "https://vegamovies.lease/download-...",
  "total_direct_links": 20,
  "direct_links": [
    {
      "url": "https://video-downloads.googleusercontent.com/...",
      "server": "G-Direct Instant",
      "quality": "480P"
    },
    {
      "url": "https://pub-xxx.r2.dev/HASH?token=...",
      "server": "FSL Server",
      "quality": "480P"
    },
    {
      "url": "https://video-downloads.googleusercontent.com/...",
      "server": "Server 10Gbps",
      "quality": "720P"
    },
    {
      "url": "https://lh3.googleusercontent.com/pw/...=dv",
      "server": "Vegadrop 10Gbps",
      "quality": "720P"
    }
  ]
}
```

#### /bypass — সিরিজ (Series 2-Step Fast Mode + `&full=1`)
```
Step 1: GET /bypass?url=https://vegamovies.lease/download-marshals-2026-season-1-2-hindi-dubbed-org/
Step 2: GET /bypass?url=<pack_url>   (অথবা একবারেই সব পেতে &full=1 যোগ করুন)
```
**Step 1 Response (~1.6s — Heroku টাইমআউট মুক্ত):**
```json
{
  "success": true,
  "is_series": true,
  "total_packs": 19,
  "packs": [
    {
      "index": 1,
      "season": "Season 2",
      "audio": "Hindi-English",
      "quality": "480P",
      "label": "Season 2 | Hindi-English | 480P | ⚡ G-Direct [Instant]",
      "url": "https://nexdrive.beer/genxfm784776512272/",
      "bypass_url": "/bypass?url=https%3A%2F%2Fnexdrive.beer%2Fgenxfm784776512272%2F"
    }
  ]
}
```
**Step 2 Response (`direct_links` — Episode অনুযায়ী সাজানো):**
```json
{
  "success": true,
  "source": "https://nexdrive.beer/genxfm784776512269/",
  "total_direct_links": 16,
  "direct_links": [
    {
      "url": "https://...r2.cloudflarestorage.com/...",
      "server": "FSLv2 Server",
      "episode": "Episode 1"
    },
    {
      "url": "https://pixeldrain.com/api/file/...",
      "server": "PixelServer",
      "episode": "Episode 1"
    }
  ]
}
```

#### /bypass — নির্দিষ্ট vcloud / nexdrive URL
```
GET /bypass?url=https://vcloud.beer/ajzbtthabonhzuo
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
      "source_url": "https://gpdl2.hubcloud.pw/?id=...",
      "success": true,
      "http_status": 200
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
> - Heroku তে পুরানো কোড deploy আছে। নতুন `v6.3` ZIP deploy করুন।
>
> **Heroku তে `vcloud.beer` এ `HTTP Error 403: Forbidden` কেন আসে ও সমাধান কী:**
> - Heroku এর AWS ডাটাসেন্টার IP রেঞ্জ কে `vcloud.beer` এর Cloudflare ফায়ারওয়াল ব্লক করে (`HTTP 403`), কিন্তু **লোকালহোস্ট (PC/Termux/রেসিডেনশিয়াল ইন্টারনেট)** থেকে `vcloud.beer` সম্পূর্ণ `200 OK` তে কাজ করে।
> - **সমাধান ১ (Heroku Proxy Config Var):** আপনার কাছে নিজস্ব রেসিডেনশিয়াল বা আনব্লকড HTTP/HTTPS প্রক্সি থাকলে Heroku Settings → Config Vars এ `PROXY_URL` (বা `HTTPS_PROXY`) সেট করলে `vcloud.beer` ও কাজ করবে।
> - **সমাধান ২ (Proxy ছাড়া Heroku):** কোনো প্রক্সি ছাড়াই Heroku থেকে `/bypass?url=<vegamovies_url>` দিলে `vegamovies` → `nexdrive` → **`G-Direct Instant`** (`video-downloads.googleusercontent.com`) এবং **`Vegadrop 10Gbps`** (`lh3.googleusercontent.com/...=dv`) + GoFile/TransferIt সরাসরি সলভ হয়ে আসে (কোনো `dl.php?link=` ছাড়া)।
> - **সমাধান ৩ (লোকাল/Termux):** পুরো `FSL Server`, `FSLv2`, `PixelServer`, `Server 10Gbps` একসাথে পেতে চাইলে PC, Termux বা নন-ব্লকড VPS থেকে চালান।

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
    
    api = f"https://YOUR-APP.herokuapp.com/bypass?url={url}"
    r = requests.get(api, timeout=120).json()
    
    if r.get("is_series") and r.get("packs"):
        msg = f"📺 Series Packs ({r['total_packs']}):\n\n"
        for p in r["packs"]:
            msg += f"{p['index']}. {p['label']}\n/extract {p['url']}\n\n"
        await update.message.reply_text(msg[:4096])
    elif r.get("success"):
        msg = f"🎬 Total: {r['total_direct_links']} links\n\n"
        for link in r.get("direct_links", []):
            tag = link.get("episode") or link.get("quality") or ""
            s = link.get("server", "")
            msg += f"📥 [{tag}] {s}\n{link['url']}\n\n"
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

### v6.3 (Latest)
- ✅ **Series 2-Step Fast Mode (`is_series` + `packs`)** — বড় সিরিজ পেজ (`Marshals` ইত্যাদি) দিলে ~1–2s এ সব Season/Audio/Quality প্যাক ও `bypass_url` লিস্ট দেয় (Heroku 30s টাইমআউট এড়ায়)
- ✅ **Episode Detection & Grouping (`Episode 1` .. `Episode N`)** — সিরিজ প্যাক বাইপাসে এপিসোড অনুযায়ী ক্রমানুসারে ক্লিন `direct_links` দেয়
- ✅ **`&full=1` Full Series Bypass Option** — লোকালহোস্ট/VPS থেকে পুরো সিরিজ একসাথে বাইপাসের সাপোর্ট
- ✅ **Speed & Parallel Mirror Tuning + Transient Retry** — `fetch()` অটো-রিট্রাই এবং প্যারালাল VegaDrive মিরর রেজোলিউশন (১৩-এপিসোড প্যাকও `<30s` এ সম্পন্ন)

### v6.2
- ✅ **Single `/bypass` Endpoint for Vegamovies** — এখন `/bypass?url=<vegamovies_url>` দিলেই পুরো চেইন (`vegamovies` → `nexdrive` → `vcloud`/`fastdl`/`vegadrive`) সলভ হয়ে ক্লিন ডাইরেক্ট লিংক দেয়
- ✅ **Removed `dl.php?link=` & `proxy_url`** — `fastdl.beer/dl.php?link=` এবং `cryptonewz.online/dl.php?link=` র‍্যাপার সম্পূর্ণ বাদ দিয়ে শুধু আসল ফাইনাল ডাউনলোড URL রিটার্ন করে
- ✅ **Server Label Fix** — `fastdl` লিংক (`G-Direct [10Gbps]` ইত্যাদি) সঠিকভাবে `G-Direct Instant` নামে লেবেল হয়
- ✅ **VegaDrive 502 Retry & Timeout Optimization** — `skydrop` এর ক্ষণস্থায়ী `502` এরর রিট্রাই করে `Vegadrop 10Gbps` (`lh3.googleusercontent.com/...=dv`) বের করে (Heroku 30s লিমিটের মধ্যেই)
- ✅ **`PROXY_URL` / `HTTPS_PROXY` Support** — Heroku Config Vars থেকে প্রক্সি ব্যবহারের সুবিধা + 403 ওয়ার্নিং রিপোর্টিং

### v6.1
- ✅ **PixelServer JS Override Fix** — পেজের `var pxl = "..."` থেকে আসল PixelDrain ID বের করে (ডিকয় 404 লিংক এড়ায়)
- ✅ **VegaDrive CookieSession & Skydrop Bypass** — `.app` → `.me` + কুকি সেশন + `Vegadrop 10Gbps` ডাইরেক্ট লিংক ও রেডি মিরর এক্সট্রাক্ট
- ✅ **Live 403/404 Link Validation** — ডেড R2/PixelDrain লিংক (`HTTP 403/404`) অটো-ডিটেক্ট ও `/direct` থেকে ফিল্টার
- ✅ **Accurate Quality Slice Detection** — `480P` → `720P` → `1080P` অর্ডার ও কোয়ালিটি ম্যাচিং ফিক্স
- ✅ **10Gbps Server bypass** — `gpdl2.hubcloud.pw` → `workers.dev` → `cryptonewz.online/dl.php` → Google Drive URL
- ✅ **vcloud.py** — স্ট্যান্ডঅ্যালোন vcloud bypass API + CLI মোড (`python vcloud.py <url>`)

### v5.0
- ✅ Quality detection (480p, 720p, 1080p, 2160p 4K)
- ✅ `/direct` endpoint — clean output
- ✅ Deep URL resolution
- ✅ Heroku support

### v4.0
- ✅ vcloud.beer bypass (double base64)
- ✅ vegadrive.app/.co support
- ✅ Junk URL filtering
