#!/usr/bin/env python3
"""
XiaoZhi Music MCP Server — Cloud Stream Proxy (RAM-only, không file đĩa).

Kiến trúc:
  - MCP tool `search_song(keyword)`: tìm bài trên YouTube (yt-dlp, có fallback
    scrape HTML khi bị anti-bot).
  - MCP tool `get_song_url(title)`: trả NGAY lập tức stream URL công khai
    `<PUBLIC_BASE>/stream/<id>.mp3` (id = md5(title)); không tải gì trước.
  - HTTP `GET /stream/{id}.mp3`: lúc robot kết nối thì resolve direct URL
    googlevideo bằng yt-dlp (cache RAM ~30 phút), spawn ffmpeg pipe ra stdout
    và StreamingResponse truyền thẳng xuống HTTP — 0 file tạm trên đĩa.
    Chuỗi convert: PCM 24 kHz mono -> MP3 (mặc định 160 kbps; xem AUDIO_*
    bên dưới để chỉnh mà không cần sửa code).

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
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yt_dlp
from fastapi import FastAPI, HTTPException
from starlette.routing import Mount
from starlette.responses import JSONResponse, StreamingResponse
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
if _cookies_b64:
    try:
        _data = base64.b64decode(_cookies_b64)
        COOKIES_FILE = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
        with open(COOKIES_FILE, "wb") as _f:
            _f.write(_data)
        sys.stdout.write(f"[music] cookies loaded ({len(_data)} bytes)\n")
        sys.stdout.flush()
    except Exception as e:
        COOKIES_FILE = None
        sys.stderr.write(f"[music] YTDLP_COOKIES_B64 decode failed: {e}\n")

YTDLP_PROXY = os.environ.get("YTDLP_PROXY", "").strip()


def _apply_ydl_auth(opts: dict) -> dict:
    """Gắn cookiefile/proxy vào opts yt-dlp nếu env corresponding có set."""
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    if YTDLP_PROXY:
        opts["proxy"] = YTDLP_PROXY
    return opts


mcp = FastMCP("music-server", host="0.0.0.0", port=PORT)

# RAM-only state


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
        "no_warnings": True,
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


def _preresolve_bg(key: str, title: str) -> None:
    """Pre-resolve the direct googlevideo URL in a background thread so the
    first HTTP GET /stream/... is fast. Uses a thread (NOT asyncio) because
    yt-dlp is blocking and the MCP tool runs outside the uvicorn event loop."""
    def _work():
        try:
            _resolve(key, title)
        except Exception as e:  # noqa: BLE001 - best effort warm-up only
            sys.stdout.write(f"[music] preresolve failed for '{title}': {e}\n")
            sys.stdout.flush()

    threading.Thread(target=_work, daemon=True).start()


def _resolve(key: str, title: str) -> dict:
    with _lock:
        cached = _resolved.get(key)
    if cached and cached.get("expires", 0) > time.time():
        return cached
    base_opts = {
        "quiet": True,
        "no_warnings": True,
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
    for fmt in _format_tiers:
        opts = dict(base_opts)
        if fmt:
            opts["format"] = fmt
        _apply_ydl_auth(opts)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(title, download=False)
            break
        except yt_dlp.utils.DownloadError as e:
            if "Requested format is not available" not in str(e):
                raise  # lỗi khác (bot-check, unavailable...) -> nổi lên ngay
            last_err = e
    if info is None:
        # Chẩn đoán (stderr): extract KHÔNG selector để đếm formats;
        # no_warnings=False để lộ cảnh báo EJS/JS-runtime thường bị ẩn.
        diag_opts = dict(base_opts)
        diag_opts["no_warnings"] = False
        _apply_ydl_auth(diag_opts)
        n_formats = -1
        diag = None
        try:
            with yt_dlp.YoutubeDL(diag_opts) as ydl:
                diag = ydl.extract_info(title, download=False)
            n_formats = len(diag.get("formats") or [])
        except Exception as de:  # noqa: BLE001 - chẩn đoán
            sys.stderr.write(f"[music] diag extract: {de}\n")
        sys.stderr.write(
            f"[music] resolve fail '{title}': formats={n_formats}, "
            f"deno={shutil.which('deno') or 'NOT FOUND'}, "
            f"yt-dlp={yt_dlp.version.__version__}\n")
        if diag is not None and n_formats > 0:
            # Plain extract có formats (selector ảo là thủ phạm) -> dùng luôn.
            info = diag
        else:
            # Fallback: player_client không cần web-sig/PO-token — đặc trị
            # formats=[] khi cookies + IP datacenter khiến web client bị strip.
            cli_opts = dict(base_opts)
            cli_opts["format"] = "bestaudio/best"
            cli_opts["extractor_args"] = {
                "youtube": {"player_client": ["android", "ios", "tv"]}}
            _apply_ydl_auth(cli_opts)
            try:
                with yt_dlp.YoutubeDL(cli_opts) as ydl:
                    info = ydl.extract_info(title, download=False)
                sys.stderr.write(
                    "[music] resolved via player_client=android,ios,tv\n")
            except yt_dlp.utils.DownloadError as e:
                last_err = e
        if info is None:
            raise RuntimeError(
                f"không lấy được format nào cho '{title}' "
                f"(formats={n_formats}, "
                f"deno={'yes' if shutil.which('deno') else 'NO'}): {last_err}")
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
    entry = {
        "direct_url": direct,
        "title": chosen.get("title") or title,
        "uploader": chosen.get("uploader") or chosen.get("channel"),
        "duration": chosen.get("duration"),
        "expires": time.time() + _RESOLVE_TTL,
    }
    with _lock:
        _resolved[key] = entry
    sys.stdout.write(f"[music] resolved '{entry['title']}'\n")
    sys.stdout.flush()
    return entry


# ---------------------------------------------------------------------------
# Unified Server — MCP + HTTP Streaming (gộp chung MỘT Starlette app)
# ---------------------------------------------------------------------------

# Các endpoint HTTP stream của FastAPI; được Mount vào Starlette app của MCP
# ngay bên dưới (mcp._custom_starlette_routes) nên chạy chung một cổng với MCP.
stream_app = FastAPI()


def _ffmpeg_chunks(direct_url: str):
    # 1 lần convert duy nhất: nguồn -> PCM (swr + filter DSP) -> MP3 160 kbps.
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "warning",
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
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
    try:
        while True:
            chunk = proc.stdout.read(16 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            proc.kill()
        except Exception:
            pass


@stream_app.get("/stream/{key}.mp3")
def stream(key: str):
    with _lock:
        title = _titles.get(key)
    if not title:
        raise HTTPException(404, "unknown stream id — hãy gọi get_song_url trước")
    try:
        entry = _resolve(key, title)
    except Exception as e:
        raise HTTPException(502, f"resolve thất bại: {e}")
    return StreamingResponse(_ffmpeg_chunks(entry["direct_url"]),
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
        "proxy": bool(YTDLP_PROXY),
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
    sys.stdout.write(f"[music] search '{keyword}' -> {len(res)} ket qua\n")
    for r in res[:3]:
        sys.stdout.write(f"[music]   - {r.get('title')} | {r.get('uploader')}\n")
    sys.stdout.flush()
    return json.dumps({"results": res}, ensure_ascii=False)


@mcp.tool()
def get_song_url(title: str) -> str:
    """Chuẩn bị một bài hát để robot phát. Trả về NGAY {"status": "ready",
    "stream_url": "..."} — robot gọi self.music.play(stream_url) với URL này.
    Không cần chờ hay gọi lại: việc tải/convert diễn ra khi robot mở URL."""
    key = _key_of(title)
    with _lock:
        _titles[key] = title.strip()
    # Warm up the direct URL in the background so the robot's first GET is fast.
    _preresolve_bg(key, title.strip())
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
        sys.stdout.write(
            f"[music] transport={MCP_TRANSPORT} port={PORT} "
            f"(base={PUBLIC_BASE or 'auto-LAN'})\n")
        sys.stdout.write(
            f"[music] audio: {AUDIO_SAMPLE_RATE} Hz x{AUDIO_CHANNELS}, "
            f"{AUDIO_BITRATE} mp3, preset={AUDIO_PRESET}\n")
        sys.stdout.write(f"[music] filters: {AUDIO_FILTERS or '(none)'}\n")
        sys.stdout.flush()
        if MCP_TRANSPORT == "stdio":
            # Chế độ cũ: MCP stdio (qua mcp_pipe.py) + HTTP stream chạy nền.
            threading.Thread(target=_run_http, daemon=True).start()
            mcp.run()
        else:
            # Chế độ cloud: MCP streamable-http + HTTP stream CÙNG một uvicorn.
            mcp.run(transport="streamable-http")
