"""
TeraBox resolver (no-account-first, optional ndus cookie for max reliability)
===============================================================================
2026 working flow (reverse-engineered from TeraBox's own web frontends):

  * WAP share page  :  https://{domain}/wap/share/filelist?surl={surl}
        ->  window.__INITIAL_STATE__  contains jsToken, shareInfo and fileList.
            With a valid `ndus` cookie the fileList items EMBED signed dlinks.
  * File list API   :  https://{domain}/share/list?app_id=250528&web=1&channel=dubox
                       &clienttype=5&jsToken=...&root=1&shorturl={surl}
        ->  errno 0 + clean list (works fully guest)
  * Official DL API :  https://{domain}/api/download?type=dlink&fidlist=[fs_id]
                       &sign=...&vip=2&timestamp=...  (needs ndus cookie)
        ->  sign = base64( RC4( key = sign3, data = sign1 ) )
            sign1/sign3 come from  /api/home/info
        ->  errno -6  => domain migrated, follow `Url-Domain-Prefix` header
        ->  errno 4000023 => jsToken expired, refetch and retry

Download sources (provider chain, in order):
  1) WAP-embedded dlink  (only when ndus cookie present)
  2) Official /api/download flow (ndus cookie)  -> direct CDN, full speed
  3) Dlink-provider API  (NO cookie anywhere — default no-cookie mode):
       POST {TERABOX_API_BASE}/download  {"url": <share url>}
       -> files[].proxy_url streams the ORIGINAL file through the provider's
          own authenticated session. Public default base is MeherMankar's
          free Render deployment; set TERABOX_API_BASE to self-host it.
       (Raw dlinks 403 with "user not exists" for anonymous clients — live
        verified 2026-09-20 — so in no-cookie mode bytes always come via
        proxy_url.)

Everything here is synchronous (requests) — the bot runs it in a worker thread.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

APP_ID = "250528"
CHANNEL = "dubox"

# Default dlink-provider APIs (no key, no TeraBox login needed anywhere):
#  * SECHNO_API  — real teradl website (sechno.com) ka public API. Returns
#    downloadUrl (original file, ZIP-wrapped by their worker) + streamUrl (m3u8).
#  * RENDER_API_DEFAULT — MeherMankar's free Render deployment (fallback).
# Set TERABOX_API_BASE to YOUR OWN deployed TeraDL website (website/) for
# the primary provider — the bot then downloads through your server.
SECHNO_API = "https://sechno.com/api/terabox"
RENDER_API_DEFAULT = "https://terabox-downloader-api-pqxy.onrender.com"

# Known TeraBox / TeraBox-alternative share domains
TERABOX_DOMAINS = [
    "terabox.com", "1024terabox.com", "teraboxapp.com", "terabox.app",
    "nephobox.com", "4funbox.co", "4funbox.com", "mirrobox.com",
    "momerybox.com", "teraboxlink.com", "terafileshare.com",
    "freeterabox.com", "teraboxshare.com", "terasharefile.com",
    "terabox1.com", "terabox2.com", "gibibox.com", "tboxhub.com",
    "tibibox.com", "dubbbox.com",
]

# Hosts we can actually load the WAP page from (fallback pool)
WAP_HOSTS = [
    "www.terabox.com", "www.1024terabox.com", "www.teraboxapp.com",
    "www.terasharefile.com", "www.nephobox.com", "www.mirrobox.com",
    "www.momerybox.com", "www.freeterabox.com", "www.teraboxlink.com",
    "www.terafileshare.com", "www.teraboxshare.com", "www.terabox1.com",
    "www.terabox2.com",
]


class TeraBoxError(Exception):
    """User-friendly error."""


@dataclass
class TBFile:
    fs_id: str
    name: str
    size: int            # bytes
    is_dir: bool = False
    dlink: str = ""
    thumbnail: str = ""
    path: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def size_str(self) -> str:
        n = float(self.size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
            n /= 1024
        return f"{n:.2f} PB"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_share_url(url: str) -> tuple[str, str]:
    """Return (domain, surl) for any TeraBox share URL format.

    https://terabox.com/s/1AbCd...        -> ("terabox.com", "AbCd...")
    https://1024terabox.com/sharing/link?surl=AbCd
    https://terabox.com/sharing/link?share_link=https://www.terabox.com/s/1AbCd
    """
    url = url.strip()
    # unwrap share_link=...
    q = parse_qs(urlparse(url).query)
    if "share_link" in q:
        url = q["share_link"][0]
        if not url.startswith("http"):
            url = "https://" + url
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if not host:
        raise TeraBoxError("Invalid link — yeh TeraBox link nahi lag raha.")

    domain = host[4:] if host.startswith("www.") else host
    if not any(domain == d or domain.endswith("." + d) for d in TERABOX_DOMAINS):
        raise TeraBoxError(
            f"'{domain}' supported TeraBox domain nahi hai. "
            f"Supported: terabox.com, 1024terabox.com, teraboxapp.com, "
            f"nephobox.com, mirrobox.com, 4funbox.co, momerybox.com ..."
        )

    if "/s/" in p.path:
        surl = p.path.split("/s/", 1)[-1].strip("/").split("?")[0]
    else:
        surl = parse_qs(p.query).get("surl", [""])[0]
    if not surl:
        raise TeraBoxError("Link se share id (surl) nahi nikal paaya.")
    # path form prepends '1'
    if len(surl) > 22 and surl.startswith("1"):
        surl = surl[1:]
    if len(surl) < 8:
        raise TeraBoxError(f"Share id suspicious lag raha hai: '{surl}'")
    return domain, surl


# ---------------------------------------------------------------------------
# teraboxdl.site player links (fast original-quality direct links)
# Format: https://player.teraboxdl.site/?start=<m3u8>&direct=<dl-worker>
#         &terabox_url=<share>&filename=...&size=1.22 GB&fs_id=...&poster=...
# ---------------------------------------------------------------------------

PLAYER_HOSTS = ("player.teraboxdl.site",)


def is_player_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in PLAYER_HOSTS


def parse_player_url(url: str) -> dict:
    """Extract params from a teraboxdl player link."""
    url = url.replace("&amp;", "&")
    q = parse_qs(urlparse(url).query)

    def g(k: str) -> str:
        return q.get(k, [""])[0]

    return {
        "direct": g("direct"),       # original MP4 (fast, clean)
        "stream": g("start"),        # m3u8 (TS, transcoded — sirf playback ke liye)
        "terabox_url": g("terabox_url"),
        "filename": g("filename"),
        "size": g("size"),           # formatted, e.g. "1.22 GB"
        "fs_id": g("fs_id"),
        "poster": g("poster"),
    }


def parse_size_str(s: str) -> int:
    """'1.22 GB' / '858.11 MB' -> approx bytes."""
    s = (s or "").strip().upper().replace(" ", "")
    m = re.match(r"^([\d.]+)(B|KB|MB|GB|TB)$", s)
    if not m:
        return 0
    n = float(m.group(1))
    return int(n * {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}[m.group(2)])


def _extract_state(html: str) -> dict:
    m = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*(?:;|</script>)",
        html, re.DOTALL,
    )
    if not m:
        raise TeraBoxError("WAP page parse nahi hui (TeraBox ka format badla hoga).")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise TeraBoxError(f"__INITIAL_STATE__ JSON parse error: {e}")


def _js_token(state: dict) -> str:
    raw = state.get("jsToken", "")
    if raw.startswith("function"):
        import urllib.parse
        raw = urllib.parse.unquote(raw)
    m = re.search(r'fn\("([0-9A-Fa-f]+)"\)', raw)
    if not m:
        raise TeraBoxError("jsToken challenge page mein nahi mila.")
    return m.group(1)


def rc4_sign(sign_key: str, sign_data: str) -> str:
    """sign = base64( RC4( key=sign_key repeated, data=sign_data ) )  — ported
    from the Alist/OpenList TeraBox driver (kept in sync with TeraBox 2026)."""
    v = len(sign_key)
    if not v:
        return ""
    a = [ord(sign_key[q % v]) for q in range(256)]
    p = list(range(256))
    u = 0
    for q in range(256):
        u = (u + p[q] + a[q]) % 256
        p[q], p[u] = p[u], p[q]
    out = bytearray()
    i = u = 0
    for q in range(len(sign_data)):
        i = (i + 1) % 256
        u = (u + p[i]) % 256
        p[i], p[u] = p[u], p[i]
        out.append(ord(sign_data[q]) ^ p[(p[i] + p[u]) % 256])
    return base64.b64encode(bytes(out)).decode()


def _item_to_tbfile(item: dict) -> TBFile:
    thumbs = item.get("thumbs") or {}
    return TBFile(
        fs_id=str(item.get("fs_id", "")),
        name=item.get("server_filename") or item.get("name") or item.get("path", "unknown"),
        size=int(item.get("size") or 0),
        is_dir=str(item.get("isdir", "0")) == "1",
        dlink=item.get("dlink") or "",
        thumbnail=thumbs.get("url3") or thumbs.get("url2") or thumbs.get("url1") or thumbs.get("icon") or "",
        path=item.get("path", ""),
        extra=item,
    )


@dataclass
class DownloadSource:
    """Where (and how) to stream the actual file bytes from."""
    url: str
    headers: dict
    via: str  # "wap" | "ndus" | "api"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class TeraBox:
    def __init__(self, cookie: str = "", timeout: int = 25, api_base: str | None = None):
        """cookie: raw `ndus=VALUE` or full cookie string, or bare ndus value.
        api_base: dlink-provider API base (defaults to env TERABOX_API_BASE
        or the public free Render deployment)."""
        self.timeout = timeout
        self.ndus = self._clean_cookie(cookie)
        self.s = requests.Session()
        self.base = "https://www.terabox.com"
        self._js_token = ""
        self._host_pool: list[str] = []
        custom = (api_base or os.environ.get("TERABOX_API_BASE") or "").strip()
        self.api_base = (custom or RENDER_API_DEFAULT).rstrip("/")
        self._has_own_api = bool(custom)

    # -- cookie -------------------------------------------------------------

    @staticmethod
    def _clean_cookie(cookie: str) -> str:
        c = (cookie or "").strip()
        if not c:
            return ""
        if c.lower().startswith("ndus="):
            c = c[5:]
        else:
            for part in c.split(";"):
                part = part.strip()
                if part.lower().startswith("ndus="):
                    c = part[5:]
                    break
        return c.strip()

    # -- session -------------------------------------------------------------

    def _bootstrap(self, host: str) -> None:
        """Open a session on the host so we get guest cookies (csrfToken etc)."""
        self.base = f"https://{host}"
        try:
            self.s.get(self.base + "/", headers={"User-Agent": MOBILE_UA}, timeout=self.timeout)
        except requests.RequestException:
            pass
        if self.ndus:
            for d in TERABOX_DOMAINS:
                self.s.cookies.set("ndus", self.ndus, domain="." + d)

    def _wap_page(self, surl: str) -> tuple[dict, str]:
        """Load WAP filelist page; try share domain first, then fallback hosts."""
        candidates = []
        if self.base:
            host = urlparse(self.base).hostname or ""
            if host:
                candidates.append(host)
        for h in WAP_HOSTS:
            if h not in candidates:
                candidates.append(h)

        last_err = None
        for host in candidates:
            url = f"https://{host}/wap/share/filelist?surl={surl}"
            try:
                r = self.s.get(
                    url,
                    headers={"User-Agent": MOBILE_UA, "Accept": "text/html,*/*"},
                    allow_redirects=True,
                    timeout=self.timeout,
                )
                if r.status_code == 200 and "__INITIAL_STATE__" in r.text:
                    self._host_pool = [host] + [h for h in self._host_pool if h != host]
                    self.base = f"https://{host}"
                    return _extract_state(r.text), host
                last_err = f"{host} -> HTTP {r.status_code}"
            except requests.RequestException as e:
                last_err = f"{host} -> {type(e).__name__}"
        raise TeraBoxError(f"WAP page load fail (tried {len(candidates)} hosts). Last: {last_err}")

    # -- api plumbing ----------------------------------------------------------

    def _api_get(self, path: str, params: dict, referer: str | None = None):
        """GET an API endpoint with domain-migration (-6) + jsToken refresh (4000023)."""
        cur_params = dict(params)
        for _ in range(6):
            url = self.base + path
            r = self.s.get(
                url,
                params=cur_params,
                headers={
                    "User-Agent": MOBILE_UA,
                    "Accept": "application/json, text/plain, */*",
                    "Referer": referer or self.base + "/",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=self.timeout,
                allow_redirects=False,
            )
            try:
                data = r.json()
            except ValueError:
                raise TeraBoxError(f"API {path} ne JSON nahi diya (HTTP {r.status_code}).")
            errno = data.get("errno")
            if errno == -6:
                prefix = r.headers.get("Url-Domain-Prefix")
                if prefix:
                    self.base = f"https://{prefix}.terabox.com"
                    continue
                # no prefix (guest on migrated domain) -> try dm. host once
                if "dm." not in self.base:
                    self.base = "https://dm.terabox.com"
                    continue
            if errno == 4000023 and self._js_token:
                # stale jsToken -> refetch from WAP page and retry once
                state, _ = self._wap_page(cur_params.get("surl", ""))
                self._js_token = _js_token(state)
                cur_params["jsToken"] = self._js_token
                continue
            return data
        raise TeraBoxError("API domain migrate hote hote bhi fail ho raha hai.")

    # -- public API ------------------------------------------------------------

    def get_share_info(self, url: str) -> list[TBFile]:
        """Resolve a share URL into a file list (guest works)."""
        domain, surl = parse_share_url(url)
        host = f"www.{domain}" if not domain.startswith("www.") else domain
        self._host_pool = [host]
        self._bootstrap(host)

        state, _ = self._wap_page(surl)
        self._js_token = _js_token(state)
        share = state.get("share", {})
        share_info = share.get("shareInfo") or {}
        if share_info.get("pwd"):
            raise TeraBoxError("Yeh share password-protected hai — password support abhi nahi hai.")
        if share_info.get("expiredtype") == 2:
            raise TeraBoxError("Share link expire ho chuka hai.")

        files: list[TBFile] = []
        for item in share.get("fileList") or []:
            files.append(_item_to_tbfile(item))

        # WAP state may not contain dlink — enrich with /share/list (guest OK)
        try:
            lst = self._api_get(
                "/share/list",
                {
                    "app_id": APP_ID, "web": "1", "channel": CHANNEL,
                    "clienttype": "5", "jsToken": self._js_token,
                    "root": "1", "shorturl": surl,
                },
                referer=f"{self.base}/wap/share/filelist?surl={surl}",
            )
            if lst.get("errno") == 0 and lst.get("list"):
                clean = [_item_to_tbfile(i) for i in lst["list"]]
                if len(files) == 0:
                    files = clean
                else:
                    by_id = {f.fs_id: f for f in files}
                    for c in clean:
                        if c.fs_id in by_id:
                            f = by_id[c.fs_id]
                            if not f.dlink and c.dlink:
                                f.dlink = c.dlink
                            f.size = c.size or f.size
                            f.thumbnail = c.thumbnail or f.thumbnail
            elif lst.get("errno") not in (0,):
                # 101/102 => password, 4 => share not found ...
                em = {4: "Share not found / expired.", 101: "Share password-protected hai."}
                if lst.get("errno") in em and not files:
                    raise TeraBoxError(em[lst["errno"]])
        except TeraBoxError:
            if not files:
                raise

        if not files:
            raise TeraBoxError("Share mein koi file nahi mili (khali share?).")
        return files

    # -- dlink providers (no-cookie mode) --------------------------------------

    def _api_post(self, url: str, payload: dict, timeout: int = 150) -> dict:
        """POST JSON to a dlink-provider API. Retries once on transient errors."""
        last_err = "unknown"
        for attempt in (1, 2):
            try:
                r = self.s.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json", "User-Agent": DESKTOP_UA},
                    timeout=timeout,
                )
                if r.status_code == 429:
                    last_err = "rate-limit (429)"
                    time.sleep(5 * attempt)
                    continue
                body = r.json()
                if isinstance(body, dict) and body.get("error"):
                    raise TeraBoxError(str(body["error"])[:160])
                return body if isinstance(body, dict) else {}
            except TeraBoxError:
                raise
            except ValueError:
                last_err = "invalid JSON"
                time.sleep(3 * attempt)
            except requests.RequestException as e:
                last_err = type(e).__name__
                time.sleep(3 * attempt)
        raise TeraBoxError(f"provider API fail ({last_err})")

    @staticmethod
    def _pick_file(files: list, fs_id: str, name_hint: str) -> dict | None:
        """Pick the right file from a provider response (fs_id, then name, then single)."""
        for it in files:
            f = str(it.get("fs_id") or it.get("fsId") or "").strip()
            if f and f != "0" and f == str(fs_id).strip():
                return it
        n = (name_hint or "").strip().lower()
        if n:
            for it in files:
                if str(it.get("filename") or it.get("name") or "").strip().lower() == n:
                    return it
        if len(files) == 1:
            return files[0]
        return None

    def _sechno_source(self, url: str, fs_id: str, name_hint: str) -> DownloadSource:
        """sechno.com — real teradl website ka public API (no key, no cookie)."""
        data = self._api_post(SECHNO_API, {"url": url})
        files = data.get("files") or []
        pick = self._pick_file(files, fs_id, name_hint)
        if not pick or not pick.get("downloadUrl"):
            raise TeraBoxError("sechno provider ne file/dlink nahi di")
        return DownloadSource(str(pick["downloadUrl"]), {}, "sechno")

    def _own_site_source(self, url: str, fs_id: str, name_hint: str) -> DownloadSource:
        """Apna TeraDL website (TERABOX_API_BASE) — bot downloads through it."""
        data = self._api_post(
            self.api_base.rstrip("/") + "/api/download",
            {"url": url, "fsId": str(fs_id), "name": name_hint},
        )
        f = data.get("file") or (data if data.get("download_url") else {})
        dl = f.get("download_url") or f.get("proxy_url")
        if not dl:
            raise TeraBoxError(f"apna website ne download_url nahi di: {str(data)[:120]}")
        return DownloadSource(str(dl), {}, "own-site")

    def _render_source(self, url: str, fs_id: str, name_hint: str) -> DownloadSource:
        """MeherMankar's free Render API (last-resort fallback)."""
        data = self._api_post(RENDER_API_DEFAULT + "/download", {"url": url})
        d = data.get("data") or {}
        files = d.get("files") or []
        pick = self._pick_file(files, fs_id, name_hint)
        if not pick or not pick.get("proxy_url"):
            raise TeraBoxError(f"render provider ne source nahi di: {d.get('note') or 'no files'}")
        return DownloadSource(str(pick["proxy_url"]), {}, "render")

    def resolve_download_source(self, url: str, fs_id: str, name_hint: str = "") -> DownloadSource:
        """Provider chain for the actual file bytes:

        0) teraboxdl player link         (fast original — user paste kare)
        1) WAP-embedded dlink            (ndus cookie only)
        2) official /api/download RC4    (ndus cookie only)
        3) dlink-provider chain          (NO cookie — default mode)
        """
        # 0) player.teraboxdl.site link = already-resolved fast original link
        if is_player_url(url):
            d = parse_player_url(url)
            if d.get("direct"):
                return DownloadSource(d["direct"], {}, "player-direct")
            raise TeraBoxError("Player link mein 'direct' URL nahi mila — poora player link paste karo.")

        domain, surl = parse_share_url(url)
        host = f"www.{domain}" if not domain.startswith("www.") else domain
        self._host_pool = [host]
        self._bootstrap(host)

        if self.ndus:
            # 1) WAP state may already embed signed dlinks
            try:
                state, _ = self._wap_page(surl)
                self._js_token = _js_token(state)
                for item in (state.get("share", {}) or {}).get("fileList") or []:
                    if str(item.get("fs_id")) == str(fs_id) and item.get("dlink"):
                        return DownloadSource(str(item["dlink"]), {"Cookie": f"ndus={self.ndus}"}, "wap")
            except TeraBoxError:
                pass

            # 2) official flow
            if not self._js_token:
                try:
                    state, _ = self._wap_page(surl)
                    self._js_token = _js_token(state)
                except TeraBoxError:
                    pass
            common = {
                "app_id": APP_ID, "web": "1", "channel": CHANNEL,
                "clienttype": "0", "jsToken": self._js_token,
            }
            info = self._api_get("/api/home/info", dict(common))
            if info.get("errno") == 0 and "data" in info:
                sign = rc4_sign(info["data"]["sign3"], info["data"]["sign1"])
                dl = self._api_get(
                    "/api/download",
                    {
                        **common,
                        "type": "dlink",
                        "fidlist": f"[{fs_id}]",
                        "sign": sign,
                        "vip": "2",
                        "timestamp": str(int(time.time())),
                    },
                )
                dlinks = (dl or {}).get("dlink") or []
                if dl.get("errno") == 0 and dlinks and dlinks[0].get("dlink"):
                    return DownloadSource(str(dlinks[0]["dlink"]), {"Cookie": f"ndus={self.ndus}"}, "ndus")
                raise TeraBoxError(
                    f"ndus flow se dlink nahi mila (errno={dl.get('errno')}). "
                    "Cookie expire hua hoga — nayi ndus daalo ya API mode pe jao."
                )
            raise TeraBoxError(
                f"/api/home/info fail (errno={info.get('errno')}). "
                "ndus cookie expire hua hoga — nayi cookie daalo ya API mode use karo."
            )

        # 3) no cookie anywhere -> dlink-provider chain (koi key bhi nahi):
        #    apna website (TERABOX_API_BASE) -> sechno (real site) -> render (free)
        errors: list[str] = []
        candidates: list[tuple[str, callable]] = []
        if self._has_own_api:
            candidates.append(("apna website", self._own_site_source))
        candidates.append(("sechno (real site API)", self._sechno_source))
        if RENDER_API_DEFAULT not in self.api_base:
            candidates.append(("render (free)", self._render_source))
        for label, fn in candidates:
            try:
                return fn(url, fs_id, name_hint)
            except TeraBoxError as e:
                errors.append(f"{label}: {str(e)[:80]}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{label}: {type(e).__name__}")
        raise TeraBoxError(
            "Saare dlink-providers fail ho gaye — thodi der baad try karo.\n" + " | ".join(errors[:3])
        )

    def get_dlink(self, url: str, fs_id: str) -> str:
        """Backward-compatible: return the URL to stream bytes from."""
        return self.resolve_download_source(url, fs_id).url

    @staticmethod
    def _unwrap_zip(dest: str) -> None:
        """Some providers wrap the original file in a stored-ZIP. If `dest` is a
        one-file stored ZIP, replace it with the unzipped original in-place."""
        import zipfile
        try:
            with open(dest, "rb") as f:
                if f.read(4) != b"PK\x03\x04":
                    return
        except OSError:
            return
        if not zipfile.is_zipfile(dest):
            return
        with zipfile.ZipFile(dest) as z:
            entries = [n for n in z.namelist() if not n.endswith("/")]
            if len(entries) != 1:
                return  # not our single-file wrapper — leave as is
            tmp = dest + ".unz"
            with z.open(entries[0]) as src, open(tmp, "wb") as out:
                while True:
                    b = src.read(1024 * 1024)
                    if not b:
                        break
                    out.write(b)
            os.replace(tmp, dest)

    def download(
        self,
        source: str,
        dest: str,
        headers: dict | None = None,
        progress_cb=None,
        cancel_flag: list | None = None,
        resume: bool = True,
    ) -> int:
        """Stream `source` (dlink or provider proxy_url) to `dest`.
        Returns total bytes in `dest`. progress_cb(done, total).
        resume=True: agar `dest` mein partial file hai to Range se wahi se continue."""
        hdrs = {
            "User-Agent": DESKTOP_UA,
            "Accept": "*/*",
        }
        if self.base:
            hdrs["Referer"] = self.base + "/"
        if self.ndus and "Cookie" not in hdrs:
            hdrs["Cookie"] = f"ndus={self.ndus}"
        hdrs.update(headers or {})

        existing = 0
        if resume:
            try:
                existing = os.path.getsize(dest)
            except OSError:
                existing = 0
        if existing > 0:
            hdrs["Range"] = f"bytes={existing}-"
        written = existing
        fmode = "ab" if existing > 0 else "wb"
        with self.s.get(
            source, headers=hdrs, stream=True, timeout=self.timeout, allow_redirects=True
        ) as r:
            if r.status_code == 416:
                raise TeraBoxError("Resume fail — partial file size mismatch, naya try karo.")
            if r.status_code not in (200, 206):
                body = ""
                try:
                    body = r.content[:200].decode("utf-8", "replace")
                except Exception:
                    pass
                raise TeraBoxError(
                    f"Download HTTP {r.status_code} — link expire hua, phir try karo. {body[:160]}"
                )
            if r.status_code == 200 and existing > 0:
                # server ne Range ignore kiya — fresh start
                fmode, written = "wb", 0
            total = int(r.headers.get("Content-Length") or 0)
            if r.status_code == 206:
                total = existing + total
            with open(dest, fmode) as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if cancel_flag is not None and cancel_flag[0]:
                        raise TeraBoxError("Cancelled")
                    if not chunk:
                        continue
                    f.write(chunk)
                    written += len(chunk)
                    if progress_cb:
                        progress_cb(written, total)
        # provider (sechno) original file ko stored-ZIP mein wrap karta hai
        # — unwrap karke asli original file wahi dest mein chhod do
        self._unwrap_zip(dest)
        return os.path.getsize(dest)


# ---------------------------------------------------------------------------
# CLI:  python terabox.py <share_url>
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TB_TEST_URL", "")
    if not url:
        print("Usage: python terabox.py <terabox-share-url>")
        sys.exit(1)
    tb = TeraBox(cookie=os.environ.get("TERABOX_COOKIE", ""))
    print(f"Resolving {url} ...")
    files = tb.get_share_info(url)
    print(f"Files: {len(files)}")
    for i, f in enumerate(files, 1):
        print(f"  [{i}] {'📁' if f.is_dir else '🎬'} {f.name}  ({f.size_str})  fs_id={f.fs_id}  dlink={'yes' if f.dlink else 'no'}")
    non_dir = [f for f in files if not f.is_dir]
    if non_dir:
        print("\nFetching download source for first file ...")
        src = tb.resolve_download_source(url, non_dir[0].fs_id)
        print(f"via:   {src.via}  (ndus cookie: {'haan' if tb.ndus else 'NAHI — provider API mode'})")
        print("source:", src.url[:150] + ("..." if len(src.url) > 150 else ""))
