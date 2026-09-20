# 📥 TeraBox Downloader Telegram Bot

**Koi bhi TeraBox link bhejo → bot original FULL QUALITY mein file download karke wahi chat mein bhej deta hai.**

End-user (jo link bhejta hai) ko **bina ID, login, cookies — kuch nahi dena hai.** Bas link paste karo, file mil jaati hai. 🎉

**Aur ab TeraBox cookie bhi nahi chahiye!** 🚀 Bot ke andar 3 dlink-modes hain — default mode **100% zero-cookie** hai (neecheh samjha hai).

---

## ✨ Features

- 🎬 **100% original quality** — file *document* ke roop mein bhejta hai (koi re-encoding/compress nahi)
- 🌐 **Saare TeraBox alternative domains** supported: `terabox.com`, `1024terabox.com`, `teraboxapp.com`, `nephobox.com`, `mirrobox.com`, `4funbox.co`, `momerybox.com`, `teraboxlink.com`, `terafileshare.com`, `freeterabox.com`, `teraboxshare.com` ...
- 📁 **Multi-file share** — inline buttons se file select karo
- 📊 **Advanced live progress (English)** — download + upload dono ke liye: 20-segment bar, exact %, size, speed, **ETA + elapsed time** — real-time sync
- ✅ **Final summary** — download time, upload time, total time, speeds
- 🛡️ **FloodWait control** — har Telegram API call auto-retry with platform sleep; bot kabhi hang/crash nahi karta
- ❤️ **Health server (port 8080)** — `GET /healthz` JSON (Koyeb/VPS monitoring ke liye)
- 🚀 **Koyeb-ready** — 5-min deploy (`DEPLOY-KOYEB.md` + GitHub Action included)
- 🔒 **Private chat only** + optional allowlist
- ⚡ Concurrency, rate-limit, auto-cleanup (server par junk files nahi rehti)
- 🏷️ Files > 2GB (Telegram bot limit) ke liye **direct download link** bhejta hai
- 🔑 **4 dlink modes** — zero-cookie (default), apna TeraDL website, player-link turbo, ndus direct
- 🔄 2026 TeraBox API changes handled (domain migration, jsToken refresh, RC4 sign)

---

## 🧠 Yeh kaise kaam karta hai (2026 reality)

2026 mein TeraBox ka pura public download API band hai. Humne live test karke prove kiya hai (20 Sep 2026):

| Cheez | Status |
|---|---|
| File list (name, size, fs_id) | ✅ **Guest** bhi chalta hai — bina kisi cookie ke |
| Direct dlink (CDN se bytes) | ❌ **Bina login-user cookie ke 403** — CDN khud bolta hai: `error_code 31045, "user not exists"` (referer/UA/sab tricks try ki, fail) |
| **Dlink-provider API** (jiski apni logged-in account pool hai) | ✅ **Zero-cookie se dlink/proxy milta hai** — bot isko use karta hai |

Isliye bot ka **download-source chain** aisa hai (automatically, is order mein):

0. **Link `player.teraboxdl.site` ka hai?** → teraboxdl player link ke andar hi signed direct URL hota hai → **~2 MB/s original MP4** (bonus fast route, neeche)
1. **ndus cookie set hai?** → WAP-embedded dlink / official RC4 flow → **direct CDN, full speed**
2. **Nahi?** → **Apna TeraDL website** (agar `TERABOX_API_BASE` set hai) → `POST /api/download` → clean original bytes stream
3. **Phir bhi nahi?** → **sechno.com** (real TeraDL site) ka public API → original file (provider ZIP automatically unwrap) → last resort: free Render API

---

## 🔑 Dlink Modes (3 options — .env se choose karo)

### Mode 1 — ZERO-COOKIE (default, koi setup nahi) ⭐

`.env` mein **kuch mat daalo** — bot **sechno.com** (ek real TeraDL website) ke public API
se original file leta hai.

- ✅ Tera TeraBox account nahi chahiye, kisi ka bhi cookie nahi chahiye, koi API key nahi
- ✅ Original full quality, saare domains (provider ZIP wrapper bot automatically hata deta hai)
- ⚠️ Speed provider ki apni hai: sandbox test mein **~87–250 KB/s** (1.22 GB ≈ 2–4 ghante).
  Production ke liye Mode 2 dekho.
