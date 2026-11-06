# 🎬 Link Extractor & Bypass API v5.0

Vegamovies, Nexdrive, Fastdl, Filebee, VCloud, HubCloud — **সব চেইন বাইপাস** করে **ডাইরেক্ট ডাউনলোড লিংক** বের করে। কোয়ালিটি সহ (480p, 720p, 1080p, 2160p 4K)।

**একটা API, একটা URL — ব্যস!**

---

## 🚀 Quick Start

### লোকাল সার্ভার চালু করুন:
```bash
python link_extractor.py
```
সার্ভার চালু হবে: `http://localhost:8080`

### টেস্ট করুন:
```
http://localhost:8080/direct?url=https://vegamovies.lease/download-movie-name-480p-720p-1080p-web-dl/
```

---

## ⭐ মেইন API Endpoint: `/direct`

**এটাই আপনার মেইন API।** Vegamovies URL দিন, পুরো চেইন অটো বাইপাস হবে, শুধু ক্লিন ডাইরেক্ট ডাউনলোড URL পাবেন।

### লোকাল ব্যবহার:
```
http://localhost:8080/direct?url=VEGAMOVIES_PAGE_URL
```

### হিরোকু ব্যবহার:
```
https://YOUR-APP.herokuapp.com/direct?url=VEGAMOVIES_PAGE_URL
```

### উদাহরণ:
```
GET /direct?url=https://vegamovies.lease/download-the-mongoose-2026-hindi-dubbed-org-english-480p-720p-1080p-web-dl/
```

### আউটপুট:
```json
{
  "success": true,
  "source": "https://vegamovies.lease/download-...",
  "total_direct_links": 18,
  "direct_links": [
    {
      "url": "https://pub-xxx.r2.dev/abc?token=123",
      "server": "FSL Server",
      "quality": "480P"
    },
    {
      "url": "https://gofile.io/d/abc123",
      "server": "GoFile",
      "quality": "720P"
    },
    {
      "url": "https://pixeldrain.com/api/file/xyz",
      "server": "PixelServer",
      "quality": "1080P"
    },
    {
      "url": "https://video-downloads.googleusercontent.com/...",
      "server": "G-Direct Instant",
      "quality": "2160P"
    }
  ]
}
```

### বাইপাস চেইন যেটা অটো হয়:
```
Vegamovies পেজ
  → Nexdrive.beer লিংক খুঁজে বের করে
    → VCloud / HubCloud / Fastdl / Filebee বাইপাস করে
      → FSL Server / PixelServer / 10Gbps / GoFile → ফাইনাল URL
```

---

## 📋 সব Endpoints

| Endpoint | বর্ণনা |
|----------|--------|
| ⭐ `GET /direct?url=<URL>` | **মেইন API** — পুরো চেইন বাইপাস → শুধু ক্লিন ডাইরেক্ট URL (কোয়ালিটি সহ) |
| `GET /movie?url=<URL>` | Full chain bypass — detailed output (সব info সহ) |
| `GET /bypass?url=<URL>` | Single link bypass — nexdrive/fastdl/vcloud/hubcloud |
| `GET /api/extract?url=<URL>` | পেজ থেকে ডাউনলোড লিংক এক্সট্রাক্ট |
| `GET /api/resolve?url=<URL>` | Redirect follow → final URL |

সব endpoint GET ও POST দুইটাই সাপোর্ট করে। POST-এ JSON body: `{"url": "..."}`

---

## 🌐 সাপোর্টেড সাইট

| সাইট | স্ট্যাটাস |
|------|----------|
| vegamovies.lease (যেকোনো ডোমেইন) | ✅ Full chain bypass |
| nexdrive.beer | ✅ সব লিংক টাইপ |
| fastdl.beer | ✅ Direct URL extraction |
| filebee.xyz / filebee.top | ✅ Bypass |
| vcloud.beer | ✅ Base64 decode + server links |
| hubcloud / vegadrive | ✅ Bypass + deep resolve |
| gofile.io | ✅ Direct link |
| pixeldrain | ✅ Direct download API |

---

## ☁️ Heroku তে ডিপ্লয় করার নিয়ম

