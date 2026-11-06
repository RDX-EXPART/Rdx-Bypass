# URL Extractor 🔗

শর্টেনড/রিডাইরেক্ট URL থেকে আসল (original) URL বের করার Express.js অ্যাপ্লিকেশন।

## ফিচারসমূহ

- ✅ শর্ট URL (bit.ly, tinyurl, t.co ইত্যাদি) থেকে আসল URL বের করা
- ✅ পুরো রিডাইরেক্ট চেইন দেখানো (কতবার রিডাইরেক্ট হয়েছে)
- ✅ সুন্দর UI সহ ওয়েব ইন্টারফেস
- ✅ REST API (POST ও GET উভয়ই সাপোর্ট করে)
- ✅ কোনো এক্সটার্নাল HTTP লাইব্রেরি লাগে না (Node.js বিল্ট-ইন `http`/`https` ব্যবহার)

## ইনস্টল

```bash
cd url-extractor
npm install
```

## চালানো

```bash
# প্রোডাকশন
npm start

# ডেভেলপমেন্ট (auto-reload সহ, Node.js 18+)
npm run dev
```

সার্ভার চালু হলে ব্রাউজারে যান: **http://localhost:3000**

## API ব্যবহার

### POST `/api/extract`

```bash
curl -X POST http://localhost:3000/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://bit.ly/example"}'
```

### GET `/api/extract`

```bash
curl "http://localhost:3000/api/extract?url=https://bit.ly/example"
```

### রেসপন্স ফরম্যাট

```json
{
  "success": true,
  "originalUrl": "https://bit.ly/example",
  "finalUrl": "https://www.example.com/long/path/to/page",
  "totalRedirects": 2,
  "chain": [
    "https://bit.ly/example",
    "https://bit.ly/redirect-step",
    "https://www.example.com/long/path/to/page"
  ]
}
```

## প্রজেক্ট স্ট্রাকচার

```
url-extractor/
├── package.json       # প্রজেক্ট কনফিগ ও ডিপেন্ডেন্সি
├── server.js          # Express সার্ভার ও রাউট
├── urlExtractor.js    # URL এক্সট্রাকশন লজিক
└── README.md          # ডকুমেন্টেশন
```

## কাস্টম পোর্ট

```bash
PORT=8080 npm start
```
