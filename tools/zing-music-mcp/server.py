#!/usr/bin/env python3
"""
XiaoZhi Music MCP Server — Preload to disk + Cloud Stream Proxy.

Kiến trúc:
  - MCP tool `search_song(keyword)`: tìm bài trên YouTube (yt-dlp, có fallback
    scrape HTML khi bị anti-bot).
  - MCP tool `get_song_url(title)`: trả NGAY stream URL công khai
    `<PUBLIC_BASE>/stream/<id>.mp3` (id = md5(title)) và START preload nền:
    resolve -> tải source (chunk 1 MB, có Range/resume) -> transcode MP3 ->
    ghi file `{key}.mp3` vào MUSIC_CACHE_DIR (mặc định <tmp>/zing-music).
  - HTTP `GET /stream/{id}.mp3`:
      * file đã có  -> FileResponse (Content-Length; đọc lại được, kể cả
        sau khi server restart).
      * chưa có     -> trả 200 ngay + silence primer (MP3 im cùng thông số,
        8 KB, pace ~real-time) giữ kết nối trong khi preload chạy, rồi chuyển sang
        toàn bộ file (decoder nhận MP3 liên tục, không đứt giữa chừng).
      * preload lỗi / quá 240 s -> fallback live ffmpeg pipe (hành vi cũ).
    Chuỗi convert: nguồn -> PCM 24 kHz mono -> MP3 (mặc định 160 kbps; xem
    AUDIO_* bên dưới để chỉnh mà không cần sửa code).

Server gộp MCP + HTTP stream vào MỘT process (một cổng duy nhất):
  - MCP streamable-http: endpoint POST /mcp  (mặc định — deploy cloud/Render)
  - MCP stdio:           MCP_TRANSPORT=stdio (chế độ cũ, chạy qua mcp_pipe.py)
  - HTTP stream:         GET /stream/{id}.mp3 + GET /health (cùng cổng MCP)

Cấu hình qua biến môi trường:
  PORT          — cổng HTTP (Render tự set). Mặc định 8080.
  MCP_TRANSPORT — streamable-http (mặc định) | stdio
  PUBLIC_BASE   — base URL công khai cho stream URL (vd https://xxx.onrender.com).
                  Bỏ trống thì tự dùng http://<IP-LAN>:<PORT> (chạy laptop dev).
  YTDLP_COOKIES_B64 — base64 của cookies.txt Netscape cho yt-dlp (chống
                  "Sign in to confirm you're not a bot" khi deploy IP datacenter).
                  Xem README để biết cách export cookies.
  YTDLP_PROXY    — proxy tùy chọn cho yt-dlp (vd http://user:pass@host:port).
  YTDLP_PLAYER_CLIENTS — ladder player client của YouTubeExtractor; các tier
                  cách nhau bằng "|", client trong tier cách nhau bằng ",";
                  tier rỗng = để yt-dlp dùng client mặc định. Mặc định:
                  "|visionos,tv,web_embedded|mweb,tv_simply,web|android_vr,android,ios"
  YTDLP_DEBUG    — "1" để in cả message [debug] của yt-dlp
                  (mặc định: info/warn/err — warning theo client là manh mối
                  chính để chẩn đoán bot-check).

Chạy: python server.py
"""

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yt_dlp
from fastapi import FastAPI, HTTPException
from starlette.routing import Mount
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.requests import Request

from mcp.server.fastmcp import FastMCP

PORT = int(os.environ.get("PORT", "8080"))
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "streamable-http").strip().lower()
if MCP_TRANSPORT not in ("streamable-http", "stdio"):
    sys.stderr.write(
        f"[music] MCP_TRANSPORT='{MCP_TRANSPORT}' không hợp lệ, dùng "
        "'streamable-http'. Hợp lệ: streamable-http, stdio\n")
    MCP_TRANSPORT = "streamable-http"
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "").rstrip("/")
FFMPEG = shutil.which("ffmpeg")  # Dockerfile cài qua apt; dev có imageio fallback
if not FFMPEG:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# Chẩn đoán môi trường (stderr — an toàn với MCP stdio, hiện trong log Render):
# deno là JS runtime cho yt-dlp; thiếu nó YouTube drop formats -> lỗi
# "Requested format is not available".
sys.stderr.write(
    f"[music] yt-dlp={yt_dlp.version.__version__}, "
    f"deno={shutil.which('deno') or 'NOT FOUND'}\n")

# ---------------------------------------------------------------------------
# Chuỗi chất lượng âm thanh gửi xuống robot (env-tunable, không cần sửa code).
#
# AUDIO_SAMPLE_RATE / AUDIO_CHANNELS phải KHỚP board, để ESP32 không phải
# resample thêm lần nữa (bread-compact-wifi: AUDIO_OUTPUT_SAMPLE_RATE = 24000,
# loa mono). Convert 1 lần ở server chất lượng cao luôn tốt hơn để ESP32
# resample bằng esp_ae_rate_cvt (perf_type SPEED, complexity 2).
#
# AUDIO_BITRATE: 96k -> 160k. Ở 24 kHz MP3 là MPEG-2 LSF nên libmp3lame KẸP
# TRẦN ở 160 kbps (xin 192k cũng chỉ ra 160k) — 160k là mức cao nhất có thể.
# Lưu ý: bitrate KHÔNG mở rộng được dải tần. Đo thực tế cho thấy 96/128/160k
# đều bị cắt như nhau từ ~11 kHz trở lên, vì trần đó do sample rate 24 kHz
# (Nyquist 12 kHz) chứ không do bitrate. Tăng 128k -> 160k chỉ giảm artifact
# (méo lượng tử, pre-echo), không làm nhạc "sáng" hơn. Băng thông 16 -> 20 KB/s.
#
# AUDIO_PRESET: bộ lọc DSP bù trừ loa nhỏ (xem AUDIO_PRESETS). Đây là thứ thay
# đổi âm sắc nghe được rõ nhất. Đổi preset rồi restart là nghe khác ngay.
#   speaker (mặc định) - bù trừ loa nhỏ: cắt sub-bass vô ích, nhấn 200 Hz cho
#                        ấm, giảm 800 Hz bớt "hộp", nhấn 3.2 kHz cho rõ tiếng
#   flat               - chỉ cắt sub-bass + chống clip, gần như nguyên bản
#   warm               - nhiều bass hơn (nhấn 200 Hz +6 dB)
#   bright             - nhiều treble hơn (nhấn 3.5 kHz +4 dB, 10 kHz +3 dB)
#   loud               - loudnorm (to nhỏ đều giữa các bài) + EQ speaker
#   none               - không DSP (thô nhất, dễ rè nhất vì bass sâu làm loa rung)
#
# AUDIO_FILTERS: chuỗi ffmpeg -af tuỳ ý, ĐÈ preset nếu được set. Đặt "" để tắt
# hoàn toàn DSP (tương đương AUDIO_PRESET=none).
# ---------------------------------------------------------------------------
AUDIO_SAMPLE_RATE = int(os.environ.get("AUDIO_SAMPLE_RATE", "24000"))
AUDIO_CHANNELS = os.environ.get("AUDIO_CHANNELS", "1")
AUDIO_BITRATE = os.environ.get("AUDIO_BITRATE", "160k")

