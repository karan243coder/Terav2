# 🚀 Deploy to Koyeb (5 minutes)

This repo is ready for [Koyeb](https://www.koyeb.com). The bot runs as a
**web service** with its **health server on port 8080** — Koyeb's healthcheck
just hits `GET /healthz`.

The bot needs **no inbound traffic** to work (it talks to Telegram via long
polling) — the public port exists only for the health check / monitoring.

---

## What the health endpoint returns

`GET https://<your-service>.koyeb.app/healthz` → HTTP 200

```json
{
  "status": "ok",
  "service": "terabox-downloader-bot",
  "version": "2.0",
  "uptime_s": 3600,
  "active_downloads": 1,
  "concurrency": 3,
  "mode": "zero-cookie (provider API)",
  "health_port": 8080
}
```

Koyeb HTTP healthcheck = any 2xx/3xx on the path → deployment stays Healthy.

---

## Option A — Koyeb CLI (fastest)

```bash
# 1) Install + login
npm install -g @koyeb/cli
koyeb login

# 2) Deploy this folder (Docker build, env vars, port, route, healthcheck)
cd terabox-bot
koyeb apps init terabot \
  --archive-builder docker \
  --archive-docker-dockerfile Dockerfile \
  --instance-type nano \
  --ports 8080 \
  --routes /:8080 \
  --checks 8080:http:/healthz \
  --env BOT_TOKEN=123456:ABC-your-token \
  --env API_ID=12345678 \
  --env API_HASH=your-hash \
  --env HEALTH_PORT=8080
  # optional (advanced modes):
  # --env TERABOX_COOKIE=ndus=...
  # --env TERABOX_API_BASE=https://your-teradl-website.example
```

Done. Koyeb shows the service going **Healthy** (healthcheck passes), and your
bot is live. Public URL: `https://terabot-xxxx.koyeb.app/healthz`.

Update later:

```bash
koyeb apps update terabot --env BOT_TOKEN=...   # change env
koyeb apps delete terabot                        # delete
```

> **Secrets:** for tokens you don't want in shell history, use
> `koyeb secrets` or Koyeb's dashboard env editor instead of `--env`.

---

## Option B — Koyeb Dashboard (GUI)

1. Create a GitHub repo with this folder (or any git repo).
2. Koyeb dashboard → **New Service** → pick the repo.
3. **Builder:** Docker → Dockerfile path: `Dockerfile`.
4. **Instance:** free `nano`.
5. **Ports & routes:** expose port **8080**, public, HTTP, path **/**.
6. **Health checks** (Settings tab): port 8080 → protocol **HTTP** → path
   **/healthz** (anything 2xx = healthy).
7. **Environment variables:** `BOT_TOKEN`, `API_ID`, `API_HASH`
   (+ optional `TERABOX_COOKIE`, `TERABOX_API_BASE`, `HEALTH_PORT`, `RATE_LIMIT_SEC`).
8. **Deploy.**

---

## Option C — GitHub Action (auto-deploy on push)

The repo includes `.github/workflows/deploy-koyeb.yml`. After pushing the repo
to GitHub:

1. Create a Koyeb API token (dashboard → Settings → Tokens).
2. GitHub repo → **Settings → Secrets and variables → Actions** → add
   `KOYEB_API_TOKEN`.
3. Push a commit → the bot auto-deploys to Koyeb (port 8080, route `/`,
   healthcheck `8080:http:/healthz`).

---

## Free-plan notes (Koyeb)

- Free plan = 1 nano instance; the bot is light (idle RAM ~100-200 MB).
- Large downloads temporarily fill the container's disk (`/bot/downloads` is
  a volume). If a download fails with no space, restart the deployment
  (Koyeb → Redeploy) — the bot auto-cleans finished files.
- The instance only **sleeps** if Koyeb's plan enforces deep-sleep on
  inactivity; a running Telegram bot rarely goes fully idle. If your free
  plan sleeps instances, keep a light ping to `/healthz` (e.g. uptime
  robot) to keep it awake.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Deployment stuck **Starting** | Healthcheck must be `HTTP /healthz` on port 8080 (not TCP only) |
| `/healthz` 404 | You opened the wrong route — route `/` → port 8080 |
| Bot not replying on Telegram | Check `BOT_TOKEN/API_ID/API_HASH` env vars; see Koyeb logs |
| `No space left on device` in logs | Redeploy (fresh disk) or raise the free-plan volume |