- ⚠️ Third-party site par depend hai (bot automatically fallback try karta hai agar fail ho)

### Mode 2 — APNA TERADL WEBSITE (recommended for production) 🚀

Is repo ke andar hi ek poora **TeraDL website** hai (`website/` folder) — link paste karo →
video **online play** + **original download**. Usi website ka **API** bot directly use
karta hai (`TERABOX_API_BASE` set karte hi bot automatically uske through download karega).

**Deploy (free Render / Railway / apni VPS — 5 min):**

*Render (easiest):*
1. [Render.com](https://render.com) → **New → Web Service** → apni repo connect karo
2. Build: `pip install -r website/requirements.txt` (ya Dockerfile: `website/Dockerfile`,
   context = repo root)
3. Start: `uvicorn app:app --host 0.0.0.0 --port $PORT` (working dir = `website/`)
4. Deploy → public URL milega, jaise `https://teradl.onrender.com`
5. Bot ke `.env` mein: `TERABOX_API_BASE=https://teradl.onrender.com` ✅

*Docker (VPS):*
```bash
docker build -f website/Dockerfile -t teradl .   # context = terabox-bot/
docker run -d -p 8000:8000 teradl
```

> Website ka API contract: `POST /api/resolve {url}` → files list;
> `POST /api/download {url, fsId, name}` → `{file:{download_url, stream_url, size,...}}`;
> `GET /proxy?url=...&name=...&size=...` → clean original bytes stream (ZIP auto-removed).
> Pura details neeche: **"🌐 Apna TeraDL website"** section.

### Mode 3 — NDUS DIRECT (fastest, no proxy hop)

Bot khud TeraBox se direct baat karega. Bot ke `.env` mein:
`TERABOX_COOKIE=ndus=VALUE`
(value kaise leni hai: neeche Step 3B). Proxy hop hi nahi — full CDN speed.

### Bonus — ⚡ TeraBoxDL PLAYER LINK (turbo route, no setup)

TeraBoxDL ki player page se jo **player link** milta hai
(`https://player.teraboxdl.site/...?start=...&direct=...`), use **seedha bot mein paste**
karo — bot link ke andar embedded signed **direct MP4 URL** parse karke **~2 MB/s** mein
original file download karta hai (sandbox-mein measured: 1.22 GB ≈ 11 min).

- ✅ Koi cookie nahi, koi setup nahi — sirf link paste karo
- ✅ Original MP4 (clean `ftyp` header verified)
- ⚠️ Link signed hai — kafi der valid rehta hai par kabhi-kabhi expire/refresh karna padta hai
- ⚠️ Player link ke bina normal share link ke saath Mode 1/2/3 hi chalega

---

## 🌐 Apna TeraDL website (`website/`) — detail

Ye ek chhota **FastAPI** app hai jo 3 cheezein deta hai:

1. **UI** (`GET /`) — Hinglish dark UI: link paste karo → file list →
   **▶️ Play** (HLS stream, hls.js) ya **⬇️ Download** (original file)
2. **API** — bot + koi bhi client use kar sakta hai:
   ```
   POST /api/resolve   {"url": "https://1024terabox.com/s/1..."}
   → {"title": "...", "files": [{filename, size, size_formatted, fsId, thumbUrl, ...}]}

   POST /api/download  {"url": "https://1024terabox.com/s/1...", "fsId": "585060...", "name": "x.mp4"}
   → {"file": {filename, size, size_formatted,
               download_url,   ← apna streaming /proxy (CLEAN original file)
               stream_url,     ← HLS m3u8 (online play)
               provider_download_url}}

   GET  /proxy?url=<provider-url>&name=x.mp4&size=<bytes>
   → original file bytes — provider ka stored-ZIP wrapper STREAMING mein hi
     on-the-fly hata deta hai (pehla byte turant, disk par pura file nahi aata)
   ```
3. **Guest-only resolve** — bina kisi cookie ke file list (TeraBox share API),
   phir provider chain (sechno) se download source.

**Deploy options:** Render free tier (sabse aasan) • Railway • koi bhi 512MB VPS
(Dockerfile ready hai: `website/Dockerfile`). Bot integration = bas
`TERABOX_API_BASE=<website ka public URL>` daalo — baat automatically ho jaati hai.

---

## 🚀 Setup (5-10 min)

### Step 1 — Telegram Bot Token

1. Telegram mein **[@BotFather](https://t.me/BotFather)** kholo
2. `/newbot` bhejo → name + username do
3. Jo **token** mile (jaise `123456:ABC-DEF...`) copy karo

### Step 2 — Telegram API ID / Hash

1. **[my.telegram.org](https://my.telegram.org)** pe apne number se login karo
2. **API development tools** → app banao (koi bhi naam)
3. **`api_id`** (number) aur **`api_hash`** (string) copy karo

### Step 3 — Download Mode choose karo

- **A) Zero-cookie chahiye (easy):** kuch mat karo — Mode 1 (sechno) automatically on hai. Bot chalega. 🎉
- **B) Fast + apna site chahiye (production, recommended):** Mode 2 — apna TeraDL website
  deploy karo (upar steps) → `TERABOX_API_BASE` daalo. **Recommended.**
- **C) Turbo test chahiye:** TeraBoxDL player link paste karke bhejo (Bonus section).
- **C) Seedha CDN chahiye:** Mode 3 — ndus cookie:
  1. Browser mein [terabox.com](https://www.terabox.com) kholo
  2. **Free account banao** (email ya Google se — 1 min)
  3. **F12** → **Application** → **Cookies** → domain ke under
  4. **`ndus`** ki value copy karo → `.env` mein `TERABOX_COOKIE=ndus=YAHAN_PASTE`

> Cookie/API test karne ke liye:
> ```bash
> python test_cookie.py "https://www.terabox.com/s/1AbCdEfG..."   # Mode 3 verify
> python terabox.py "https://www.terabox.com/s/1AbCdEfG..."       # full flow (listing + download source)
> ```

### Step 4 — Code Chalao

```bash
cd terabox-bot
cp .env.example .env
# .env mein apne values daalo (BOT_TOKEN, API_ID, API_HASH, + Mode 2/3 wali vars)
bash run.sh
```

Bot live! Ab Telegram mein apne bot ko link bhejo. 🎉

---

## 🐳 Docker se chalana (VPS par best)

```bash
cp .env.example .env   # values daalo
docker compose up -d --build
docker compose logs -f
```

## 🚀 Koyeb par deploy (FREE, 5 min)

Poora step-by-step: **[`DEPLOY-KOYEB.md`](DEPLOY-KOYEB.md)**. Summary:

- Koyeb par **web service** deploy hota hai, **port 8080 public** (health ke liye)
- Healthcheck: **HTTP → port 8080 → path `/healthz`** (bot khud JSON return karta hai)
- 3 tareeke: **CLI** (`koyeb apps init terabot ... --checks 8080:http:/healthz`),
  **Dashboard (GUI)**, ya **GitHub Action** (repo mein `.github/workflows/deploy-koyeb.yml` ready hai)
- Env vars: `BOT_TOKEN`, `API_ID`, `API_HASH` (+ optional `TERABOX_COOKIE`, `TERABOX_API_BASE`)

Health endpoint: `https://<service>.koyeb.app/healthz`
→ `{"status":"ok","uptime_s":...,"active_downloads":...,"mode":"zero-cookie (provider API)"}`

## 🖥️ VPS par Systemd se (Docker ke bina)

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# service file setup karo (apna path edit karna)
sudo cp terabox-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now terabox-bot
journalctl -u terabox-bot -f   # logs dekhne ke liye
```

**Sasta VPS kaafi hai** (1GB RAM, 500MB+/mo) — bot khud file download karke forward karta hai.
Mode 2 + Mode 3 ke saath VPS par speed full milti hai.

---

## 💬 Bot Commands

| Command | Kaam |
|---|---|
| `/start` ya `/help` | Help message |
| `/ping` | Bot responsive hai ya nahi |
| `/id` | Tera Telegram ID (allowlist ke liye) |
| `/stats` | Sirf owner — uptime, **dlink mode**, cache |
| *(koi bhi TeraBox link)* | Download shuru |

## ⚙️ Config Options (.env)

| Var | Default | Matlab |
|---|---|---|
| `BOT_TOKEN` | — | BotFather ka token |
| `API_ID` / `API_HASH` | — | my.telegram.org ke values |
| `TERABOX_API_BASE` | khali | Apna TeraDL website ka public URL (Mode 2 — set karte hi bot usi se download karega) |
| `TERABOX_COOKIE` | khali | `ndus=...` (Mode 3 — sabse fast) |
| `OWNER_ID` | khali | Tera Telegram ID (owner commands) |
| `ALLOWED_USERS` | khali = sab | Comma-separated IDs (private bot ke liye) |
| `MAX_FILE_GB` | `2.0` | Telegram bot limit; usse bade files ka direct link bhejta hai |
| `RATE_LIMIT_SEC` | `60` | Har user ke 2 requests ka gap (ban se bachav) |
| `CONCURRENCY` | `3` | Ek saath kitne downloads |
| `WORK_DIR` | `downloads` | Temp download folder (auto-cleanup) |
| `HEALTH_PORT` | `8080` | Health server port (`GET /healthz` — Koyeb/VPS) |

> Priority: player link (Bonus) > `TERABOX_COOKIE` (Mode 3) > `TERABOX_API_BASE` (Mode 2) > sechno (Mode 1) > Render (last resort)

---

##  Troubleshooting

| Problem | Solution |
|---|---|
| Download bahut slow (Mode 1) | Provider ki speed hai (~100–250 KB/s). Mode 2 (apna TeraDL website) deploy karo, ya player link use karo |
| `Dlink provider fail (rate-limit/congested)` | Thodi der baad try karo; baar-baar ho to Mode 2 |
| Provider API down hai (5xx/timeout) | Bot 2x retry karta hai; phir bhi fail → Mode 2/3 use karo |
| `/api/home/info fail` / `cookie expire` (Mode 3) | Nayi `ndus` copy karke `.env` update karo, bot restart karo |
| `Share not found / expired` | Link expire ho chuka hai — naya link lo |
| `Share password-protected` | Abhi password-wale shares support nahi (aage add ho sakta hai) |
| Upload fail badi files par | VPS ka internet speed check karo; bot 2GB tak bhej sakta hai |
| Bot koi reply nahi deta | `journalctl -u terabox-bot -f` ya docker logs dekho; rate limit (60s) yaad rakho |
| `FloodWait` errors | `RATE_LIMIT_SEC` badha do (120-180) |

## 📌 Notes & Limits

- Telegram bot **max 2GB** file bhej sakta hai (Bot API limit) — usse badi files ka **direct link** bhejta hai (browser/IDM se full quality download)
- Provider API se aane wale links **~8 ghante** valid rehte hain (dlink `expires=8h` hai) — file turant download karo
- **Mode 1 honesty:** sechno ki speed is datacenter-sandbox se ~87–250 KB/s measure hui; tera final speed tera server + provider dono par depend karega. 1.22 GB real-file test: player-link route pe ~2 MB/s (11 min)
- TeraBox apna private API baar-baar badalta hai; listing (guest) bahut stable hai, dlink-provider baar-baar verify karte raho
- **Responsible use** — sirf wohi files download karo jinke tumhe rights hain / jo publicly share ki gayi hain

## 📄 Files

```
terabox-bot/
├── bot.py               # Telegram bot (Pyrogram) — share + player dono links handle karta hai
├── terabox.py           # TeraBox resolver (2026 flow, all domains, player/own-site/sechno/render chain)
├── test_cookie.py       # ndus cookie verify karne ka CLI (Mode 3)
├── run.sh               # Local run helper
├── requirements.txt
├── .env.example         # Config template
├── Dockerfile           # EXPOSE 8080 (health) — Koyeb/VPS ready
├── docker-compose.yml
├── terabox-bot.service  # systemd (VPS)
├── DEPLOY-KOYEB.md      # 🚀 Koyeb deploy guide (CLI + GUI + GitHub Action)
├── .github/workflows/deploy-koyeb.yml  # auto-deploy on push
└── website/             # 🌐 Apna TeraDL website (FastAPI) — UI + API + streaming proxy
    ├── app.py           #   GET /, POST /api/resolve, POST /api/download, GET /proxy
    ├── index.html       #   Hinglish dark UI (play + download)
    ├── requirements.txt
    └── Dockerfile
```

**Credits:** dlink-provider architecture — MeherMankar/terabox-downloader-api (open source, MIT);
default no-cookie provider — sechno.com; turbo route — TeraBoxDL player links

**License:** MIT — apna banao, apna chalao. 🚀
