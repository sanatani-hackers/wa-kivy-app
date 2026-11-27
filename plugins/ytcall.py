"""ytcall — True Live YouTube Audio PCMStream & Video DJ Plugin for WA Core Calls.

Streams YouTube audio directly in real time (zero full-file download) via
Android MediaExtractor+MediaCodec over loopback TCP into Go meowcaller's
PCMStream 60ms ring buffer, powered by an embedded pure-Python InnerTube client
(sanatani-hackers/ytstream). Video calls fall back cleanly to <=480p 15fps MP4.

Commands (prefix !yt or !ytcall):
  !yt help                           Show this guide
  !yt <url|id|query|#N>              Stream live in active call, or search tracks
  !yt search <query>                 Search YouTube & pick results with #1..#5
  !yt play <url|id|query|#N>         Stream/switch YouTube audio live in call
  !yt dial <num|me|here> <song|url>  Dial number & start live YT stream on pickup
  !yt playcall <song|url|#N>         Call this chat & stream YT audio on pickup
  !yt vcall <num|me|here> <vid|url>  Video call with YouTube MP4 (H.264 + audio)
  !yt queue <url|id|query|#N>        Add YT track to live call DJ queue
  !yt q                              View active call's YT queue
  !yt skip                           Skip to next YT track in queue
  !yt save <url|id|query|#N>         Download & save permanently to !call tracks
  !yt info <url|id|query|#N>         Show metadata & stream details without playing
  !yt pause / resume / stop / hangup Quick call & playback controls
  !yt status / clearcache            Status & cache management
  !yt mode <stream|cache>            stream = zero-download live PCM, cache = WAV
  !yt loop <on|off>                  Loop single-track playback (default OFF)
  !yt quality <hq|fast>              Audio bitrate mode (hq=130kbps, fast=~50kbps)
  !yt maxmins <N>                    Max track length in minutes (default 60, 0=off)
  !yt public <on|off>                Allow non-owners to queue/play YT tracks
"""
import asyncio
import http.client
import json
import logging
import os
import re
import shutil
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

NAME = "ytcall"
log = logging.getLogger("plugin.ytcall")
PREFIXES = ("!yt", "!ytcall")

DEFAULT_CFG = {
    "stream_mode": True,
    "loop": False,
    "public_dj": False,
    "max_minutes": 60,
    "video_max_minutes": 10,
    "max_cache_mb": 150,
    "max_kbps": 160,
    "owners": [],
}

_cfg: Dict[str, Any] = dict(DEFAULT_CFG)
_ctx = None
_yt_client = None
_PENDING_PLAY: Dict[str, dict] = {}
_YT_QUEUES: Dict[str, List[dict]] = {}
_ACTIVE_LIVE_TRACK: Dict[str, dict] = {}
_LAST_SEARCH: Dict[str, List[dict]] = {}
_BG_TASKS: set = set()
_CACHE_LOCK = threading.Lock()
_DL_LOCKS: Dict[str, threading.Lock] = {}
_DL_LOCKS_GUARD = threading.Lock()


