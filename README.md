# RDX V-Cloud Bypass

ছোট একটি HTTP সার্ভিস, যা V-Cloud লিঙ্ক থেকে সরাসরি ডাউনলোড লিঙ্ক বের করে দেয়।

## Heroku deploy
1. রেপো ডিপ্লয় করুন (`Procfile`, `requirements.txt`, `runtime.txt` আগে থেকেই আছে)।
2. Config Vars সেট করুন:
   - `BYPARR_API` : Byparr / FlareSolverr এর URL (যেমন `https://your-solver/v1`) — **আবশ্যক**
   - `API_KEY` : ঐচ্ছিক। সেট করলে রিকোয়েস্টে `X-API-Key` হেডার লাগবে
   - `CACHE_TTL` : ঐচ্ছিক, সেকেন্ডে (ডিফল্ট 300)

## ব্যবহার
```
GET /bypass?url=<vcloud link>
GET /health
```
CLI: `python vcloud_host.py <vcloud link>`

RDX বটে `VCLOUD_API_URL` (বা `PROXY_API_URL`) এ এই সার্ভিসের URL দিন।