# Bù trừ loa nhỏ dùng chung cho preset `speaker` và `loud`.
# Đo đáp tuyến thật của chuỗi này (48 kHz stereo -> 24 kHz mono):
#   50 Hz -12 dB | 90 Hz -2.5 dB | 200 Hz +3.5 dB | 800 Hz -2.5 dB
#   3.2 kHz +2.5 dB | 10 kHz +1.5 dB | 11 kHz  0 dB
_SPEAKER_EQ = (
    "highpass=f=90,"
    "equalizer=f=200:t=q:w=1.0:g=3.5,"
    "equalizer=f=800:t=q:w=1.2:g=-2.5,"
    "equalizer=f=3200:t=q:w=1.4:g=2.5,"
    "treble=g=1.5:f=10000:w=0.7"
)
_SPEAKER_LIMIT = "alimiter=limit=0.841:level=disabled"  # -1.5 dBFS

AUDIO_PRESETS = {
    "speaker": f"{_SPEAKER_EQ},{_SPEAKER_LIMIT}",
    "flat": "highpass=f=90,alimiter=limit=0.891:level=disabled",
    "warm": (
        "highpass=f=80,"
        "equalizer=f=200:t=q:w=0.9:g=6,"
        "equalizer=f=800:t=q:w=1.2:g=-2.5,"
        "equalizer=f=3200:t=q:w=1.4:g=2.5,"
        "treble=g=1.5:f=10000:w=0.7,"
        f"{_SPEAKER_LIMIT}"),
    "bright": (
        "highpass=f=100,"
        "equalizer=f=220:t=q:w=1.0:g=2,"
        "equalizer=f=800:t=q:w=1.2:g=-3,"
        "equalizer=f=3500:t=q:w=1.5:g=4,"
        "treble=g=3:f=10000:w=0.7,"
        f"{_SPEAKER_LIMIT}"),
    "loud": f"loudnorm=I=-16:TP=-1.5:LRA=11,{_SPEAKER_EQ},{_SPEAKER_LIMIT}",
    "none": "",
}

AUDIO_PRESET = os.environ.get("AUDIO_PRESET", "speaker").strip().lower()
if AUDIO_PRESET not in AUDIO_PRESETS:
    sys.stderr.write(
        f"[music] AUDIO_PRESET='{AUDIO_PRESET}' không hợp lệ, dùng 'speaker'. "
        f"Hợp lệ: {', '.join(AUDIO_PRESETS)}\n")
    AUDIO_PRESET = "speaker"

# AUDIO_FILTERS (nếu set) đè preset. Phân biệt "chưa set" và "set rỗng".
AUDIO_FILTERS = os.environ.get("AUDIO_FILTERS")
if AUDIO_FILTERS is None:
    AUDIO_FILTERS = AUDIO_PRESETS[AUDIO_PRESET]
else:
    AUDIO_PRESET = "custom(AUDIO_FILTERS)"

# ---------------------------------------------------------------------------
# YouTube anti-bot: cookies + proxy (env-tunable)
# ---------------------------------------------------------------------------
# IP datacenter (Render) thường bị YouTube gắn cờ -> lỗi
# "Sign in to confirm you're not a bot" ở bước _resolve (search extract_flat
# vẫn chạy được). Cách chính thức theo yt-dlp: gửi cookies Netscape:
#   YTDLP_COOKIES_B64 — base64 của cookies.txt export từ trình duyệt đang
#                       đăng nhập YouTube (cách export xem README).
#   YTDLP_PROXY       — proxy tùy chọn, vd http://user:pass@host:port.
COOKIES_FILE = None
_cookies_b64 = os.environ.get("YTDLP_COOKIES_B64", "").strip()
# (Thieu cookies -> khong warn o day vi chua biet POT_AVAILABLE; dong canh bao
# duoc in SAU block bgutil PO-token duoi day khi da ro ca hai nguon auth.)
if _cookies_b64:
    try:
        # Decode -> bytes THÔ rồi ghi binary ("wb") — tuyệt đối KHÔNG
        # .decode('utf-8') rồi ghi text mode 'w': giữ nguyên từng byte
        # (tab, \r\n) và không bị newline translation đổi bytes.
        _data = base64.b64decode(_cookies_b64)
        # Gỡ UTF-8 BOM (EF BB BF) nếu có: http.cookiejar mở file ở TEXT
        # mode nên BOM thành ký tự U+FEFF bám trước "# Netscape" ->
        # NETSCAPE_MAGIC_RGX không match -> LoadError
        # "does not look like a Netscape format cookies file".
        if _data.startswith(b"\xef\xbb\xbf"):
            _data = _data[3:]
            sys.stderr.write("[music] cookies: stripped UTF-8 BOM\n")
        # Validate đúng regex magic của http.cookiejar NGAY lúc boot
        # (fail-fast trung thực — /health "cookies" chỉ true khi file
        # đọc được thật; lý do nằm trong log Render thay vì yt-dlp fail
        # về sau với thông báo mơ hồ):
        _head = _data.split(b"\n", 1)[0]
        if not re.match(rb"#( Netscape)? HTTP Cookie File", _head):
            raise ValueError(
                "dòng đầu không phải Netscape magic: %r" % (_head[:60],))
        COOKIES_FILE = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
        with open(COOKIES_FILE, "wb") as _f:
            _f.write(_data)
        _auth_names = (
            "SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO",
            "__Secure-1PSID", "__Secure-3PSID", "__Secure-3PAPISID")
        _auth = set()
        for _line in _data.decode("utf-8", "replace").splitlines():
            _c = _line.split("\t")
            if len(_c) >= 7 and _c[5] in _auth_names:
                _auth.add(_c[5])
        sys.stderr.write(
            f"[music] cookies: Netscape OK ({len(_data)} bytes, "
            f"{len(_auth)}/9 auth) -> {COOKIES_FILE}\n")
        if not _auth:
            sys.stderr.write(
                "[music] WARNING: cookies khong co auth cookie "
                "(SID/SAPISID/LOGIN_INFO) - export khi chua dang nhap "
                "YouTube? Se van bi bot-check/reload.\n")
        sys.stderr.flush()
    except Exception as e:
        COOKIES_FILE = None
        sys.stderr.write(f"[music] YTDLP_COOKIES_B64 rejected: {e}\n")
        sys.stderr.flush()

# ---------------------------------------------------------------------------
# bgutil PO-token provider — giải pháp lâu dài cho bot-check: plugin pip
# đăng ký provider bgutil:http với yt-dlp; start.sh boot server POT chạy
# nền tại 127.0.0.1:4416. Với PO token thì cookies chỉ là phương án dự
# phòng (không còn phải export lại khi Google revoke session).
# ---------------------------------------------------------------------------
POT_AVAILABLE = False
try:
    from importlib import metadata as _plg_md
    _pot_v = _plg_md.version("bgutil-ytdlp-pot-provider")
    POT_AVAILABLE = True
    sys.stderr.write(
        f"[music] bgutil PO-token plugin {_pot_v} OK -> yt-dlp lay PO token "
        "tu http://127.0.0.1:4416 (start.sh)\n")
except Exception:
    sys.stderr.write(
        "[music] WARNING: bgutil PO-token plugin chua cai — khong co PO token, "
        "IP datacenter van bi bot-check (xem requirements.txt)\n")
sys.stderr.flush()


def _pot_server_up(timeout: float = 2.0) -> bool:
    """Probe POT server (start.sh chay nen truoc khi python start).

    POT_AVAILABLE chi noi plugin pip da cai; neu deno bi OOM-kill sau boot
    thi provider that bai IM LAP (no_warnings=True) va bot-check van xay ra.
    Chu the pot_srv=up/down trong log give-up / "pot_server" trong /health
    phan biet ro 2 truong hop nay.
    """
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:4416/ping", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