### প্রয়োজনীয়:
- [Git](https://git-scm.com/downloads) ইনস্টল থাকতে হবে
- [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) ইনস্টল থাকতে হবে
- Heroku অ্যাকাউন্ট থাকতে হবে

### স্টেপ ১: ZIP এক্সট্রাক্ট করুন
ZIP ফাইল এক্সট্রাক্ট করুন। ভিতরে `url-extractor` ফোল্ডার পাবেন।

### স্টেপ ২: টার্মিনাল/CMD খুলুন
```bash
cd url-extractor
```

### স্টেপ ৩: Heroku Login
```bash
heroku login
```
ব্রাউজার খুলবে, লগইন করুন।

### স্টেপ ৪: নতুন App তৈরি করুন (প্রথমবার)
```bash
heroku create my-link-extractor
```
অথবা আগের app-এ ডিপ্লয় করতে চাইলে:
```bash
heroku git:remote -a YOUR-EXISTING-APP-NAME
```
যেমন:
```bash
heroku git:remote -a newrdxbyposs-3953bc054fb6
```

### স্টেপ ৫: Git Init ও Push
```bash
git init
git add .
git commit -m "Deploy link extractor v5.0"
git push heroku main
```

> **যদি এরর আসে `main` দিয়ে:**
> ```bash
> git push heroku master
> ```

### স্টেপ ৬: চেক করুন
```
https://YOUR-APP.herokuapp.com/
```
দেখবেন `"version": "5.0.0"` দেখাচ্ছে। তাহলে সফল!

### স্টেপ ৭: টেস্ট করুন
```
https://YOUR-APP.herokuapp.com/direct?url=https://vegamovies.lease/download-movie-name-480p-720p-1080p-web-dl/
```

---

## ❌ 404 Not Found সমস্যা সমাধান

### সমস্যা:
`/direct` endpoint-এ `{"error": "Not Found"}` আসছে।

### কারণ:
**পুরানো কোড** চলছে হিরোকুতে। হিরোকুতে v2.0 আছে যেখানে `/direct` endpoint ছিল না।

### সমাধান:
**নতুন কোড (v5) ডিপ্লয় করুন।** উপরের "Heroku তে ডিপ্লয়" সেকশন ফলো করুন।

### যাচাই করুন:
```
https://YOUR-APP.herokuapp.com/
```
- ✅ `"version": "5.0.0"` দেখালে নতুন কোড চলছে
- ❌ `"version": "2.0.0"` দেখালে পুরানো কোড চলছে — রিডিপ্লয় করুন

---

## 🤖 Leech Bot-এ ব্যবহার

```python
import requests

# আপনার API URL
API = "https://YOUR-APP.herokuapp.com"

# Vegamovies URL থেকে সব ডাইরেক্ট লিংক পান
def get_download_links(movie_url):
    resp = requests.get(f"{API}/direct", params={"url": movie_url}, timeout=120)
    data = resp.json()
    
    if not data.get("success"):
        print("Error:", data.get("error"))
        return []
    
    links = []
    for link in data.get("direct_links", []):
        print(f"[{link['quality']}] {link['server']}: {link['url']}")
        links.append(link)
    
    return links

# ব্যবহার
links = get_download_links("https://vegamovies.lease/download-movie-name/")

# নির্দিষ্ট কোয়ালিটি ফিল্টার
links_1080p = [l for l in links if l["quality"] == "1080P"]
links_720p = [l for l in links if l["quality"] == "720P"]
```

---

## 🔄 Vegamovies ডোমেইন চেঞ্জ হলে

**কোডে কিছু করতে হবে না!** শুধু URL-এ নতুন ডোমেইন দিন:

```
# আগে:
/direct?url=https://vegamovies.lease/download-...

# নতুন ডোমেইন:
/direct?url=https://vegamovies.NEW-DOMAIN/download-...
```

কোডে vegamovies ডোমেইন হার্ডকোড করা নেই। যেকোনো ডোমেইনের URL দিলেই কাজ করবে।

---

## 📁 ফাইল স্ট্রাকচার

```
url-extractor/
├── link_extractor.py   # মেইন API সার্ভার (Python)
├── Procfile            # Heroku process file
├── runtime.txt         # Python version (3.12.10)
├── requirements.txt    # Dependencies (built-in only)
├── .gitignore          # Git ignore
└── README.md           # এই ফাইল
```

## 📝 নোট

- **কোনো pip dependency নেই** — শুধু Python built-in library ব্যবহার করে
- **Google Drive URL**: `proxy_url` ফিল্ডে proxy URL থাকে যা ব্রাউজারে কাজ করে
- **Parallel processing** — ThreadPoolExecutor দিয়ে দ্রুত bypass
- **CORS enabled** — যেকোনো frontend/bot থেকে কল করা যায়
- **Timeout**: ডিফল্ট 20 সেকেন্ড প্রতি রিকোয়েস্ট, পুরো চেইন ~30-40 সেকেন্ড