def _spawn(coro) -> asyncio.Task:
    """Run coroutine outside PluginSupervisor's 20s handler timeout window."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def _vid_lock(vid: str) -> threading.Lock:
    with _DL_LOCKS_GUARD:
        lk = _DL_LOCKS.get(vid)
        if lk is None:
            lk = threading.Lock()
            _DL_LOCKS[vid] = lk
        return lk


# =============================================================================
# Embedded pure-Python YouTube InnerTube client (sanatani-hackers/ytstream)
# =============================================================================

_PLAYER_URL = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
_SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
_VISITOR_URL = "https://www.youtube.com/youtubei/v1/visitor_id?prettyPrint=false"
_MIB = 1024 * 1024

_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_URL_ID_RE = re.compile(
    r"(?:v=|/v/|/embed/|/shorts/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})"
)
_EXT_BY_MIME = {
    "audio/mp4": "m4a",
    "video/mp4": "mp4",
    "audio/webm": "webm",
    "video/webm": "webm",
    "audio/3gpp": "3gp",
    "video/3gpp": "3gp",
}

_SAFARI_VISION_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/26.0 Safari/605.1.15"
)
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
_WEB_VISITOR_CONTEXT = {
    "clientName": "WEB",
    "clientVersion": "2.20260708.00.00",
    "hl": "en",
    "gl": "US",
}


class YTError(Exception):
    """Base error for YouTube extraction, search, and download."""


class _StreamRefused(YTError):
    def __init__(self, message: str, status: Optional[int] = None, client: Optional[str] = None):
        self.status = status
        self.client = client
        super().__init__(message)


class _SkipClient(Exception):
    pass


class _ClientCfg:
    def __init__(self, name: str, client_id: str, user_agent: str, context: Dict[str, object],
                 extra_headers: Optional[Dict[str, str]] = None, range_bytes: int = _MIB):
        self.name = name
        self.client_id = client_id
        self.user_agent = user_agent
        self.context = context
        self.extra_headers = dict(extra_headers or {})
        self.range_bytes = range_bytes

    @property
    def version(self) -> str:
        return str(self.context.get("clientVersion", ""))

    def media_headers(self) -> Dict[str, str]:
        headers = {"User-Agent": self.user_agent}
        for k in ("Origin", "Referer"):
            if k in self.extra_headers:
                headers[k] = self.extra_headers[k]
        return headers


_CLIENTS: Dict[str, _ClientCfg] = {
    c.name: c
    for c in (
        _ClientCfg(
            "VISIONOS",
            "101",
            _SAFARI_VISION_UA,
            {
                "clientName": "VISIONOS",
                "clientVersion": "1.02",
                "deviceMake": "Apple",
                "deviceModel": "RealityDevice17,1",
                "userAgent": _SAFARI_VISION_UA,
                "osName": "visionOS",
                "osVersion": "26.5.23O471",
            },
        ),
        _ClientCfg(
            "ANDROID_VR",
            "28",
            "com.google.android.apps.youtube.vr.oculus/1.65.10 "
            "(Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip",
            {
                "clientName": "ANDROID_VR",
                "clientVersion": "1.65.10",
                "deviceMake": "Oculus",
                "deviceModel": "Quest 3",
                "osName": "Android",
                "osVersion": "12L",
                "androidSdkVersion": 32,
            },
            range_bytes=512 * 1024,
        ),
        _ClientCfg(
            "TVHTML5",
            "7",
            "Mozilla/5.0(SMART-TV; Linux; Tizen 4.0.0.2) AppleWebkit/605.1.15 "
            "(KHTML, like Gecko) SamsungBrowser/9.2 TV Safari/605.1.15",
            {"clientName": "TVHTML5", "clientVersion": "7.20260707.07.00"},
        ),
        _ClientCfg(
            "WEB_REMIX",
            "67",
            _CHROME_UA,
            {"clientName": "WEB_REMIX", "clientVersion": "1.20260707.12.00"},
            extra_headers={
                "Origin": "https://music.youtube.com",
                "Referer": "https://music.youtube.com/",
            },
        ),
        _ClientCfg(
            "IOS",
            "5",
            "com.google.ios.youtube/21.26.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
            {
                "clientName": "IOS",
                "clientVersion": "21.26.4",
                "deviceMake": "Apple",
                "deviceModel": "iPhone16,2",
                "osName": "iPhone",
                "osVersion": "18.3.2.22D82",
            },
        ),
    )
}
_CASCADE = ("VISIONOS", "ANDROID_VR", "TVHTML5", "WEB_REMIX", "IOS")


class _Stream:
    def __init__(self, itag: int, url: str, mime_type: str, codecs: str, bitrate: int,
                 content_length: int, has_audio: bool, has_video: bool, client: str,
                 headers: Optional[Dict[str, str]] = None, width: int = 0, height: int = 0, fps: int = 0):
        self.itag = itag
        self.url = url
        self.mime_type = mime_type
        self.codecs = codecs
        self.bitrate = bitrate
        self.content_length = content_length
        self.has_audio = has_audio
        self.has_video = has_video
        self.client = client
        self.headers = dict(headers or {})
        self.width = width
        self.height = height
        self.fps = fps

    @property
    def ext(self) -> str:
        base = self.mime_type.split(";")[0].strip().lower()
        return _EXT_BY_MIME.get(base, base.split("/")[-1] or "bin")

    @property
    def kbps(self) -> int:
        return self.bitrate // 1000

    @property
    def audio_only(self) -> bool:
        return self.has_audio and not self.has_video

    @property
    def muxed(self) -> bool:
        return self.has_audio and self.has_video


class _VideoInfo:
    def __init__(self, video_id: str, title: str = "", author: str = "", duration: int = 0,
                 is_live: bool = False, streams: Optional[List[_Stream]] = None,
                 client: str = "", fetched_at: Optional[float] = None, expires_at: float = 0.0):
        self.video_id = video_id
        self.title = title
        self.author = author
        self.duration = duration
        self.is_live = is_live
        self.streams = list(streams or [])
        self.client = client
        self.fetched_at = time.time() if fetched_at is None else fetched_at
        self.expires_at = expires_at

    def select(self, selector: str = "bestaudio", max_kbps: Optional[int] = None) -> Optional[_Stream]:
        return _select_stream(self.streams, selector, max_kbps)


def _extract_video_id(text: str) -> str:
    s = (text or "").strip()
    if _ID_RE.fullmatch(s):
        return s
    m = _URL_ID_RE.search(s)
    if m:
        return m.group(1)
    raise YTError(f"not a valid YouTube URL/ID: {s!r}")


def _is_yt_ref(text: str) -> bool:
    s = (text or "").strip()
    if _URL_ID_RE.search(s):
        return True
    # Treat bare 11-char string as Video ID only if it isn't a plain lowercase English word (e.g. "unstoppable").
    if _ID_RE.fullmatch(s) and any(c.isdigit() or c.isupper() or c in "-_" for c in s):
        return True
    return False


def _pick_audio(cands: List[_Stream], max_kbps: Optional[int]) -> Optional[_Stream]:
    if not cands:
        return None
    if max_kbps:
        within = [s for s in cands if s.bitrate <= max_kbps * 1000]
        if within:
            return max(within, key=lambda s: s.bitrate)
        return min(cands, key=lambda s: s.bitrate)
    return max(cands, key=lambda s: s.bitrate)


def _select_one(streams: List[_Stream], sel: str, max_kbps: Optional[int]) -> Optional[_Stream]:
    sel = sel.strip().lower()
    audio = [s for s in streams if s.audio_only]
    if sel.isdigit():
        return next((s for s in streams if s.itag == int(sel)), None)
    if sel in ("bestaudio", "ba"):
        return _pick_audio(audio, max_kbps)
    if sel in ("m4a", "mp4a"):
        return _pick_audio([s for s in audio if s.ext == "m4a"], max_kbps)
    if sel in ("webm", "opus"):
        return _pick_audio([s for s in audio if s.ext == "webm"], max_kbps)
    muxed = [s for s in streams if s.muxed]
    if sel in ("best", "b"):
        return max(muxed, key=lambda s: (s.height, s.bitrate)) if muxed else None
    if sel in ("worst", "w"):
        return min(muxed, key=lambda s: (s.height, s.bitrate)) if muxed else None
    return None


def _select_stream(streams: List[_Stream], selector: str = "bestaudio", max_kbps: Optional[int] = None) -> Optional[_Stream]:
    for part in selector.split("/"):
        found = _select_one(streams, part, max_kbps)
        if found is not None:
            return found
    return None


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _parse_duration_str(txt: str) -> int:
    parts = [p for p in (txt or "").strip().split(":") if p.isdigit()]
    if not parts:
        return 0
    sec = 0
    for p in parts:
        sec = sec * 60 + int(p)
    return sec


class _HttpSession:
    """HTTP helper preferring requests+certifi (connection pooled), falling back to urllib+certifi."""

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self._ssl_ctx = _ssl_context()
        self._local = threading.local()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl_ctx)
        )
        try:
            import requests  # noqa: F401
            self._has_requests = True
        except Exception:
            self._has_requests = False

    def _req_timeout(self, timeout: Optional[float]):
        read_t = float(timeout or self.timeout)
        return (min(6.0, read_t), read_t)

    def _req_sess(self):
        s = getattr(self._local, "rs", None)
        if s is None:
            import requests
            s = requests.Session()
            self._local.rs = s
        return s

    def post_json(self, url: str, body: dict, headers: Dict[str, str], timeout: Optional[float] = None) -> dict:
        hdrs = {"Content-Type": "application/json", **headers}
        if self._has_requests:
            resp = self._req_sess().post(url, json=body, headers=hdrs, timeout=self._req_timeout(timeout))
            resp.raise_for_status()
            return resp.json()
        t = float(timeout or self.timeout)
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=hdrs, method="POST")
        with self._opener.open(req, timeout=t) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def get_bytes(self, url: str, headers: Dict[str, str], timeout: Optional[float] = None) -> Tuple[int, bytes, Optional[str]]:
        if self._has_requests:
            with self._req_sess().get(
                url, headers=headers, timeout=self._req_timeout(timeout), stream=True
            ) as resp:
                status = resp.status_code
                cr = resp.headers.get("Content-Range")
                if status in (400, 401, 403, 404, 410, 416):
                    return status, b"", cr
                if status >= 400:
                    raise urllib.error.HTTPError(url, status, resp.reason or "", resp.headers, None)
                buf = bytearray()
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        buf.extend(chunk)
                return status, bytes(buf), cr
        t = float(timeout or self.timeout)
        req = urllib.request.Request(url, headers=headers)
        try:
            with self._opener.open(req, timeout=t) as r:
                return r.status, r.read(), r.headers.get("Content-Range")
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 404, 410, 416):
                return exc.code, b"", exc.headers.get("Content-Range") if exc.headers else None
            raise

    def probe(self, url: str, headers: Dict[str, str], byte_range: str, timeout: Optional[float] = None) -> int:
        status, _, _ = self.get_bytes(url, {**headers, "Range": f"bytes={byte_range}"}, timeout=timeout or 8.0)
        return status


class _VisitorCache:
    def __init__(self, cache_dir: Optional[str], ttl: float = 24 * 3600):
        self.cache_dir = cache_dir
        self.ttl = ttl
        self._val: Optional[str] = None
        self._ts = 0.0

    @property
    def _path(self) -> Optional[str]:
        return os.path.join(self.cache_dir, "visitor.json") if self.cache_dir else None

    def get(self, sess: _HttpSession, force: bool = False) -> Optional[str]:
        if not force:
            if self._val and (time.time() - self._ts) < self.ttl:
                return self._val
            p = self._path
            if p and os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        ent = json.load(fh)
                    if ent.get("value") and (time.time() - float(ent.get("ts", 0))) < self.ttl:
                        self._val, self._ts = ent["value"], float(ent["ts"])
                        return self._val
                except Exception:
                    pass
        try:
            data = sess.post_json(
                _VISITOR_URL,
                {"context": {"client": _WEB_VISITOR_CONTEXT}},
                {"User-Agent": _CHROME_UA},
                timeout=6.0,
            )
            val = (data.get("responseContext") or {}).get("visitorData")
            if val:
                self._val, self._ts = val, time.time()
                p = self._path
                if p:
                    try:
                        os.makedirs(self.cache_dir, exist_ok=True)
                        tmp = f"{p}.tmp"
                        with open(tmp, "w", encoding="utf-8") as fh:
                            json.dump({"value": val, "ts": self._ts}, fh)
                        os.replace(tmp, p)
                    except Exception:
                        pass
                return val
        except Exception as exc:
            log.debug("visitor fetch failed: %s", exc)
        return None


def _parse_streams(streaming_data: dict, cfg: _ClientCfg) -> Tuple[List[_Stream], bool]:
    streams: List[_Stream] = []
    ciphered = False
    hdrs = cfg.media_headers()
    for key, muxed in (("formats", True), ("adaptiveFormats", False)):
        for f in streaming_data.get(key) or []:
            url = f.get("url")
            if not url:
                if f.get("signatureCipher") or f.get("cipher"):
                    ciphered = True
                continue
            mime_raw = f.get("mimeType", "")
            mime_base = mime_raw.split(";")[0].strip()
            m_c = re.search(r'codecs="([^"]*)"', mime_raw)
            codecs = m_c.group(1) if m_c else ""
            kind = mime_base.split("/")[0]
            streams.append(
                _Stream(
                    itag=int(f.get("itag", 0)),
                    url=url,
                    mime_type=mime_raw or mime_base,
                    codecs=codecs,
                    bitrate=int(f.get("bitrate") or f.get("averageBitrate") or 0),
                    content_length=int(f.get("contentLength") or 0),
                    has_audio=muxed or kind == "audio",
                    has_video=muxed or kind == "video",
                    client=cfg.name,
                    headers=dict(hdrs),
                    width=int(f.get("width") or 0),
                    height=int(f.get("height") or 0),
                    fps=int(f.get("fps") or 0),
                )
            )
    return streams, ciphered


class _YTClient:
    """Thread-safe YouTube resolver, search, and ranged downloader."""

    def __init__(self, cache_dir: Optional[str] = None, timeout: float = 15.0):
        self.session = _HttpSession(timeout=timeout)
        self._visitor = _VisitorCache(cache_dir)
        self._cache: Dict[str, _VideoInfo] = {}
        self._excluded: Dict[str, Dict[str, float]] = {}
        self._lock = threading.Lock()

    def _cache_get(self, vid: str) -> Optional[_VideoInfo]:
        with self._lock:
            info = self._cache.get(vid)
            if info is None:
                return None
            deadline = info.fetched_at + 4 * 3600
            if info.expires_at:
                deadline = min(deadline, info.expires_at - 600)
            if time.time() >= deadline:
                del self._cache[vid]
                return None
            return info

    def _cache_put(self, info: _VideoInfo) -> None:
        with self._lock:
            self._cache[info.video_id] = info

    def _excluded_for(self, vid: str) -> set:
        with self._lock:
            ent = self._excluded.get(vid, {})
            now = time.time()
            for name in [n for n, until in ent.items() if until <= now]:
                del ent[name]
            return set(ent)

    def report_refused(self, vid: str, client: str, ttl: float = 600.0) -> None:
        with self._lock:
            self._excluded.setdefault(vid, {})[client] = time.time() + ttl
            cached = self._cache.get(vid)
            if cached and cached.client == client:
                del self._cache[vid]

    def search(self, query: str, limit: int = 5) -> List[dict]:
        """Search YouTube via InnerTube WEB search endpoint (returns list of dicts)."""
        body = {"context": {"client": _WEB_VISITOR_CONTEXT}, "query": query}
        data = self.session.post_json(_SEARCH_URL, body, {"User-Agent": _CHROME_UA})
        hits: List[dict] = []
        seen: set = set()

        def _walk(obj):
            if len(hits) >= max(limit, 10):
                return
            if isinstance(obj, dict):
                vr = obj.get("videoRenderer")
                if isinstance(vr, dict) and vr.get("videoId"):
                    vid = vr["videoId"]
                    if vid not in seen:
                        seen.add(vid)
                        title = "".join(t.get("text", "") for t in (vr.get("title") or {}).get("runs") or [])
                        if not title:
                            title = (vr.get("title") or {}).get("simpleText", "") or vid
                        author = "".join(t.get("text", "") for t in (vr.get("ownerText") or {}).get("runs") or [])
                        dur_text = ((vr.get("lengthText") or {}).get("simpleText")) or ""
                        dur_sec = _parse_duration_str(dur_text)
                        is_live = not dur_text
                        hits.append({
                            "id": vid,
                            "title": title.strip(),
                            "author": author.strip(),
                            "duration_text": dur_text or "LIVE",
                            "duration": dur_sec,
                            "is_live": is_live,
                            "url": f"https://youtu.be/{vid}",
                        })
                    return
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)

        _walk(data)
        return hits[:limit]

    def _try_client(self, vid: str, cfg: _ClientCfg, visitor: Optional[str], max_kbps: int) -> _VideoInfo:
        ctx = {**cfg.context, "hl": "en", "gl": "US"}
        if visitor:
            ctx["visitorData"] = visitor
        body = {
            "videoId": vid,
            "contentCheckOk": True,
            "racyCheckOk": True,
            "context": {"client": ctx},
        }
        headers = {
            "User-Agent": cfg.user_agent,
            "X-YouTube-Client-Name": cfg.client_id,
            "X-YouTube-Client-Version": cfg.version,
            **cfg.extra_headers,
        }
        if visitor:
            headers["X-Goog-Visitor-Id"] = visitor

        data = self.session.post_json(_PLAYER_URL, body, headers)
        status = data.get("playabilityStatus") or {}
        if status.get("status") != "OK":
            raise _SkipClient(f"{status.get('status')}: {status.get('reason', '')}".strip().rstrip(":"))

        sdata = data.get("streamingData") or {}
        streams, ciphered = _parse_streams(sdata, cfg)
        if not streams:
            raise _SkipClient("signatureCipher only" if ciphered else "no direct URLs (SABR-only)")

        det = data.get("videoDetails") or {}
        expires_in = int(sdata.get("expiresInSeconds") or 0)
        info = _VideoInfo(
            video_id=vid,
            title=det.get("title", "") or vid,
            author=det.get("author", "") or "",
            duration=int(det.get("lengthSeconds") or 0),
            is_live=bool(det.get("isLiveContent")),
            streams=streams,
            client=cfg.name,
            expires_at=(time.time() + expires_in) if expires_in else 0.0,
        )
        probe = info.select("m4a/bestaudio", max_kbps) or streams[0]
        n = probe.content_length
        rng = f"{n - 1}-{n - 1}" if n > 0 else "0-1"
        try:
            code = self.session.probe(probe.url, probe.headers, rng)
            if code not in (200, 206):
                raise _SkipClient(f"HTTP {code}")
        except _SkipClient:
            raise
        except Exception as exc:
            log.debug("probe error %s (%s); continuing", cfg.name, exc)
        return info

    def _ytdlp_fallback(self, vid: str) -> Optional[_VideoInfo]:
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            return None
        opts = {"quiet": True, "no_warnings": True, "socket_timeout": 15, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
        streams: List[_Stream] = []
        for f in data.get("formats") or []:
            url = f.get("url")
            if not url or str(f.get("protocol", "")).startswith(("m3u8", "http_dash_segments")):
                continue
            has_a = f.get("acodec") not in (None, "none")
            has_v = f.get("vcodec") not in (None, "none")
            if not (has_a or has_v):
                continue
            ext = f.get("ext") or "bin"
            fid = str(f.get("format_id", ""))
            streams.append(
                _Stream(
                    itag=int(fid) if fid.isdigit() else 0,
                    url=url,
                    mime_type=f'{"video" if has_v else "audio"}/{"mp4" if ext == "m4a" else ext}',
                    codecs="",
                    bitrate=int((f.get("abr") or f.get("tbr") or 0) * 1000),
                    content_length=int(f.get("filesize") or f.get("filesize_approx") or 0),
                    has_audio=has_a,
                    has_video=has_v,
                    client="yt-dlp",
                    headers=dict(f.get("http_headers") or {}),
                    width=int(f.get("width") or 0),
                    height=int(f.get("height") or 0),
                    fps=int(f.get("fps") or 0),
                )
            )
        if not streams:
            return None
        return _VideoInfo(
            video_id=vid,
            title=data.get("title") or vid,
            author=data.get("uploader") or "",
            duration=int(data.get("duration") or 0),
            is_live=bool(data.get("is_live")),
            streams=streams,
            client="yt-dlp",
        )

    def extract(self, video: str, *, max_kbps: int = 160, use_cache: bool = True) -> _VideoInfo:
        vid = _extract_video_id(video)
        excluded = self._excluded_for(vid)
        if use_cache:
            cached = self._cache_get(vid)
            if cached and cached.client not in excluded:
                return cached

        reasons: Dict[str, str] = {}
        visitor = self._visitor.get(self.session)
        for attempt in range(2):
            for name in _CASCADE:
                if name in excluded:
                    continue
                cfg = _CLIENTS[name]
                try:
                    info = self._try_client(vid, cfg, visitor, max_kbps)
                    self._cache_put(info)
                    return info
                except _SkipClient as exc:
                    reasons[name] = str(exc)
                except Exception as exc:
                    reasons[name] = f"{type(exc).__name__}: {exc}"
            if attempt == 0 and any("LOGIN_REQUIRED" in r for r in reasons.values()):
                fresh = self._visitor.get(self.session, force=True)
                if not fresh or fresh == visitor:
                    break
                visitor = fresh
                reasons.clear()
                continue
            break

        if "yt-dlp" not in excluded:
            try:
                info = self._ytdlp_fallback(vid)
                if info is not None:
                    self._cache_put(info)
                    return info
            except Exception as exc:
                reasons["yt-dlp"] = f"{type(exc).__name__}: {exc}"

        detail = "; ".join(f"{k}: {v}" for k, v in reasons.items()) or "no clients succeeded"
        raise YTError(f"Could not extract {vid}: {detail}")

    def _fetch_range(self, stream: _Stream, start: int, end: int, retries: int = 3) -> Tuple[bytes, Optional[int]]:
        hdrs = {**stream.headers, "Range": f"bytes={start}-{end}"}
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                status, data, cr = self.session.get_bytes(stream.url, hdrs)
                if status == 416:
                    return b"", None
                if status in (400, 401, 403, 404, 410):
                    raise _StreamRefused(f"HTTP {status}", status=status, client=stream.client)
                if status == 200 and start > 0:
                    raise YTError("server ignored Range header")
                total = None
                if cr and "/" in cr:
                    tail = cr.rsplit("/", 1)[1]
                    total = int(tail) if tail.isdigit() else None
                elif status == 200:
                    total = len(data)
                return data, total
            except _StreamRefused:
                raise
            except (urllib.error.URLError, OSError, http.client.HTTPException, socket.timeout, Exception) as exc:
                last_err = exc
                if attempt < retries:
                    time.sleep(min(0.5 * (2 ** (attempt + 1)), 5.0))
        raise YTError(f"chunk failed at byte {start}: {last_err}")

    def download(self, video: str, dest_dir: str, selector: str = "m4a/bestaudio", *,
                 max_kbps: Optional[int] = None, max_minutes: int = 0) -> Tuple[str, _VideoInfo, _Stream]:
        """Resolve, validate duration/format, download to dest_dir (cached by video_id), and return (path, info, stream)."""
        vid = _extract_video_id(video)
        max_k = max_kbps or int(_cfg.get("max_kbps", 160))

        with _vid_lock(vid):
            last_exc: Optional[Exception] = None
            for _ in range(3):
                info = self.extract(vid, max_kbps=max_k)
                if info.is_live:
                    raise YTError(f"'{info.title}' is a live stream (cannot download finite track).")
                if max_minutes > 0 and info.duration > max_minutes * 60:
                    mins = info.duration // 60
                    raise YTError(
                        f"'{info.title}' is {mins}m long (limit is {max_minutes}m). "
                        f"Raise via `!yt maxmins {mins + 5}` if needed."
                    )

                stream = info.select(selector, max_kbps)
                if stream is None:
                    if selector.startswith("best"):
                        raise YTError(
                            "No muxed MP4 stream available for this video (YouTube served adaptive-only streams). "
                            "Use `!yt playcall` or `!yt dial` to stream its audio!"
                        )
                    have = ", ".join(f"{s.itag}({s.ext})" for s in info.streams) or "none"
                    raise YTError(f"No matching format `{selector}` (available: {have})")

                os.makedirs(dest_dir, exist_ok=True)
                safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f\s]+', "_", info.title or vid).strip("._")[:42] or vid
                fname = f"yt_{safe_title}_[{vid}]_{stream.itag}.{stream.ext}"
                dest = os.path.join(dest_dir, fname)

                # Cache hit by exact filename or video_id+ext suffix
                for existing in os.listdir(dest_dir):
                    if f"[{vid}]_{stream.itag}.{stream.ext}" in existing:
                        ep = os.path.join(dest_dir, existing)
                        if os.path.isfile(ep) and os.path.getsize(ep) > 1024:
                            try:
                                os.utime(ep, None)
                            except Exception:
                                pass
                            return ep, info, stream

                part = dest + ".part"
                total = stream.content_length or None
                pos = os.path.getsize(part) if os.path.exists(part) else 0
                if total is not None and pos > total:
                    pos = 0
                cfg = _CLIENTS.get(stream.client)
                chunk = cfg.range_bytes if cfg else _MIB

                try:
                    with open(part, "ab" if pos else "wb") as fh:
                        while total is None or pos < total:
                            end = pos + chunk - 1
                            if total is not None:
                                end = min(end, total - 1)
                            asked = end - pos + 1
                            data, seen_total = self._fetch_range(stream, pos, end)
                            if seen_total is not None:
                                total = seen_total
                            if not data:
                                break
                            fh.write(data)
                            pos += len(data)
                            if total is None and len(data) < asked:
                                break
                    if total is not None and pos != total:
                        raise YTError(f"incomplete download ({pos}/{total} bytes)")
                    os.replace(part, dest)
                    return dest, info, stream
                except _StreamRefused as exc:
                    self.report_refused(vid, stream.client)
                    if os.path.exists(part):
                        try:
                            os.remove(part)
                        except Exception:
                            pass
                    last_exc = exc

            raise last_exc or YTError("YouTube download failed after client failovers")


# =============================================================================
# Plugin helpers & cache housekeeping
# =============================================================================

def _num(jid: str) -> str:
    return (jid or "").split("@")[0].split(":")[0].lstrip("+")


def _fmt_dur(sec: int) -> str:
    sec = max(0, int(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _yt() -> _YTClient:
    global _yt_client
    if _yt_client is None:
        vdir = _ctx.get_path("visitor") if _ctx and hasattr(_ctx, "get_path") else None
        _yt_client = _YTClient(cache_dir=vdir)
    return _yt_client


def _cache_dir() -> str:
    if _ctx and hasattr(_ctx, "get_path"):
        d = _ctx.get_path("cache", public=False)
    else:
        d = os.path.expanduser("~/.wa_app/ytcall_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _prune_cache():
    """Keep cached YouTube files under max_cache_mb via LRU eviction."""
    with _CACHE_LOCK:
        cdir = _cache_dir()
        limit_bytes = max(20, int(_cfg.get("max_cache_mb", 150))) * _MIB
        files = []
        total = 0
        for fn in os.listdir(cdir):
            p = os.path.join(cdir, fn)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
                files.append((st.st_mtime, st.st_size, p, fn))
                total += st.st_size
            except OSError:
                pass
        if total <= limit_bytes:
            return
        files.sort(key=lambda x: x[0])  # oldest first
        try:
            from core import call_media
            cm_cache = call_media.call_cache_dir()
        except Exception:
            cm_cache = ""

        for _, sz, p, fn in files:
            if total <= limit_bytes * 0.8:
                break
            try:
                os.remove(p)
                total -= sz
                if cm_cache and os.path.isdir(cm_cache):
                    base = os.path.splitext(fn)[0]
                    for cf in os.listdir(cm_cache):
                        if cf.startswith(base + "_"):
                            try:
                                os.remove(os.path.join(cm_cache, cf))
                            except OSError:
                                pass
            except OSError:
                pass


def _save():
    if _ctx and hasattr(_ctx, "save_json"):
        _ctx.save_json("config.json", _cfg)


def _load():
    if _ctx and hasattr(_ctx, "load_json"):
        data = _ctx.load_json("config.json", default=None)
        if isinstance(data, dict):
            _cfg.update({k: v for k, v in data.items() if k in DEFAULT_CFG})


def _is_owner(msg) -> bool:
    if bool(getattr(msg, "from_me", False)):
        return True
    owners = _cfg.get("owners", [])
    if not owners:
        return True
    return _num(getattr(msg, "sender_id", "")) in owners


def _resolve_target_ref(chat_id: str, raw_arg: str, max_mins: int) -> Tuple[str, str]:
    """Turn `#index`, YouTube URL/ID, or search query into (video_id_or_url, search_note)."""
    arg = (raw_arg or "").strip()
    if not arg:
        raise YTError("Empty YouTube link or search query.")

    if arg.startswith("#") and arg[1:].isdigit():
        idx = int(arg[1:]) - 1
        hits = _LAST_SEARCH.get(chat_id, [])
        if 0 <= idx < len(hits):
            return hits[idx]["id"], ""
        raise YTError(f"Search slot `#{idx + 1}` not found. Run `!yt search <query>` first.")

    if _is_yt_ref(arg):
        return _extract_video_id(arg), ""

    hits = _yt().search(arg, limit=6)
    if not hits:
        raise YTError(f"No YouTube results found for `{arg}`.")
    _LAST_SEARCH[chat_id] = hits

    # Prefer a non-live track within max_mins if one exists among top hits
    for h in hits:
        if not h["is_live"] and (max_mins <= 0 or h["duration"] <= max_mins * 60):
            return h["id"], f"🔎 Matched: *{h['title']}* ({h['duration_text']})"
    for h in hits:
        if not h["is_live"]:
            return h["id"], f"🔎 Matched: *{h['title']}* ({h['duration_text']})"
    return hits[0]["id"], f"🔎 Matched: *{hits[0]['title']}*"


def _resolve_live_stream_sync(chat_id: str, query_or_url: str) -> dict:
    """Resolve YouTube video & direct audio stream URL cleanly without downloading to disk."""
    yt = _yt()
    max_mins = int(_cfg.get("max_minutes", 60))
    max_kbps = int(_cfg.get("max_kbps", 160))
    vid, _ = _resolve_target_ref(chat_id, query_or_url, max_mins)
    info = yt.extract(vid, max_kbps=max_kbps)
    if info.is_live:
        raise YTError(f"'{info.title}' is a live stream without direct finite audio segments.")
    if max_mins > 0 and info.duration > max_mins * 60:
        mins = info.duration // 60
        raise YTError(
            f"'{info.title}' is {mins}m long (limit is {max_mins}m). "
            f"Raise via `!yt maxmins {mins + 10}` if needed."
        )
    stream = info.select("m4a/webm/bestaudio", max_kbps)
    if stream is None:
        have = ", ".join(f"{s.itag}({s.ext})" for s in info.streams) or "none"
        raise YTError(f"No audio stream available (have: {have})")
    return {
        "video_id": vid,
        "title": info.title or vid,
        "author": info.author or "YouTube",
        "duration": info.duration,
        "client": stream.client,
        "ext": stream.ext,
        "kbps": stream.kbps,
        "content_length": stream.content_length,
        "size_mb": round((stream.content_length or 0) / _MIB, 2),
        "url": stream.url,
        "headers": dict(stream.headers),
        "mode": "stream",
    }


def _prepare_yt_media_sync(chat_id: str, query_or_url: str, mode: str = "audio") -> dict:
    """Resolve, download, and transcode YouTube audio (cache mode) or video for WA Core calls."""
    from core import call_media

    yt = _yt()
    max_mins = int(_cfg.get("video_max_minutes", 10) if mode == "video" else _cfg.get("max_minutes", 60))
    vid, _ = _resolve_target_ref(chat_id, query_or_url, max_mins)

    selector = "best/18" if mode == "video" else "m4a/webm/bestaudio"
    max_kbps = None if mode == "video" else int(_cfg.get("max_kbps", 160))
    local_path, info, stream = yt.download(
        vid, _cache_dir(), selector=selector, max_kbps=max_kbps, max_minutes=max_mins
    )
    _prune_cache()

    if mode == "video":
        ok, _, _, msg_v = call_media.prepare_video_for_call(local_path, fps=15)
        if not ok:
            raise YTError(f"Video transcode failed: {msg_v}")
    else:
        ok, err_a = call_media.prepare_audio_for_call(local_path)
        if not ok:
            raise YTError(f"Audio prepare failed: {err_a}")

    return {
        "video_id": vid,
        "title": info.title or vid,
        "author": info.author or "YouTube",
        "duration": info.duration,
        "client": info.client,
        "ext": stream.ext,
        "kbps": stream.kbps,
        "size_mb": round(os.path.getsize(local_path) / _MIB, 2),
        "path": local_path,
        "mode": mode,
    }


def _supports_live_pcm(ctx) -> bool:
    return bool(
        _cfg.get("stream_mode", True)
        and hasattr(ctx, "play_call_url")
        and hasattr(ctx.calls, "play_url_stream")
    )


async def _play_yt_audio_entry(ctx, cid: str, chat_id: str, meta_or_query, *, track_for_loop: bool = True) -> dict:
    """Play YouTube audio via live zero-download PCMStream (with automatic client failover & cache fallback)."""
    meta = (
        dict(meta_or_query)
        if isinstance(meta_or_query, dict)
        else await asyncio.to_thread(
            _resolve_live_stream_sync if _supports_live_pcm(ctx) else _prepare_yt_media_sync,
            chat_id,
            str(meta_or_query),
        )
    )

    if meta.get("mode") == "stream" and _supports_live_pcm(ctx):
        vid = meta["video_id"]
        max_mins = int(_cfg.get("max_minutes", 60))
        max_secs = max_mins * 60 if max_mins > 0 else 0
        last_err = ""
        for attempt in range(3):
            if cid not in ctx.calls._active:
                return {"ok": False, "error": "Call ended"}
            if attempt > 0:
                meta = await asyncio.to_thread(_resolve_live_stream_sync, chat_id, vid)
            label = f"{meta['title']} [{vid}]"
            res = await ctx.play_call_url(
                cid,
                meta["url"],
                headers=meta.get("headers"),
                content_length=int(meta.get("content_length") or 0),
                label=label,
                max_seconds=max_secs,
            )
            if res.get("ok"):
                if track_for_loop:
                    _ACTIVE_LIVE_TRACK[cid] = {
                        "video_id": vid,
                        "chat_id": chat_id,
                        "title": meta["title"],
                        "duration": meta["duration"],
                    }
                return {"ok": True, "meta": meta, "live": True}
            last_err = str(res.get("error") or "stream failed")
            if res.get("refused"):
                _yt().report_refused(vid, meta.get("client", ""))
                continue
            if "does not export CallPlayPCMStream" in last_err or "No live HTTP audio decoder" in last_err:
                break
            return {"ok": False, "error": last_err}

        if "does not export" not in last_err and "No live HTTP audio decoder" not in last_err:
            return {"ok": False, "error": last_err or "YouTube live stream refused by all clients"}

    # Fallback: cached/local file playback (`!yt mode cache` or older `.so`/no decoder)
    _ACTIVE_LIVE_TRACK.pop(cid, None)
    if not meta.get("path"):
        meta = await asyncio.to_thread(_prepare_yt_media_sync, chat_id, meta.get("video_id") or str(meta_or_query), "audio")
    res = await ctx.play_call_audio(cid, meta["path"], loop=bool(track_for_loop and _cfg.get("loop", False)))
    if res.get("ok"):
        return {"ok": True, "meta": meta, "live": False}
    return {"ok": False, "error": res.get("error")}


def _parse_dial_args(cmd: str, rest: str, msg) -> Tuple[str, str]:
    if cmd == "playcall":
        return msg.chat_id, rest.strip()
    p1, _, p2 = rest.partition(" ")
    p1 = p1.strip()
    if not p1 or not p2.strip():
        raise YTError(f"Usage: `!yt {cmd} <phone|me|here> <song_or_url>`")
    low = p1.lower()
    if low == "me":
        target = msg.sender_id
    elif low == "here":
        target = msg.chat_id
    else:
        target = re.sub(r"[^0-9@a-zA-Z.]", "", p1)
    return target, p2.strip()


# =============================================================================
# Plugin lifecycle & event handlers
# =============================================================================

async def setup(ctx):
    global _ctx
    _ctx = ctx
    _load()
    _yt()
    ctx.log.info("ytcall plugin ready (VoIP Supported=%s)", ctx.calls.is_supported())


async def teardown(ctx):
    for t in list(_BG_TASKS):
        t.cancel()
    _BG_TASKS.clear()
    _PENDING_PLAY.clear()
    _YT_QUEUES.clear()
    _ACTIVE_LIVE_TRACK.clear()


async def _advance_queue(ctx, cid: str):
    q = _YT_QUEUES.get(cid)
    while q:
        nxt = q.pop(0)
        ev_ready = nxt.get("ready_event")
        if ev_ready and not ev_ready.is_set():
            await ev_ready.wait()
        if cid not in ctx.calls._active:
            return
        if nxt.get("error"):
            if nxt.get("notify_chat"):
                await ctx.send(
                    nxt["notify_chat"],
                    f"⚠️ Skipped failed queued track `{nxt.get('query')}`: {nxt['error']}",
                )
            continue
        chat_id = nxt.get("notify_chat") or ""
        try:
            play_res = await _play_yt_audio_entry(
                ctx, cid, chat_id, nxt.get("meta") or nxt["query"], track_for_loop=False
            )
        except Exception as e:
            play_res = {"ok": False, "error": str(e)}
        if play_res.get("ok"):
            meta = play_res["meta"]
            tag = "📡 Live" if play_res.get("live") else "🎵 Cached"
            if chat_id:
                await ctx.send(
                    chat_id,
                    f"⏭️ *Now Playing from YT Queue ({tag}):* {meta['title']} (`{_fmt_dur(meta['duration'])}`)",
                )
            return
        if chat_id:
            await ctx.send(chat_id, f"⚠️ Next track playback error: {play_res.get('error')}")

    # Queue is empty: loop current live track if loop=ON
    loop_spec = _ACTIVE_LIVE_TRACK.get(cid)
    if loop_spec and _cfg.get("loop", False) and cid in ctx.calls._active:
        try:
            await _play_yt_audio_entry(ctx, cid, loop_spec["chat_id"], loop_spec["video_id"], track_for_loop=True)
        except Exception as e:
            log.debug("live loop replay failed: %s", e)


async def on_call(ctx, call_ev):
    ev = call_ev.event
    cid = call_ev.call_id

    if ev == "ready":
        spec = _PENDING_PLAY.pop(cid, None)
        if not spec:
            return

        async def _start_ready():
            kind = spec.get("kind", "audio")
            notify = spec.get("notify_chat")
            if kind == "video":
                res = await ctx.play_call_video(
                    cid, spec.get("path", ""), fps=15, loop=bool(spec.get("loop", _cfg.get("loop", False))), with_audio=True
                )
                if notify:
                    if res.get("ok"):
                        await ctx.send(
                            notify,
                            f"📺 *YouTube Video Live!*\n"
                            f"▶️ *Title:* {spec.get('title', 'YouTube Video')}\n"
                            f"⏱️ *Length:* `{_fmt_dur(spec.get('duration', 0))}`\n"
                            f"Controls: `!yt stop` | `!yt hangup`"
                        )
                    else:
                        await ctx.send(notify, f"⚠️ Call connected, but YT video failed: {res.get('error')}")
                return

            meta = spec.get("meta")
            if not meta:
                return
            try:
                play_res = await _play_yt_audio_entry(
                    ctx, cid, notify or "", meta, track_for_loop=True
                )
            except Exception as e:
                play_res = {"ok": False, "error": str(e)}
            if notify:
                if play_res.get("ok"):
                    m = play_res["meta"]
                    badge = "📡 Live PCMStream" if play_res.get("live") else "🎵 Cached WAV"
                    await ctx.send(
                        notify,
                        f"📺 *YouTube Audio Connected!* ({badge})\n"
                        f"▶️ *Title:* {m['title']}\n"
                        f"⏱️ *Length:* `{_fmt_dur(m['duration'])}` • "
                        f"🔁 *Loop:* `{'ON' if _cfg.get('loop') else 'OFF'}`\n"
                        f"Controls: `!yt skip` | `!yt queue <song>` | `!yt pause` | `!yt stop` | `!yt hangup`"
                    )
                else:
                    await ctx.send(notify, f"⚠️ Call connected, but YT stream failed: {play_res.get('error')}")

        _spawn(_start_ready())

    elif ev == "audio_finish":
        loop_spec = _ACTIVE_LIVE_TRACK.get(cid)
        is_own_live = bool(loop_spec and f"[{loop_spec['video_id']}]" in (getattr(call_ev, "path", "") or ""))
        if _YT_QUEUES.get(cid) or (_cfg.get("loop", False) and is_own_live):
            _spawn(_advance_queue(ctx, cid))
        elif loop_spec:
            _ACTIVE_LIVE_TRACK.pop(cid, None)

    elif ev == "end":
        _PENDING_PLAY.pop(cid, None)
        _YT_QUEUES.pop(cid, None)
        _ACTIVE_LIVE_TRACK.pop(cid, None)


async def on_message(ctx, msg):
    text = (msg.text or "").strip()
    if not text:
        return
    low = text.lower()

    matched = None
    for pfx in PREFIXES:
        if low == pfx or low.startswith(pfx + " "):
            matched = pfx
            break
    if matched is None:
        return

    raw_arg = text[len(matched):].strip()
    head, _, rest = raw_arg.partition(" ")
    cmd = head.lower()
    rest = rest.strip()

    known_cmds = {
        "", "help", "search", "s", "info", "play", "dial", "playcall", "vcall",
        "queue", "q", "skip", "pause", "resume", "stop", "hangup", "save",
        "status", "clearcache", "mode", "loop", "quality", "maxmins", "public",
    }
    if cmd not in known_cmds:
        rest = raw_arg
        cmd = "__auto__"

    is_owner = _is_owner(msg)
    if not is_owner and not _cfg.get("public_dj", False) and cmd not in ("", "help", "search", "s", "info", "status", "q"):
        return await ctx.reply(msg, "❌ Owner-only command. Owner can allow guests via `!yt public on`.")

    if cmd in ("", "help"):
        return await ctx.reply(
            msg,
            "📺 *YouTube Call PCMStream Guide*\n\n"
            "*Play on Calls (Zero-Download Live Audio):*\n"
            "• `!yt <url or song name>` — Stream live if in call, or search\n"
            "• `!yt play <url|song|#N>` — Stream/switch audio live in active call\n"
            "• `!yt playcall <url|song|#N>` — Call this chat & stream on pickup\n"
            "• `!yt dial <num|me> <url|song>` — Call number & stream on pickup\n"
            "• `!yt vcall <num|me> <url|vid>` — Video call with YT MP4\n\n"
            "*Search, Queue & Library:*\n"
            "• `!yt search <query>` — Top 5 results (`#1`..`#5`)\n"
            "• `!yt queue <url|song|#N>` — Queue track in live call DJ\n"
            "• `!yt q` | `skip` | `pause` | `resume` | `stop` | `hangup`\n"
            "• `!yt save <url|song|#N>` — Save offline WAV to `!call tracks`\n\n"
            "*Settings (Owner):*\n"
            "• `!yt mode <stream|cache>` | `quality <hq|fast>` | `loop <on|off>`\n"
            "• `!yt maxmins <N>` | `public <on|off>` | `status` | `clearcache`"
        )

    if cmd == "status":
        cdir = _cache_dir()
        cfiles = [os.path.join(cdir, f) for f in os.listdir(cdir) if os.path.isfile(os.path.join(cdir, f))]
        cbytes = sum(os.path.getsize(f) for f in cfiles)
        active = await ctx.calls.list_active()
        try:
            import yt_dlp  # noqa: F401
            ytdlp_ok = "Available ✅"
        except Exception:
            ytdlp_ok = "Not installed (InnerTube active)"
        q_label = "fast (<=80k)" if int(_cfg.get("max_kbps", 160)) <= 80 else "hq (<=160k)"
        m_label = "Live PCMStream ⚡" if _supports_live_pcm(ctx) else "Local Cache (WAV)"
        return await ctx.reply(
            msg,
            "📺 *YouTube Call Plugin Status*\n"
            f"• ⚙️ *VoIP Engine:* {'Ready ✅' if ctx.calls.is_supported() else 'Missing .so ❌'}\n"
            f"• 📡 *Playback Mode:* `{m_label}`\n"
            f"• 📞 *Active Calls:* `{len(active)}` | *Jobs:* `{len(_BG_TASKS)}`\n"
            f"• 🎛️ *Quality:* `{q_label}` | 🔁 *Loop:* `{'ON' if _cfg.get('loop') else 'OFF'}`\n"
            f"• ⏱️ *Max Audio:* `{_cfg.get('max_minutes')}m` | *Max Video:* `{_cfg.get('video_max_minutes')}m`\n"
            f"• 💾 *Saved Cache:* `{len(cfiles)} files ({cbytes / _MIB:.1f} / {_cfg.get('max_cache_mb')} MB)`\n"
            f"• 🧩 *yt-dlp Fallback:* {ytdlp_ok}"
        )

    if cmd == "clearcache":
        cdir = _cache_dir()
        removed = 0
        with _CACHE_LOCK:
            for fn in os.listdir(cdir):
                p = os.path.join(cdir, fn)
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                        removed += 1
                    except OSError:
                        pass
        return await ctx.reply(msg, f"🗑️ Cleared `{removed}` cached YouTube media file(s).")

    if cmd in ("search", "s"):
        if not rest:
            return await ctx.reply(msg, "⚠️ Usage: `!yt search <song name>`")

        async def _job_search():
            try:
                hits = await asyncio.to_thread(_yt().search, rest, 5)
            except Exception as e:
                return await ctx.reply(msg, f"❌ YouTube search failed: {e}")
            if not hits:
                return await ctx.reply(msg, f"🔍 No results found for `{rest}`.")
            _LAST_SEARCH[msg.chat_id] = hits
            lines = [f"🔍 *YouTube Results for:* `{rest}`"]
            for i, h in enumerate(hits, 1):
                lines.append(f"`#{i}` *{h['title']}*\n   👤 {h['author'] or 'YouTube'} • ⏱️ `{h['duration_text']}` • {h['url']}")
            lines.append("\nReply with `!yt playcall #1`, `!yt dial me #1`, `!yt play #1`, or `!yt queue #1`.")
            await ctx.reply(msg, "\n".join(lines))

        _spawn(_job_search())
        return

    if cmd == "info":
        if not rest:
            return await ctx.reply(msg, "⚠️ Usage: `!yt info <url|id|query|#N>`")

        async def _job_info():
            try:
                vid, _ = await asyncio.to_thread(
                    _resolve_target_ref, msg.chat_id, rest, int(_cfg.get("max_minutes", 20))
                )
                info = await asyncio.to_thread(_yt().extract, vid, max_kbps=int(_cfg.get("max_kbps", 160)))
            except Exception as e:
                return await ctx.reply(msg, f"❌ Could not resolve `{rest}`: {e}")
            m4a = info.select("m4a/bestaudio", int(_cfg.get("max_kbps", 160)))
            sz = f" (~{m4a.content_length / _MIB:.1f} MB)" if (m4a and m4a.content_length) else ""
            fmt_txt = f"{m4a.ext.upper()} {m4a.kbps}kbps{sz}" if m4a else "none"
            await ctx.reply(
                msg,
                f"🎬 *{info.title}*\n"
                f"👤 *Channel:* {info.author or 'YouTube'}\n"
                f"⏱️ *Duration:* `{_fmt_dur(info.duration)}` {'(LIVE)' if info.is_live else ''}\n"
                f"🎵 *Best Audio:* `{fmt_txt}` via `{info.client}`\n"
                f"🔗 https://youtu.be/{info.video_id}"
            )

        _spawn(_job_info())
        return

    if cmd == "save":
        if not rest:
            return await ctx.reply(msg, "⚠️ Usage: `!yt save <url|id|query|#N>`")
        await ctx.reply(msg, f"⏳ Downloading & preparing `{rest}` for CallMusic library...")

        async def _job_save():
            try:
                meta = await asyncio.to_thread(_prepare_yt_media_sync, msg.chat_id, rest, "audio")
                from core import call_media
                ok_p, prep = await asyncio.to_thread(call_media.prepare_audio_for_call, meta["path"])
                if not ok_p:
                    return await ctx.reply(msg, f"❌ Transcode failed: {prep}")
                ext = os.path.splitext(prep)[1] or ".wav"
                clean_title = re.sub(r'[\\/:*?"<>|\x00-\x1f\s]+', "_", meta["title"]).strip("._")[:48] or meta["video_id"]
                dst = os.path.join(call_media.call_music_dir(), f"{clean_title}_[{meta['video_id']}]{ext}")
                await asyncio.to_thread(shutil.copy2, prep, dst)
                await ctx.reply(
                    msg,
                    f"💾 *Saved to CallMusic Library!*\n"
                    f"🎵 `{os.path.basename(dst)}`\nUse `!call tracks` or `!yt play` anytime."
                )
            except Exception as e:
                log.exception("yt save failed")
                await ctx.reply(msg, f"❌ Failed to save track: {e}")

        _spawn(_job_save())
        return

    # ---- Outbound Dial + Play (`!yt dial`, `!yt playcall`, `!yt vcall`)
    if cmd in ("dial", "playcall", "vcall"):
        mode = "video" if cmd == "vcall" else "audio"
        try:
            target, song_q = _parse_dial_args(cmd, rest, msg)
        except YTError as e:
            return await ctx.reply(msg, f"⚠️ {e}")

        use_live = (mode == "audio") and _supports_live_pcm(ctx)
        await ctx.reply(
            msg,
            f"⏳ Resolving YouTube stream (`{song_q}`)..."
            if use_live
            else f"⏳ Fetching & preparing YouTube {'video' if mode == 'video' else 'audio'} (`{song_q}`)..."
        )

        async def _job_dial():
            try:
                if use_live:
                    meta = await asyncio.to_thread(_resolve_live_stream_sync, msg.chat_id, song_q)
                    sz_note = f"live ~{meta['size_mb']} MB" if meta["size_mb"] else "live PCM"
                else:
                    meta = await asyncio.to_thread(_prepare_yt_media_sync, msg.chat_id, song_q, mode)
                    sz_note = f"{meta['size_mb']} MB cached"
            except Exception as e:
                log.exception("yt %s prepare failed", cmd)
                return await ctx.reply(msg, f"❌ YouTube prepare failed: {e}")

            await ctx.reply(
                msg,
                f"📲 Calling `{_num(target)}` {'(Video Call)' if mode == 'video' else '(Voice Call)'}...\n"
                f"🎵 *On pickup:* {meta['title']} (`{_fmt_dur(meta['duration'])}`, {sz_note})"
            )
            res = await ctx.call(target, video=(mode == "video"))
            if not res.get("ok"):
                return await ctx.reply(msg, f"❌ Failed to place call: {res.get('error')}")

            cid = res.get("call_id", "")
            _PENDING_PLAY[cid] = {
                "kind": mode,
                "path": meta.get("path", ""),
                "meta": meta,
                "title": meta["title"],
                "duration": meta["duration"],
                "loop": bool(_cfg.get("loop", False)),
                "notify_chat": msg.chat_id,
            }

        _spawn(_job_dial())
        return

    # ---- Active Call Playback & Controls
    active_cid = ctx.calls.latest_active_call_id()

    if cmd == "__auto__":
        if not active_cid:
            async def _job_lookup():
                try:
                    if _is_yt_ref(rest):
                        vid = _extract_video_id(rest)
                        info = await asyncio.to_thread(_yt().extract, vid, max_kbps=int(_cfg.get("max_kbps", 160)))
                        return await ctx.reply(
                            msg,
                            f"🎵 *{info.title}* (`{_fmt_dur(info.duration)}`)\n"
                            f"No active call right now.\n"
                            f"• `!yt playcall {vid}` — Call this chat & stream live\n"
                            f"• `!yt dial me {vid}` — Call yourself & stream live"
                        )
                    hits = await asyncio.to_thread(_yt().search, rest, 5)
                    if not hits:
                        return await ctx.reply(msg, f"🔍 No results found for `{rest}`.")
                    _LAST_SEARCH[msg.chat_id] = hits
                    lines = [f"🔍 *YouTube Results for `{rest}`* (no active call):"]
                    for i, h in enumerate(hits, 1):
                        lines.append(f"`#{i}` *{h['title']}* (`{h['duration_text']}`)")
                    lines.append("\nRun `!yt playcall #1` or `!yt dial me #1` to start a call with it!")
                    await ctx.reply(msg, "\n".join(lines))
                except Exception as e:
                    log.exception("yt lookup failed")
                    await ctx.reply(msg, f"❌ YouTube lookup failed: {e}")

            _spawn(_job_lookup())
            return
        cmd = "play"

    if cmd == "play":
        if not rest:
            return await ctx.reply(msg, "⚠️ Usage: `!yt play <url|id|song|#N>`")
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call. Use `!yt playcall <song>` or `!yt dial <num|me> <song>`!")
        await ctx.reply(msg, f"⏳ Starting YouTube stream `{rest}`...")

        async def _job_play(cid: str):
            try:
                play_res = await _play_yt_audio_entry(ctx, cid, msg.chat_id, rest, track_for_loop=True)
            except Exception as e:
                log.exception("yt play failed")
                return await ctx.reply(msg, f"❌ Failed: {e}")
            if play_res.get("ok"):
                m = play_res["meta"]
                badge = "📡 Live PCMStream" if play_res.get("live") else "🎵 Cached WAV"
                return await ctx.reply(
                    msg,
                    f"▶️ *Now Playing ({badge}):* {m['title']} (`{_fmt_dur(m['duration'])}`)"
                )
            await ctx.reply(msg, f"❌ Playback failed: {play_res.get('error')}")

        _spawn(_job_play(active_cid))
        return

    if cmd == "queue":
        if not rest:
            return await ctx.reply(msg, "⚠️ Usage: `!yt queue <url|id|song|#N>`")
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call to queue into.")

        cur_state = ctx.calls._active.get(active_cid, {}).get("audio_state", "idle")
        if cur_state == "idle" and not _YT_QUEUES.get(active_cid):
            await ctx.reply(msg, f"⏳ Starting YouTube stream `{rest}`...")

            async def _job_q_now(cid: str):
                try:
                    play_res = await _play_yt_audio_entry(ctx, cid, msg.chat_id, rest, track_for_loop=False)
                except Exception as e:
                    log.exception("yt queue play failed")
                    return await ctx.reply(msg, f"❌ Queue play failed: {e}")
                if play_res.get("ok"):
                    m = play_res["meta"]
                    badge = "📡 Live PCMStream" if play_res.get("live") else "🎵 Cached WAV"
                    return await ctx.reply(msg, f"▶️ *Started ({badge}):* {m['title']} (`{_fmt_dur(m['duration'])}`)")
                await ctx.reply(msg, f"❌ Error: {play_res.get('error')}")

            _spawn(_job_q_now(active_cid))
            return

        q_list = _YT_QUEUES.setdefault(active_cid, [])
        item = {
            "query": rest,
            "title": rest,
            "duration": 0,
            "meta": None,
            "error": "",
            "notify_chat": msg.chat_id,
            "ready_event": asyncio.Event(),
        }
        q_list.append(item)
        pos = len(q_list)

        async def _bg_prepare(entry: dict, chat_id: str, q_str: str):
            try:
                fn = _resolve_live_stream_sync if _supports_live_pcm(ctx) else _prepare_yt_media_sync
                meta = await asyncio.to_thread(fn, chat_id, q_str)
                entry["title"] = meta["title"]
                entry["duration"] = meta["duration"]
                entry["meta"] = meta
            except Exception as exc:
                entry["error"] = str(exc)
            finally:
                entry["ready_event"].set()

        _spawn(_bg_prepare(item, msg.chat_id, rest))
        return await ctx.reply(msg, f"📋 Added `{rest}` to YT Queue (`#{pos}` — resolving in background).")

    if cmd == "q":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        q_list = _YT_QUEUES.get(active_cid, [])
        if not q_list:
            return await ctx.reply(msg, "📭 YT Queue is empty. Add tracks via `!yt queue <song>`.")
        lines = ["📋 *Active YT DJ Queue:*"]
        for i, it in enumerate(q_list, 1):
            st = "✅ ready" if it.get("meta") else ("❌ " + it["error"] if it["error"] else "⏳ resolving...")
            lines.append(f"`#{i}` {it['title']} _({st})_")
        return await ctx.reply(msg, "\n".join(lines))

    if cmd == "skip":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        q_list = _YT_QUEUES.get(active_cid, [])
        if not q_list:
            _ACTIVE_LIVE_TRACK.pop(active_cid, None)
            await ctx.stop_call_audio(active_cid)
            return await ctx.reply(msg, "⏹️ YT queue empty — stopped playback.")
        nxt = q_list.pop(0)

        async def _job_skip(cid: str, entry: dict):
            if not entry["ready_event"].is_set():
                await ctx.reply(msg, f"⏳ Finishing lookup for `{entry['query']}`...")
                await entry["ready_event"].wait()
            if cid not in ctx.calls._active:
                return
            if entry.get("error"):
                return await ctx.reply(msg, f"❌ Next track failed: {entry['error']}")
            try:
                play_res = await _play_yt_audio_entry(
                    ctx, cid, msg.chat_id, entry.get("meta") or entry["query"], track_for_loop=False
                )
            except Exception as e:
                play_res = {"ok": False, "error": str(e)}
            if play_res.get("ok"):
                m = play_res["meta"]
                badge = "📡 Live" if play_res.get("live") else "🎵 Cached"
                return await ctx.reply(msg, f"⏭️ *Skipped to ({badge}):* {m['title']} (`{_fmt_dur(m['duration'])}`)")
            await ctx.reply(msg, f"❌ Skip failed: {play_res.get('error')}")

        _spawn(_job_skip(active_cid, nxt))
        return

    if cmd == "pause":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        res = await ctx.pause_call_audio(active_cid)
        return await ctx.reply(msg, "⏸️ Audio paused." if res.get("ok") else f"❌ {res.get('error')}")

    if cmd == "resume":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        res = await ctx.resume_call_audio(active_cid)
        return await ctx.reply(msg, "▶️ Audio resumed." if res.get("ok") else f"❌ {res.get('error')}")

    if cmd == "stop":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        _YT_QUEUES.pop(active_cid, None)
        _ACTIVE_LIVE_TRACK.pop(active_cid, None)
        await ctx.stop_call_video(active_cid)
        res = await ctx.stop_call_audio(active_cid)
        return await ctx.reply(msg, "⏹️ Playback stopped." if res.get("ok") else f"❌ {res.get('error')}")

    if cmd == "hangup":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        _ACTIVE_LIVE_TRACK.pop(active_cid, None)
        res = await ctx.hangup_call(active_cid)
        return await ctx.reply(msg, "📴 Call ended." if res.get("ok") else f"❌ {res.get('error')}")

    # ---- Owner configuration
    if not is_owner:
        return await ctx.reply(msg, "❌ Only bot owner can change settings.")
    sender_num = _num(msg.sender_id)
    if sender_num and sender_num not in _cfg.setdefault("owners", []):
        _cfg["owners"].append(sender_num)

    if cmd == "mode":
        m = rest.lower()
        if m in ("stream", "live", "pcm"):
            _cfg["stream_mode"] = True
            _save()
            return await ctx.reply(msg, "📡 Playback mode set to *Live PCMStream* (zero full-file download).")
        if m in ("cache", "download", "wav"):
            _cfg["stream_mode"] = False
            _save()
            return await ctx.reply(msg, "💾 Playback mode set to *Cache WAV* (downloads full file first).")
        return await ctx.reply(msg, "⚠️ Usage: `!yt mode <stream|cache>`")

    if cmd == "loop":
        val = rest.lower() in ("on", "1", "true", "yes")
        _cfg["loop"] = val
        _save()
        return await ctx.reply(msg, f"🔁 YT loop playback: *{'ON' if val else 'OFF'}*")

    if cmd == "quality":
        m = rest.lower()
        if m in ("fast", "low", "lite", "data"):
            _cfg["max_kbps"] = 80
            _save()
            return await ctx.reply(msg, "⚡ Audio quality set to *FAST* (`<=80 kbps` — smaller files, quicker start).")
        if m in ("hq", "high", "best"):
            _cfg["max_kbps"] = 160
            _save()
            return await ctx.reply(msg, "🎧 Audio quality set to *HQ* (`<=160 kbps`).")
        return await ctx.reply(msg, "⚠️ Usage: `!yt quality <hq|fast>`")

    if cmd == "maxmins":
        try:
            mins = max(0, min(300, int(rest)))
        except ValueError:
            return await ctx.reply(msg, "⚠️ Usage: `!yt maxmins <0..300>` (`0` disables limit)")
        _cfg["max_minutes"] = mins
        _save()
        return await ctx.reply(msg, f"⏱️ Max audio duration set to: `{mins}m`" if mins else "⏱️ Max audio limit disabled.")

    if cmd == "public":
        val = rest.lower() in ("on", "1", "true", "yes")
        _cfg["public_dj"] = val
        _save()
        return await ctx.reply(msg, f"🎛️ Public YT DJ commands: *{'ON' if val else 'OFF'}*")