if POT_AVAILABLE:
    # Correlate voi log "[start] ... ready/WARNING" cua start.sh.
    sys.stderr.write(
        "[music] bgutil POT server /ping khi boot: "
        f"{'up' if _pot_server_up() else 'DOWN — xem log [start] cua start.sh'}\n")
    sys.stderr.flush()

if not _cookies_b64 and not POT_AVAILABLE:
    # Khong co cookies VA khong co PO token -> bot-check chac chan chan;
    # thieu mot trong hai thi nguon kia (POT hoac cookies) dam nhan.
    sys.stderr.write(
        "[music] WARNING: YTDLP_COOKIES_B64 not set VA khong co PO token — "
        "YouTube bot-check se chan (xem README: cai bgutil hoac dat "
        "cookies; /health hien cookies=false, pot=false)\n")
    sys.stderr.flush()

YTDLP_PROXY = os.environ.get("YTDLP_PROXY", "").strip()


# YTDLP_DEBUG=1 -> in ca message [debug]; mac dinh chi info/warn/err.
YTDLP_DEBUG = os.environ.get("YTDLP_DEBUG", "").strip().lower() in (
    "1", "true", "yes", "on")


class _YtdlpLogger:
    """Bơm message của yt-dlp ra stderr để hiện trong log Render.

    Trước đây opts đặt no_warnings=True -> mất TOÀN BỘ warning theo từng client
    ("Sign in to confirm you're not a bot", "<client> formats require a GVS PO
    Token which was not provided", "Skipping unsupported client") nên log chỉ
    còn 1 dòng resolve give-up chung, không biết hỏng ở client nào. yt-dlp thử
    tiếp client kế tiếp khi client trước lỗi (extractor/youtube/_video.py:
    ExtractorError -> report_warning + continue) nên các warning này là manh mối
    DUY NHẤT để phân biệt "IP bị gắn cờ" với "thiếu PO token".
    """

    def __init__(self, verbose: bool = False):
        self._verbose = verbose

    def _emit(self, level: str, msg: str) -> None:
        sys.stderr.write(f"[ytdlp:{level}] {msg}\n")
        sys.stderr.flush()

    def debug(self, msg):
        """yt-dlp gửi message [debug] ở đây; message thường cũng đi qua đây."""
        text = str(msg)
        if text.startswith("[debug] "):
            if self._verbose:
                self._emit("debug", text[8:])
        else:
            self._emit("info", text)

    def info(self, msg):
        self._emit("info", str(msg))

    def warning(self, msg):
        self._emit("warn", str(msg))

    def error(self, msg):
        self._emit("err", str(msg))


_YTDLP_LOGGER = _YtdlpLogger(YTDLP_DEBUG)


def _apply_ydl_auth(opts: dict) -> dict:
    """Gắn cookiefile/proxy/logger vào opts yt-dlp nếu env tương ứng có set.

    no_warnings luôn = False: warning theo từng client là dữ liệu chẩn đoán
    bot-check, không được ẩn (xem _YtdlpLogger)."""
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if YTDLP_PROXY:
        opts["proxy"] = YTDLP_PROXY
    opts["no_warnings"] = False
    opts["logger"] = _YTDLP_LOGGER
    return opts


mcp = FastMCP("music-server", host="0.0.0.0", port=PORT)


# ---------------------------------------------------------------------------
# Tìm kiếm YouTube (yt-dlp + fallback scrape HTML)
# ---------------------------------------------------------------------------
def _parse_duration(text: str):
    if not text:
        return None
    try:
        sec = 0
        for p in text.split(":"):
            sec = sec * 60 + int(p)
        return sec
    except ValueError:
        return None


