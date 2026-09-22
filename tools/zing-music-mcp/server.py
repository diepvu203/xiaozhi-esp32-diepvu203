#!/usr/bin/env python3
"""
XiaoZhi Music MCP Server — Cloud Stream Proxy (RAM-only, không file đĩa).

Kiến trúc:
  - MCP tool `search_song(keyword)`: tìm bài trên YouTube (yt-dlp, có fallback
    scrape HTML khi bị anti-bot).
  - MCP tool `get_song_url(title)`: trả NGAY lập tức stream URL công khai
    `<PUBLIC_BASE>/stream/<id>.mp3` (id = md5(title)); không tải gì trước.
  - FastAPI `GET /stream/{id}.mp3`: lúc robot kết nối thì resolve direct URL
    googlevideo bằng yt-dlp (cache RAM ~30 phút), spawn ffmpeg pipe ra stdout
    và StreamingResponse truyền thẳng xuống HTTP — 0 file tạm trên đĩa.

Cấu hình qua biến môi trường:
  PORT        — cổng HTTP (Render tự set). Mặc định 8619.
  PUBLIC_BASE — base URL công khai cho stream URL (vd https://xxx.onrender.com).
                Bỏ trống thì tự dùng http://<IP-LAN>:<PORT> (chạy laptop dev).

Chạy: python server.py  (uvicorn nền + MCP stdio qua mcp_pipe.py)
"""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse

from mcp.server.fastmcp import FastMCP

PORT = int(os.environ.get("PORT", "8619"))
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "").rstrip("/")
FFMPEG = shutil.which("ffmpeg")  # Dockerfile cài qua apt; dev có imageio fallback
if not FFMPEG:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

mcp = FastMCP("music-server")

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
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio[abr<=128]/bestaudio/best",
        "default_search": "ytsearch1",
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(title, download=False)
    chosen = info["entries"][0] if "entries" in info else info
    direct = chosen.get("url")
    if not direct:
        fmts = [f for f in (chosen.get("formats") or []) if f.get("url")]
        if not fmts:
            raise RuntimeError("không lấy được direct stream URL")
        direct = fmts[-1]["url"]
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
# FastAPI — Cloud Stream Proxy
# ---------------------------------------------------------------------------
app = FastAPI()


@app.get("/health")
def health():
    return PlainTextResponse("ok")


def _ffmpeg_chunks(direct_url: str):
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "warning",
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", direct_url,
        "-vn",
        "-ac", "1",
        "-ar", "24000",
        "-codec:a", "libmp3lame",
        "-b:a", "96k",
        "-f", "mp3",
        "pipe:1"
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


@app.get("/stream/{key}.mp3")
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


# ---------------------------------------------------------------------------
# Global state (phải định nghĩa TRƯỚC mcp.run() để tránh NameError)
# ---------------------------------------------------------------------------
_titles = {}     # key -> title do get_song_url đăng ký
_resolved = {}   # key -> {"direct_url":..., "title":..., "expires": float}
_lock = threading.Lock()
_RESOLVE_TTL = 30 * 60  # direct URL googlevideo dùng lại tối đa 30 phút


def _run_http():
    import uvicorn
    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
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
        threading.Thread(target=_run_http, daemon=True).start()
        sys.stdout.write(f"[music] HTTP/Stream API on :{PORT} (base={PUBLIC_BASE or 'auto-LAN'})\n")
        sys.stdout.flush()
        mcp.run()