def _search_youtube_html(query: str, n: int) -> list:
    """Fallback scrape trang results của YouTube khi yt-dlp bị anti-bot."""
    url = ("https://www.youtube.com/results?search_query="
           + urllib.parse.quote(query) + "&sp=EgIQAQ%253D%253D")
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
        "Accept-Language": "vi,en;q=0.8",
    })
    html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", html)
    if not m:
        return []
    data = json.loads(m.group(1))
    results = []

    def walk(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if v:
                vid = v.get("videoId")
                title = "".join(r.get("text", "")
                                for r in (v.get("title") or {}).get("runs", []))
                owner_runs = (v.get("ownerText") or {}).get("runs", [{}])
                dur_text = (v.get("lengthText") or {}).get("simpleText")
                if vid and title:
                    results.append({
                        "title": title,
                        "uploader": owner_runs[0].get("text") if owner_runs else None,
                        "duration_sec": _parse_duration(dur_text),
                        "youtube_url": "https://www.youtube.com/watch?v=" + vid,
                    })
            for val in o.values():
                walk(val)
        elif isinstance(o, list):
            for val in o:
                walk(val)

    walk(data)
    return results[:n]


def _search_youtube(query: str, n: int) -> list:
    opts = {
        "quiet": True,
        "extract_flat": True,
        "default_search": f"ytsearch{n}",
    }
    _apply_ydl_auth(opts)
    entries = []
    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
            entries = info.get("entries") or []
            if entries:
                break
        except Exception:
            entries = []
        time.sleep(1 + attempt)
    if not entries:
        try:
            return _search_youtube_html(query, n)
        except Exception:
            return []
    out = []
    for e in entries:
        if not e:
            continue
        out.append({
            "title": e.get("title"),
            "uploader": e.get("uploader") or e.get("channel"),
            "duration_sec": e.get("duration"),
            "youtube_url": e.get("url") or e.get("webpage_url"),
        })
    return out


# ---------------------------------------------------------------------------
# Resolve direct URL googlevideo (cache RAM)
# ---------------------------------------------------------------------------
def _key_of(title: str) -> str:
    return hashlib.md5(title.strip().lower().encode()).hexdigest()[:10]


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _stream_url(key: str) -> str:
    base = PUBLIC_BASE or f"http://{_lan_ip()}:{PORT}"
    return f"{base}/stream/{key}.mp3"


# ---------------------------------------------------------------------------
# Ladder player client (env YTDLP_PLAYER_CLIENTS)
# ---------------------------------------------------------------------------
# yt-dlp chi thu client ke tiep khi client truoc loi, va muc "client nay co can
# PO token khong" nam trong INNERTUBE_CLIENTS[...]['GVS_PO_TOKEN_POLICY'].
# Voi ban yt-dlp dang pin (xem requirements.txt):
#   visionos / tv / web_embedded -> KHONG can PO token
#   mweb / tv_simply / web       -> can token web (bgutil sinh duoc)
#   android_vr / android / ios   -> can token Android/iOS (bgutil KHONG sinh duoc)
# Nen ladder mac dinh: tier dau RONG = de yt-dlp tu chon client (voi ban dang pin
# la visionos+web, co POT) -> nhom token-free -> nhom web+POT -> nhom android/ios
# (chi huu ich khi co token/cookies phu hop). Cac tier sau KHONG lap lai client
# da thu o tier truoc (vong lap trong _resolve tu bo).
_DEFAULT_CLIENT_LADDER = (
    "|tv,web_embedded,tv_downgraded|mweb,tv_simply,web|android_vr,android,ios")
_CLIENT_LEVEL_MARKERS = (
    "not a bot", "Sign in to confirm", "needs to be reloaded",
    "Failed to extract any player response",
    "All player responses are invalid",
)


def _parse_client_ladder(raw: str) -> tuple:
    """'a,b|' -> (('a', 'b'), ()). Tier rong = dung client mac dinh cua yt-dlp."""
    tiers = []
    for chunk in str(raw or "").split("|"):
        tiers.append(tuple(c.strip() for c in chunk.split(",") if c.strip()))
    return tuple(tiers) or ((),)


def _is_client_level_err(err) -> bool:
    """Loi o muc client (bot-check / thieu token) -> nhay sang tier client ke
    tiep; loi khac (video unavailable, HTTP...) thi noi len ngay."""
    text = str(err)
    return any(m in text for m in _CLIENT_LEVEL_MARKERS)


_CLIENT_TIERS = _parse_client_ladder(
    os.environ.get("YTDLP_PLAYER_CLIENTS", _DEFAULT_CLIENT_LADDER))


# ---------------------------------------------------------------------------
# Probe direct URL truoc khi coi resolve la thanh cong
# ---------------------------------------------------------------------------
# yt-dlp co the tra ve URL ma googlevideo se 403 khi tai that (format can GVS
# PO token khong duoc cap, hoac IP bi chan theo tung client). Khong thu thi
# robot chi nhan duoc silence primer roi im -> dung trieu chung "link chay
# nhung khong co tieng". Probe bang 1 Range nho, dung headers/proxy nhu luc tai.
_PROBE_ENABLED = os.environ.get(
    "YTDLP_FORMAT_PROBE", "1").strip().lower() not in ("0", "false", "no", "off")
_PROBE_BYTES = 64 * 1024
_PROBE_TIMEOUT = 15
_PROBE_REJECT_CODES = (400, 401, 403, 404, 410)


def _probe_direct_url(entry: dict) -> bool:
    """True neu URL tai duoc. Chi ket luan 'khong tai duoc' khi HTTP tra ve
    _PROBE_REJECT_CODES; loi mang/timeout -> khong ket luan, van dung (tranh
    loai oan URL chi vi probe chap chon)."""
    if not _PROBE_ENABLED:
        return True
    url = (entry or {}).get("direct_url")
    if not url:
        return False
    headers = dict(entry.get("http_headers") or {})
    for drop in ("Range", "If-Range", "Content-Length"):
        headers.pop(drop, None)
    headers["Accept-Encoding"] = "identity"
    headers["Range"] = f"bytes=0-{_PROBE_BYTES - 1}"
    req = urllib.request.Request(url, headers=headers)
    if YTDLP_PROXY:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": YTDLP_PROXY, "https": YTDLP_PROXY}))
    else:
        opener = urllib.request.build_opener()
    t0 = time.time()
    try:
        with opener.open(req, timeout=_PROBE_TIMEOUT) as r:
            status = getattr(r, "status", None) or r.getcode()
            data = r.read(4096)
        sys.stderr.write(
            f"[music] format probe OK ({status}, {len(data)} B, "
            f"{time.time() - t0:.1f}s)\n")
        sys.stderr.flush()
        return bool(data)
    except urllib.error.HTTPError as e:
        reject = e.code in _PROBE_REJECT_CODES
        reason = ("URL khong tai duoc" if reject
                  else "khong ket luan, van dung")
        sys.stderr.write(f"[music] format probe HTTP {e.code} ({reason})\n")
        sys.stderr.flush()
        return not reject
    except Exception as e:  # noqa: BLE001 - probe khong duoc lam chet resolve
        sys.stderr.write(
            f"[music] format probe loi ({e}) -> khong ket luan, van dung\n")
        sys.stderr.flush()
        return True


def _entry_from_info(title: str, info: dict) -> dict:
    """Chon format tot nhat tu info cua yt-dlp -> entry (direct_url + headers)."""
    chosen = info["entries"][0] if "entries" in info else info
    direct = chosen.get("url")
    if not direct:
        with_url = [f for f in (chosen.get("formats") or []) if f.get("url")]
        # Ưu tiên audio-only: fmts[-1] là cao nhất nhưng có thể là VIDEO-only
        # (âm thanh im lặng) — chỉ lấy video khi không còn format âm thanh nào.
        audio_only = [
            f for f in with_url
            if f.get("vcodec") in (None, "none")
            and f.get("acodec") not in (None, "none")]
        pick = audio_only[-1] if audio_only else (
            with_url[-1] if with_url else None)
        if pick is None:
            raise RuntimeError("không lấy được direct stream URL")
        direct = pick["url"]
    # '__yt_dlp_client' = client Innertube đã sinh ra format này (yt-dlp gắn vào
    # từng format). Có trường hợp format được chọn không mang key này (nhánh
    # HLS/m3u8) -> lấy thêm danh sách client thấy trong toàn bộ formats.
    clients_seen = sorted(
        {f.get("__yt_dlp_client") for f in (info.get("formats") or [])
         if f.get("__yt_dlp_client")})
    return {
        "direct_url": direct,
        "title": chosen.get("title") or title,
        "uploader": chosen.get("uploader") or chosen.get("channel"),
        "duration": chosen.get("duration"),
        "client": (chosen.get("__yt_dlp_client")
                   or info.get("__yt_dlp_client")
                   or (clients_seen[0] if len(clients_seen) == 1 else "")
                   or "n/a"),
        "clients_seen": clients_seen,
        "format_id": chosen.get("format_id") or "",
        # Headers yt-dlp dùng cho format này (UA android/ios/tv...) — _ffmpeg_chunks
        # replay để googlevideo không 403.
        "http_headers": (chosen.get("http_headers")
                         or info.get("http_headers") or {}),
        "expires": time.time() + _RESOLVE_TTL,
    }


def _resolve(key: str, title: str) -> dict:
    with _lock:
        cached = _resolved.get(key)
    if cached and cached.get("expires", 0) > time.time():
        return cached
    base_opts = {
        "quiet": True,
        # Ưu tiên nguồn tốt hơn để giảm "generation loss" khi encode lại MP3
        # (Opus ~160k / AAC 128k tốt hơn hẳn mp3 128k của YouTube).
        "default_search": "ytsearch1",
        "noplaylist": True,
    }
    # Ladder phòng hờ "Requested format is not available": một số video không
    # khớp selector hẹp (formats bị drop khi thiếu JS runtime / format lạ)
    # -> nới dần bestaudio[abr<=192] -> bestaudio -> best -> default, mới bỏ cuộc.
    _format_tiers = (
        "bestaudio[abr<=192]/bestaudio[abr<=128]/bestaudio/best",
        "bestaudio/best",
        "best",
        None,  # selector mặc định của yt-dlp
    )
    info = None
    last_err = None
    won_via = ""
    tried = []
    entry = None
    attempted = set()
    # Vòng lặp 2 chiều: tier client (rỗng = client mặc định của yt-dlp) ×
    # format selector. Bot-check / thiếu PO token là lỗi mức CLIENT -> đổi
    # format selector không giúp gì, nhảy ngay sang tier client kế tiếp.
    # Client đã thử ở tier trước được bỏ để không lặp lại vô ích.
    for clients in _CLIENT_TIERS:
        label = ",".join(clients) if clients else "default"
        todo = tuple(c for c in clients if c not in attempted)
        if clients and not todo:
            continue
        attempted.update(todo)
        for fmt in _format_tiers:
            opts = dict(base_opts)
            if fmt:
                opts["format"] = fmt
            if clients:
                opts["extractor_args"] = {
                    "youtube": {"player_client": list(todo)}}
            _apply_ydl_auth(opts)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(title, download=False)
                if not info:
                    raise yt_dlp.utils.DownloadError("extract trả về rỗng")
                break
            except yt_dlp.utils.DownloadError as e:
                last_err = e
                if _is_client_level_err(e):
                    sys.stderr.write(
                        f"[music] client tier '{label}' bị chặn: "
                        f"{str(e)[:160]}\n")
                    sys.stderr.flush()
                    break
                if "Requested format is not available" not in str(e):
                    raise  # lỗi khác (unavailable, HTTP...) -> nổi lên ngay
        if info:
            try:
                cand = _entry_from_info(title, info)
            except RuntimeError as e:
                last_err = e
                cand = None
            # URL chưa tải được thật (403...) thì tier này vô dụng: robot chỉ
            # nghe được silence primer -> thử tier client kế tiếp.
            if cand is not None and _probe_direct_url(cand):
                entry = cand
                won_via = label
                break
            if cand is not None:
                sys.stderr.write(
                    f"[music] tier '{label}' cho URL khong tai duoc "
                    f"-> thu tier ke tiep\n")
                sys.stderr.flush()
            info = None
        if label not in tried:
            tried.append(label)
    if entry is not None:
        if entry.get("client") in ("", "n/a"):
            # Nhánh HLS/m3u8 không mang '__yt_dlp_client' -> dùng tên tier làm
            # manh mối client.
            entry["client"] = won_via
        with _lock:
            _resolved[key] = entry
        sys.stderr.write(
            f"[music] resolve OK qua tier '{won_via}' "
            f"(tier đã fail: {', '.join(tried) if tried else 'không'}), "
            f"yt-dlp={yt_dlp.version.__version__}\n")
        sys.stderr.write(
            f"[music] resolved '{entry['title']}' (client={entry['client']}, "
            f"formats_client={entry.get('clients_seen') or 'n/a'}, "
            f"format_id={entry.get('format_id') or 'n/a'})\n")
        sys.stderr.flush()
        return entry
    if entry is None:
        _srv = _pot_server_up() if POT_AVAILABLE else False
        sys.stderr.write(
            f"[music] resolve give-up '{title}': cookies="
            f"{'on' if COOKIES_FILE else 'off'}, proxy="
            f"{'on' if YTDLP_PROXY else 'off'}, "
            f"pot={'on' if POT_AVAILABLE else 'off'}, "
            f"pot_srv={'up' if _srv else ('down' if POT_AVAILABLE else 'n/a')}, "
            f"tier_da_thu={tried or ['default']}, "
            f"last={last_err}\n")
        _hint = ""
        if last_err is not None and any(
                s in str(last_err)
                for s in ("not a bot", "Sign in to confirm")):
            # Bot-check sống sót qua TOÀN BỘ tier client.
            if POT_AVAILABLE and not _srv:
                # Plugin đã cài nhưng server POT không trả lời lúc yt-dlp cần
                # token -> provider thất bại (đã thấy qua dòng [ytdlp:warn]).
                _hint = (
                    " -> POT server DOWN khi yt-dlp cần PO token "
                    "(127.0.0.1:4416 không /ping — nghi deno OOM-kill trên "
                    "512MB): xem log [start], restart service; lặp lại -> "
                    "tăng RAM instance")
            elif POT_AVAILABLE:
                _hint = (
                    " -> PO token có nhưng vẫn bot-check. Đọc các dòng "
                    "[ytdlp:warn] phía trên để biết client nào fail: nếu CẢ "
                    "nhóm không cần token (visionos/tv/web_embedded) cũng "
                    "fail thì IP datacenter đã bị gắn cờ -> cần "
                    "YTDLP_COOKIES_B64 mới hoặc proxy sticky/residential "
                    "sạch; nếu chỉ nhóm cần token fail thì lỗi ở provider "
                    "POT / JS runtime (deno)")
            elif COOKIES_FILE:
                _hint = (
                    " -> cookie YouTube hết hạn/bị revoke: export lại "
                    "cookies.txt rồi cập nhật env YTDLP_COOKIES_B64 "
                    "trên Render (xem README)")
            else:
                _hint = (
                    " -> chưa có PO token/cookies: cài bgutil plugin "
                    "(requirements.txt) hoặc đặt env YTDLP_COOKIES_B64 "
                    "(xem README)")
        raise RuntimeError(
            f"không lấy được format nào cho '{title}' "
            f"(tier_da_thu={tried or ['default']}, "
            f"deno={'yes' if shutil.which('deno') else 'NO'}, "
            f"yt-dlp={yt_dlp.version.__version__}, "
            f"cookies={'on' if COOKIES_FILE else 'off'}, "
            f"proxy={'on' if YTDLP_PROXY else 'off'}, "
            f"pot={'on' if POT_AVAILABLE else 'off'}, "
            f"pot_srv={'up' if _srv else ('down' if POT_AVAILABLE else 'n/a')}): "
            f"{last_err}{_hint}")
# ---------------------------------------------------------------------------
# Unified Server — MCP + HTTP Streaming (gộp chung MỘT Starlette app)
# ---------------------------------------------------------------------------

# Các endpoint HTTP stream của FastAPI; được Mount vào Starlette app của MCP
# ngay bên dưới (mcp._custom_starlette_routes) nên chạy chung một cổng với MCP.
stream_app = FastAPI()


# ---------------------------------------------------------------------------
# Preload: TAI TRUOC bai hat ve dia TRUOC khi phuc vu /stream.
# Ly do: stream ffmpeg pipe tu googlevideo khong co Content-Length/Range/dia,
# upstream rot giua chung -> response het som -> robot doc thieu du lieu, mat
# ket noi va restart bai (hat dut). File tren dia co Content-Length va doc lai
# duoc (firmware reset tu dau van hat duoc toan bo bai).
# ---------------------------------------------------------------------------

_MEDIA_MIN_BYTES = 100_000  # mot bai hop le toi thieu ~100 KB


def _size_of(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _final_path(key: str) -> str:
    return os.path.join(MEDIA_DIR, f"{key}.mp3")


def _part_path(key: str) -> str:
    return os.path.join(MEDIA_DIR, f"{key}.part")


def _src_path(key: str) -> str:
    return os.path.join(MEDIA_DIR, f"{key}.src")


def _preload_state(key: str) -> str:
    with _lock:
        return (_preload.get(key) or {}).get("state", "")


def _preload_state_update(key: str, **fields) -> None:
    with _lock:
        _preload.setdefault(key, {}).update(fields)


def _count_cache_files() -> int:
    try:
        return len([n for n in os.listdir(MEDIA_DIR)
                    if n.endswith(".mp3") and not n.startswith("_")])
    except OSError:
        return 0


def _prune_cache() -> None:
    """Giu toi da _CACHE_MAX_FILES bai (moi bai ~1-12 MB; Render free chi co
    ~1 GB dia). Bo bai cu nhat truoc, khong dot bai dang preload."""
    try:
        names = [n for n in os.listdir(MEDIA_DIR)
                 if n.endswith(".mp3") and not n.startswith("_")]
    except OSError:
        return
    if len(names) <= _CACHE_MAX_FILES:
        return

    def _mtime(n):
        try:
            return os.path.getmtime(os.path.join(MEDIA_DIR, n))
        except OSError:
            return 0.0

    names.sort(key=_mtime)
    for n in names[: len(names) - _CACHE_MAX_FILES]:
        if _preload_state(n[:-4]) == "running":
            continue
        try:
            os.remove(os.path.join(MEDIA_DIR, n))
            sys.stderr.write(f"[music] prune cache: {n}\n")
            sys.stderr.flush()
        except OSError:
            pass


def _fetch_chunked(url: str, headers: dict, dest: str) -> int:
    """Tai source bang chunk 1 MB co Range + resume. Phien bi dong giua chung
    (proxy/googlevideo thuong dong som) -> retry TAI DO bang cung Range, khong
    tai lai tu dau. Server bo qua Range (200 toan bo) -> ghi lai tu byte 0.
    Tra ve tong so byte da ghi (== neu biet total)."""
    hdrs = dict(headers or {})
    for _drop in ("Range", "If-Range", "Content-Length"):
        hdrs.pop(_drop, None)
    hdrs["Accept-Encoding"] = "identity"
    if YTDLP_PROXY:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": YTDLP_PROXY, "https": YTDLP_PROXY}))
    else:
        opener = urllib.request.build_opener()
    t0 = time.time()
    start = 0
    total = None
    empties = 0
    with open(dest, "wb") as out:
        while True:
            if time.time() - t0 > _DL_DEADLINE:
                raise RuntimeError(
                    f"tai qua {_DL_DEADLINE}s (nghi tai {start} byte)")
            h = dict(hdrs)
            h["Range"] = f"bytes={start}-{start + _CHUNK_BYTES - 1}"
            req = urllib.request.Request(url, headers=h)
            status, buf, crange, clen = 0, None, None, None
            last = None
            for attempt in range(_DL_ATTEMPTS):
                try:
                    with opener.open(req, timeout=30) as r:
                        status = getattr(r, "status", None) or r.getcode()
                        crange = r.headers.get("Content-Range")
                        clen = r.headers.get("Content-Length")
                        if status == 200 and start > 0:
                            # Server khong ho tro Range -> doc lai tu dau.
                            sys.stderr.write(
                                "[music] chunked: server bo qua Range, "
                                "tai lai tu byte 0\n")
                            sys.stderr.flush()
                            out.seek(0)
                            out.truncate()
                            start = 0
                            total = None
                        buf = r.read()
                    last = None
                    break
                except Exception as e:  # noqa: BLE001 - retry cung Range
                    last = e
                    time.sleep(1 + attempt)
            if last is not None:
                raise RuntimeError(
                    f"chunk @{start} loi {_DL_ATTEMPTS} lan: {last}")
            if status == 416:
                break  # start >= total: da tai het
            if status not in (200, 206):
                raise RuntimeError(f"HTTP {status} khi tai chunk @{start}")
            if crange and "/" in crange:
                try:
                    _t = crange.rsplit("/", 1)[1]
                    if _t != "*":
                        total = int(_t)
                except ValueError:
                    pass
            if total is None and status == 200 and clen:
                try:
                    total = int(clen)
                except ValueError:
                    pass
            if not buf:
                empties += 1
                if empties >= 3:
                    raise RuntimeError(f"doc rong x3 @{start}")
                continue
            empties = 0
            out.write(buf)
            out.flush()
            start += len(buf)
            if total is not None and start >= total:
                break
    if total is not None and start < total:
        raise RuntimeError(f"thieu byte: {start}/{total}")
    if start < _MEDIA_MIN_BYTES:
        raise RuntimeError(f"source qua nho: {start} byte")
    sys.stderr.write(f"[music] chunked OK {start} byte -> {dest}\n")
    sys.stderr.flush()
    return start


def _download_ffmpeg(url: str, headers: dict, dest: str) -> None:
    """Fallback tai truc tiep bang ffmpeg (nguon m3u8 / chunked loi):
    -reconnect giu ket noi, ghi thang vao file. Cung DSP + MP3 args nhu ban
    live de chat luong khong doi."""
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "warning", "-y",
        "-reconnect", "1", "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
    ]
    if YTDLP_PROXY:
        cmd += ["-http_proxy", YTDLP_PROXY]
    if headers:
        _ua = headers.get("User-Agent")
        if _ua:
            cmd += ["-user_agent", _ua]
        _others = {k: v for k, v in headers.items()
                   if k.lower() != "user-agent"}
        if _others:
            cmd += ["-headers", "".join(
                f"{k}: {v}\r\n" for k, v in _others.items())]
    cmd += ["-i", url, "-vn",
            "-ac", AUDIO_CHANNELS, "-ar", str(AUDIO_SAMPLE_RATE)]
    if AUDIO_FILTERS:
        cmd += ["-af", AUDIO_FILTERS]
    cmd += ["-codec:a", "libmp3lame", "-b:a", AUDIO_BITRATE,
            "-f", "mp3", dest]
    try:
        proc = subprocess.run(cmd, timeout=_DL_DEADLINE + _XCODE_DEADLINE)
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg tai qua han")
    if proc.returncode != 0 or _size_of(dest) < _MEDIA_MIN_BYTES:
        raise RuntimeError(
            f"ffmpeg tai loi rc={proc.returncode} size={_size_of(dest)}")


def _transcode_local(src: str, dest: str) -> None:
    """Source da tai -> MP3 cung thong so ban live (-vn + DSP preset)."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-y",
           "-i", src, "-vn",
           "-ac", AUDIO_CHANNELS, "-ar", str(AUDIO_SAMPLE_RATE)]
    if AUDIO_FILTERS:
        cmd += ["-af", AUDIO_FILTERS]
    cmd += ["-codec:a", "libmp3lame", "-b:a", AUDIO_BITRATE,
            "-f", "mp3", dest]
    try:
        proc = subprocess.run(cmd, timeout=_XCODE_DEADLINE)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"transcode qua {_XCODE_DEADLINE}s")
    if proc.returncode != 0 or _size_of(dest) < _MEDIA_MIN_BYTES:
        raise RuntimeError(
            f"transcode loi rc={proc.returncode} size={_size_of(dest)}")


def _preload_worker(key: str, title: str) -> None:
    """resolve -> tai source (chunked; m3u8/loi -> ffmpeg) -> transcode ->
    publish atomic {key}.mp3. 2 chu ky: chu ky 2 resolve LAI (URL het han)."""
    final, part, src = _final_path(key), _part_path(key), _src_path(key)
    t0 = time.time()
    err = None
    for cycle in range(2):
        try:
            if cycle:
                with _lock:
                    _resolved.pop(key, None)  # buoc resolve URL moi
                _preload_state_update(key, note="resolve-fresh")
            t_step = time.time()
            entry = _resolve(key, title)
            t_resolve = time.time() - t_step
            url = entry["direct_url"]
            headers = entry.get("http_headers")
            for p in (part, src):
                try:
                    os.remove(p)
                except OSError:
                    pass
            t_step = time.time()
            src_bytes = 0
            if ".m3u8" in url.lower():
                # ffmpeg vua tai vua encode -> khong tach duoc 2 cong doan.
                _download_ffmpeg(url, headers, part)
                t_download = time.time() - t_step
                t_transcode = 0.0
            else:
                _fetch_chunked(url, headers, src)
                src_bytes = _size_of(src)
                t_download = time.time() - t_step
                t_step = time.time()
                _transcode_local(src, part)
                t_transcode = time.time() - t_step
                try:
                    os.remove(src)
                except OSError:
                    pass
            if _size_of(part) < _MEDIA_MIN_BYTES:
                raise RuntimeError(
                    f"mp3 qua nho sau transcode: {_size_of(part)}")
            os.replace(part, final)  # atomic -> reader khong thay file nua do
            _preload_state_update(key, state="ready", err="")
            # Chia thoi gian theo cong doan: resolve (mang/POT) - tai
            # (googlevideo/proxy) - transcode (CPU instance). Day la so lieu de
            # biet nut that that su thay vi toi uu nham cho.
            sys.stderr.write(
                f"[music] preload OK '{entry['title']}' ({_size_of(final)} B, "
                f"{time.time() - t0:.0f}s = resolve {t_resolve:.0f}s + tai "
                f"{t_download:.0f}s ({src_bytes or _size_of(part)} B) + "
                f"transcode {t_transcode:.0f}s, cycle {cycle + 1}, "
                f"client={entry.get('client') or 'n/a'})\n")
            sys.stderr.flush()
            _prune_cache()
            return
        except Exception as e:  # noqa: BLE001 - retry 1 lan roi failed
            err = e
            sys.stderr.write(f"[music] preload cycle {cycle + 1}/2 loi: {e}\n")
            sys.stderr.flush()
            time.sleep(2)
    _preload_state_update(key, state="failed", err=str(err)[:300])


def _preload_start(key: str, title: str) -> None:
    """Khoi tai/preload bai (single-flight). Goi tu get_song_url (truoc khi
    robot GET) va tu route /stream (neu get_song_url chua run)."""
    with _lock:
        st = (_preload.get(key) or {}).get("state")
        if st == "running":
            return
        if st == "ready" and _size_of(_final_path(key)) > 0:
            return
        _preload[key] = {"state": "running", "t0": time.time(), "err": ""}
    threading.Thread(target=_preload_worker, args=(key, title),
                     daemon=True).start()


def _ffmpeg_chunks(direct_url: str, headers: dict = None):
    # 1 lần convert duy nhất: nguồn -> PCM (swr + filter DSP) -> MP3 160 kbps.
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "warning",
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
    ]
    # Trùng khớp với cách yt-dlp tự tải: cùng proxy (cùng IP đã extract —
    # googlevideo có thể gắn IP) + cùng http_headers (UA android/ios/tv —
    # Google có thể 403 nếu ffmpeg gửi UA mặc định Lavf/xx).
    if YTDLP_PROXY:
        cmd += ["-http_proxy", YTDLP_PROXY]
    if headers:
        _ua = headers.get("User-Agent")
        if _ua:
            cmd += ["-user_agent", _ua]
        _others = {k: v for k, v in headers.items()
                   if k.lower() != "user-agent"}
        if _others:
            cmd += ["-headers", "".join(
                f"{k}: {v}\r\n" for k, v in _others.items())]
    cmd += [
        "-i", direct_url,
        "-vn",
        "-ac", AUDIO_CHANNELS,
        "-ar", str(AUDIO_SAMPLE_RATE),
    ]
    if AUDIO_FILTERS:
        cmd += ["-af", AUDIO_FILTERS]
    cmd += [
        "-codec:a", "libmp3lame",
        "-b:a", AUDIO_BITRATE,
        "-f", "mp3",
        "pipe:1",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=sys.stderr)
    sent = 0
    try:
        while True:
            chunk = proc.stdout.read(16 * 1024)
            if not chunk:
                break
            sent += len(chunk)
            yield chunk
        if sent == 0:
            # ffmpeg chết trước khi xuất byte (403/UA/proxy/IP-binding...) —
            # log rõ để chẩn đoán trên Render; stderr ffmpeg cũng trỏ vào log.
            sys.stderr.write(
                f"[ffmpeg] NO OUTPUT rc={proc.poll()} "
                f"url={direct_url[:140]}\n")
            sys.stderr.flush()
    finally:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Silence primer + stream body: khi file chua xong, server van gui MP3 lien
# tuc (im cung thong so) de ket noi khong chet. Firmware (music_player.cc):
# pre-buffer 64 KB truoc khi decode va timeout doc 15s moi lan doc -> server
# IM = mat ket noi + restart bai. Im va bai THAT cung sample-rate/channels/
# bitrate nen decoder chuyen doi lien tuc, khong mat dong (src_rate gan 1 lan).
# ---------------------------------------------------------------------------
def _ensure_silence() -> bytes:
    """~10s MP3 im dung chung AUDIO_* cua bai hat, tao 1 lan bo cache."""
    params = f"{AUDIO_SAMPLE_RATE}_{AUDIO_CHANNELS}_{AUDIO_BITRATE}"
    path = os.path.join(MEDIA_DIR, f"_silence_{params}.mp3")
    if os.path.exists(path) and os.path.getsize(path) >= 1000:
        with open(path, "rb") as f:
            data = f.read()
        return data
    layout = "mono" if AUDIO_CHANNELS == "1" else "stereo"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i",
           f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl={layout}",
           "-t", "10", "-ac", AUDIO_CHANNELS,
           "-codec:a", "libmp3lame", "-b:a", AUDIO_BITRATE,
           "-f", "mp3", path]
    proc = subprocess.run(cmd, timeout=60)
    if proc.returncode != 0 or _size_of(path) < 1000:
        raise RuntimeError(f"tao silence loi rc={proc.returncode}")
    with open(path, "rb") as f:
        data = f.read()
    sys.stderr.write(f"[music] silence primer {len(data)} B ({params})\n")
    sys.stderr.flush()
    return data


def _stream_body(key: str, title: str, entry: dict = None):
    """Body cho ket noi /stream chua co file (4 truong hop).

    `entry` chi can khi phai fallback live pipe; route /stream truyen None de
    khong resolve dong bo (tranh extract lan 2 chay song song voi preload).
      1. File xong         -> doc toan bo file, dung (primer neu da gui ~0.4s).
      2. Dang tai           -> primer silence 8 KB, pace ~realtime -> cho file
                               xong roi chuyen sang file tu byte 0 (MP3 lien tuc).
      3. Preload loi        -> live pipe giam cap (hanh vi cu truoc day).
      4. Qua _BODY_WAIT_MAX -> live pipe giam cap.
    """
    final = _final_path(key)
    t0 = time.time()
    primed = False
    silence = b""
    off = 0
    silence_err = False
    while True:
        if _size_of(final) > 0:
            if primed:
                sys.stderr.write(
                    f"[music] primer->file switch key={key} sau "
                    f"{time.time() - t0:.1f}s\n")
                sys.stderr.flush()
            try:
                with open(final, "rb") as f:
                    while True:
                        buf = f.read(32 * 1024)
                        if not buf:
                            return
                        yield buf
            except OSError as e:
                sys.stderr.write(f"[music] doc file loi: {e}\n")
                sys.stderr.flush()
                return
        state = _preload_state(key)
        elapsed = time.time() - t0
        if state == "failed" or elapsed > _BODY_WAIT_MAX:
            reason = ("preload failed" if state == "failed"
                      else f"cho file qua {_BODY_WAIT_MAX}s")
            sys.stderr.write(
                f"[music] {reason} -> live pipe fallback key={key}\n")
            sys.stderr.flush()
            with _lock:
                cached = _resolved.get(key)
            if cached and cached.get("expires", 0) > time.time():
                entry = cached  # URL preload da resolve san -> khong ton extract
            else:
                try:
                    entry = _resolve(key, title)  # URL moi nhat cho fallback
                except Exception as e:  # noqa: BLE001
                    sys.stderr.write(
                        f"[music] live pipe fallback resolve loi: {e}\n")
                    sys.stderr.flush()
                    return  # het duong -> dong response de firmware thu lai
            yield from _ffmpeg_chunks(entry["direct_url"],
                                      entry.get("http_headers"))
            return
        if not silence and not silence_err:
            try:
                silence = _ensure_silence()
            except Exception as e:  # noqa: BLE001 - primer loi khong chet
                silence_err = True
                sys.stderr.write(f"[music] silence primer loi: {e}\n")
                sys.stderr.flush()
                time.sleep(1.0)
                continue
        if not silence:
            time.sleep(0.5)  # khong co gi de gui -> chi doi state/thoi gian
            continue
        n = min(8 * 1024, len(silence) - off)
        if n <= 0:
            off = 0
            continue
        primed = True
        yield silence[off:off + n]
        off = (off + n) % len(silence)
        # Pace ~realtime: neu gap hon toc do robot phat thi backlog im bi
        # gom trong buffer va robot phai nghe no TRUOC bai that sau khi switch.
        time.sleep(n / _PRIMER_PACE)


@stream_app.get("/stream/{key}.mp3")
def stream(key: str):
    # File da co tren dia -> phuc vu ngay (Content-Length, khong can resolve).
    # Con song duoc sau khi Render restart: _titles mat nhung file con lai.
    if _size_of(_final_path(key)) > 0:
        return FileResponse(_final_path(key), media_type="audio/mpeg")
    with _lock:
        title = _titles.get(key)
    if not title:
        raise HTTPException(404, "unknown stream id — hãy gọi get_song_url trước")
    # KHONG resolve dong bo o day: preload thread da/đang resolve -> resolve lai
    # o day la extract thu 2 chay song song (log 2 dong "resolved") va giu ket
    # noi cua robot cho toi khi extract xong (khong gui duoc primer).
    # _stream_body tu lay entry khi phai fallback live pipe.
    _preload_start(key, title)  # idempotent: chay truoc/doi primer o duoi
    return StreamingResponse(
        _stream_body(key, title),
        media_type="audio/mpeg",
        headers={"Content-Type": "audio/mpeg"})


# /health ở ROOT cho Render healthCheckPath — đăng ký qua custom_route nên
# không cần auth; FastAPI không có route này (Mount đặt SAU nên custom_route
# được match trước).
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "cookies": bool(COOKIES_FILE),
        "pot": POT_AVAILABLE,
        "pot_server": _pot_server_up(timeout=1.0),
        "proxy": bool(YTDLP_PROXY),
        "cache": {
            "dir": MEDIA_DIR,
            "files": _count_cache_files(),
            "preload_running": sum(
                1 for v in _preload.values() if v.get("state") == "running"),
        },
        "audio": {
            "sample_rate": AUDIO_SAMPLE_RATE,
            "channels": AUDIO_CHANNELS,
            "bitrate": AUDIO_BITRATE,
            "preset": AUDIO_PRESET,
            "filters": AUDIO_FILTERS,
            "presets_available": sorted(AUDIO_PRESETS),
        },
    })


# Mount TOÀN BỘ routes của stream_app (/stream/...) vào app MCP tại root:
# thứ tự quan trọng — FastMCP đặt /mcp trước custom routes, nên request /mcp luôn
# vào MCP, /health vào custom_route, các path còn lại rơi vào stream_app.
mcp._custom_starlette_routes.append(Mount("/", app=stream_app))


# ---------------------------------------------------------------------------
# Global state (phải định nghĩa TRƯỚC mcp.run() để tránh NameError)
# ---------------------------------------------------------------------------
_titles = {}     # key -> title do get_song_url đăng ký
_resolved = {}   # key -> {"direct_url":..., "title":..., "expires": float}
_lock = threading.Lock()
# Preload truoc bai hat ve dia (xem _preload_worker): key -> {state, err, ...}
_preload = {}
# Dia luu cache (Render: ephemeral disk ~1GB; prune giu _CACHE_MAX_FILES bai).
MEDIA_DIR = os.environ.get("MUSIC_CACHE_DIR") or os.path.join(
    tempfile.gettempdir(), "zing-music")
os.makedirs(MEDIA_DIR, exist_ok=True)
_CACHE_MAX_FILES = int(os.environ.get("MUSIC_CACHE_MAX_FILES", "20"))
_DL_DEADLINE = 180      # gioi han tai source (giay)
_XCODE_DEADLINE = 300   # gioi han transcode local (giay)
_BODY_WAIT_MAX = 240    # stream body doi file toi da (giay) truoc fallback
_CHUNK_BYTES = 1024 * 1024   # moi phien Range 1 MB (resume duoc giua chung)
_DL_ATTEMPTS = 4        # retry moi chunk khi dong ket noi som


def _primer_pace() -> int:
    """Toi da byte/s cho silence primer = dung toc do phat (~bitrate/8).
    Nhanh hon toc do robot phat thi backlog im nam trong kernel buffer ->
    sau khi file xong, robot con phai nghe het backlog truoc bai THAT."""
    b = str(AUDIO_BITRATE).strip().lower()
    try:
        bits = float(b[:-1]) * 1000 if b.endswith("k") else float(b)
    except ValueError:
        bits = 160000.0
    return max(8000, int(bits / 8 * 1.05))


_PRIMER_PACE = _primer_pace()
_RESOLVE_TTL = 30 * 60  # direct URL googlevideo dùng lại tối đa 30 phút


def _run_http():
    """Chỉ dùng ở MCP_TRANSPORT=stdio: chạy HTTP stream (cùng Starlette app
    với MCP — gồm /mcp + /health + /stream/...) trên cổng PORT ở chế độ nền."""
    import uvicorn
    try:
        uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=PORT,
                    log_level="warning")
    except Exception as e:
        sys.stderr.write(f"[music] FATAL HTTP server error on port {PORT}: {e}\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------
@mcp.tool()
def search_song(keyword: str) -> str:
    """Tìm bài hát theo tên (hoặc tên + ca sĩ). Trả về tối đa 5 kết quả:
    title, uploader, duration_sec, youtube_url. Dùng get_song_url để lấy
    stream URL mp3 cho robot phát."""
    res = _search_youtube(keyword, 5)
    sys.stderr.write(f"[music] search '{keyword}' -> {len(res)} ket qua\n")
    for r in res[:3]:
        sys.stderr.write(f"[music]   - {r.get('title')} | {r.get('uploader')}\n")
    sys.stderr.flush()
    return json.dumps({"results": res}, ensure_ascii=False)


@mcp.tool()
def get_song_url(title: str) -> str:
    """Chuẩn bị một bài hát để robot phát. Trả về NGAY {"status": "ready",
    "stream_url": "..."} — robot gọi self.music.play(stream_url) với URL này.
    Không cần chờ hay gọi lại: tải/convert chạy NGAY phía sau; robot mở URL
    là phát liền (file đã cache sẵn hoặc nhận dần qua silence primer)."""
    key = _key_of(title)
    with _lock:
        _titles[key] = title.strip()
    # Pre-resolve + tai truoc + transcode nen (neu chua chay), de robot mo
    # /stream la co file san — khong con stream pipe song dong bi dut giua chung.
    _preload_start(key, title.strip())
    return json.dumps({
        "status": "ready",
        "id": key,
        "stream_url": _stream_url(key),
    }, ensure_ascii=False)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        q = " ".join(sys.argv[2:])
        print(json.dumps(_search_youtube(q, 3), ensure_ascii=False, indent=2))
    else:
        sys.stderr.write(
            f"[music] transport={MCP_TRANSPORT} port={PORT} "
            f"(base={PUBLIC_BASE or 'auto-LAN'})\n")
        sys.stderr.write(
            f"[music] audio: {AUDIO_SAMPLE_RATE} Hz x{AUDIO_CHANNELS}, "
            f"{AUDIO_BITRATE} mp3, preset={AUDIO_PRESET}\n")
        sys.stderr.write(f"[music] filters: {AUDIO_FILTERS or '(none)'}\n")
        sys.stderr.write(
            f"[music] cache: {MEDIA_DIR} (max {_CACHE_MAX_FILES} bai)\n")
        sys.stderr.flush()
        if MCP_TRANSPORT == "stdio":
            # Chế độ cũ: MCP stdio (qua mcp_pipe.py) + HTTP stream chạy nền.
            threading.Thread(target=_run_http, daemon=True).start()
            mcp.run()
        else:
            # Chế độ cloud: MCP streamable-http + HTTP stream CÙNG một uvicorn.
            mcp.run(transport="streamable-http")
