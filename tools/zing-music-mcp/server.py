#!/usr/bin/env python3
"""
XiaoZhi Music MCP Server — Preload to disk + Cloud Stream Proxy.

Kiến trúc:
  - MCP tool `search_song(keyword)`: tìm bài trên TẤT CẢ nguồn đang bật
    (mặc định YouTube + SoundCloud qua yt-dlp; YouTube có fallback scrape
    HTML khi bị anti-bot).
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
  YTDLP_SOURCES  — danh sách nguồn nhạc, cách nhau bằng "," theo thứ tự ưu
                  tiên. Mặc định "youtube,soundcloud". SoundCloud không cần
                  cookies/PO token nên giữ service sống khi YouTube bị chặn.
  YTDLP_RESOLVE_CANDIDATES — số ứng viên mỗi nguồn thử thêm khi ứng viên
                  đầu hỏng (mặc định 2). ứng viên xếp hạng theo độ khớp tên.
  YTDLP_SOURCE_COOLDOWN — giây bỏ qua nguồn vừa gặp bot-check (mặc định
                  900 = 15 phút). 0 = tắt.

Chạy: python server.py
"""

import base64
import hashlib
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yt_dlp
from fastapi import FastAPI, HTTPException
from starlette.routing import Mount
from starlette.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
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
#   speaker (mặc định) - bù trừ loa nhỏ: cắt dải mà loa không dựng nổi
#                        (dưới 100 Hz) và chuyển động lượng sang 250-500 Hz
#                        (loa thực sự phát được) -> ấm mà không vỡ bass.
#                        Giảm 800 Hz bớt "hộp", nhấn 3.2 kHz cho rõ tiếng
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
#
# Đo bằng ffmpeg bandpass/astats trên thang đo dB tuyệt đối, cùng một tín
# hiệu thử là bản "remix bass mạnh" (tổng hợp kick + bass + nhạc) — mọi
# phương án dưới đây đo trên CÙNG file đó nên so sánh được với nhau.
# Mốc: dải 500 Hz-2 kHz (loa nhỏ phát tốt nhất) đặt = 0 dB.
#
#   chuỐi filter            30-60   60-120  250-500  bass(60-120) - mid
#   nguồn (không DSP)        ...     -20.0   -22.5      +2.5 dB
#   BẢN CŨ: hp60+g110+g200  -21.3   -22.1   -25.8      +3.7 dB
#   BẢN MỚI: hp100+g250+g400 -27.2  -26.7   -23.6      -1.9 dB
#
# Vì sao đổi (đây là sửa lỗi "âm bass vỡ, remix nghe rè rè"):
# Bản cũ cộng dồn 2.5 dB ở 110 Hz + 3.5 dB ở 200 Hz NGAY TRÊN highpass 60 Hz.
# Tín hiệu bass mạnh bị đẩy lên đúng dải mà loa nhỏ không dựng nổi, năng lượng
# thành hành động côn loa (excursion) -> méo. Đo trên bài thật, phần năng lượng
# nằm trong 30-120 Hz giảm từ 14.8% xuống 10.9% toàn bài, trong khi dải
# 150 Hz-8 kHz giữ nguyên -> cắt đúng chỗ thừa, không cắt nhầm chỗ đang nghe.
#
# Nói cách khác, lần trước tôi sửa đúng triệu chứng ("nghe mỏng") nhưng gây ra
# triệu chứng mới: với bài nhạc thường boost trầm giúp ấm, còn với remix bass
# mạnh thì chính lượng boost đó là thứ vỡ âm. Không có gain nào vừa giữ được cả
# hai — nên mục tiêu mới là CÂN BẰNG, không phải bù: đưa bass xuống thấp hơn
# dải trung 1.9 dB (thấp vừa đủ để không lấn át) thay vì nhô lên 3.7 dB.
#
# Cắt sâu hơn ở 30-60 Hz là cố ý: đây là dải loa nhỏ vốn không phát nổi, giữ
# lại chỉ gây rung. Đổi lại, 250-500 Hz được cộng +2.2 dB — dải MÀ loa 3W thực
# sự dựng được, nên đây mới là chỗ "ấm" nghe được chứ không phải 60-120 Hz.
#
# Lưu ý: highpass f=100 KHÔNG cắt dải 80-150 Hz một cách máy móc như tên gọi
# gợi ý — equalizer tại 250 Hz (w=0.9 octave) có đuôi tràn xuống dưới, bù lại
# phần bị cắt; đo được ở 200 Hz vẫn +4.5 dB.
#
# KHÔNG kỳ vọng limiter ít bóp hơn: đo trên cả bài thật lẫn tín hiệu remix
# bass mạnh, peak sau EQ đều chạm trần 0 dBFS ở CẢ HAI bản (nguồn 128k vốn đã
# nén chặt), nên limiter vẫn bóp ~1.5 dB như cũ. Cái hạn chế nằm ở chỗ khác.
#
# Băng thông KHÔNG đổi: đây là bộ lọc trước khi encode, vẫn xuất MP3 160k
# như cũ -> không tốn thêm byte nào gửi về robot.
# 29/09 - sua "am tram u u, giong tram khong ro" (bai DANHKA)
#
# Do bang FFT tren chinh bai nay (full track 330 s, 40 s dau, 467 doan pho):
#     40-60 Hz chiem 61.8% TONG CONG SUAT; ca dai 0-120 Hz chiem 88.3%
#     trong khi 2-8 kHz (noi giong nguoi ro) chi con 2.2%
# Khong phai codec, khong phai loi DC (DC = -1.2, sach), cung khong phai
# headroom - ma la DAC TINH BAI HAT: sub-bass cuc nang, tieng "om om" cua
# giong nam tram nam dung o 50-70 Hz (48% cong suat).
#
# Vi sao ban truoc van "u": highpass f=100 chi cat DUOI 100 Hz o 12 dB/octave
# nen 50-70 Hz chi giam ~3.5 dB, con 100-120 Hz gan nhu nguyen yen. Dai
# 40-60 Hz tran thang vao con loa -> chinh la "u u". Dong thoi luong cong
# suat khong loi do bi limiter bop lai, thay the moi thu o 2-8 kHz (2.2%)
# nen giong nguoi bi chim hanh.
#
# Sua: cat manh vung 30-90 Hz (Q hep de khong lan sang tren), roi DAY LEN
# dai loa thuc su phat duoc. Cat tram + nang trung-ca la mot lan giao
# dich: giai phong cong suat cho giong noi thay vi dot het vao loa khong
# dung noi. 2 tang p=2 + 2 equalizer Q hep -> ~24 dB/octave quanh 60 Hz.
# 29/09 (v2) — sua loi cua v1: cat Q rong lam "nghe tu, mat cao"
#
# Do dap ung tan so that (sine tung tan qua chuoi -> MP3 160k -> decode)
# tren chinh bai DANHKA cho thay v1 KHONG lam mat cao (800Hz-10kHz deu
# TANG +1.8..+2.6 dB). Cai sai la v1 cat nham 120-300 Hz (-7.7..-4.2 dB) -
# dung vung THAN GIONG ca si nam, nen ca bai nghe "tu". Loa nho dung dai
# 80-150 Hz lam "cau noi" de moi tan so cao hon cong huong, cat mat no thi
# ca phan tren cung mat rung.
#
# v2: cat vua phai o 40-70 Hz (giu duoc viec bo "u u") + TRA LAI than giong
# o 200-400 Hz + day them 3-10 kHz cho do sang.
#   dap ung so voi ban dau (dB):        50Hz   80Hz  120Hz  200Hz  300Hz  3.2k   6k  10k
#   v0 (c ban dau)                    -12.5   -5.0   -0.3   +4.1   +5.2  +2.0  -0.1  +0.3
#   v1 (cat Q rong)                  -18.5  -13.2   -8.1   -2.3   +1.0  +3.9   0.0  +2.2
#   v2 (nay)                          -12.0   -5.3   -0.9   +3.4   +3.6  +6.1  +3.4  +1.5
_SPEAKER_EQ = (
    "highpass=f=70:p=2,"
    "equalizer=f=50:t=q:w=0.6:g=-5,"
    "equalizer=f=200:t=q:w=1.0:g=4,"
    "equalizer=f=400:t=q:w=1.1:g=3,"
    "equalizer=f=800:t=q:w=1.2:g=-1,"
    "equalizer=f=3200:t=q:w=1.3:g=6,"
    "equalizer=f=6000:t=q:w=1.2:g=3,"
    "treble=g=3.5:f=10000:w=0.7"
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


def _mk_result(e: dict, source: str) -> dict:
    return {
        "title": e.get("title"),
        "uploader": e.get("uploader") or e.get("channel") or e.get("creator"),
        "duration_sec": e.get("duration"),
        "source": source,
        # TEN TRUONG GIU NGUYEN PHIEN BAN CU: agent cu/van doc key nay, va
        # URL nay co the tro toi bat ky nguon nao (khong con chi YouTube).
        "youtube_url": e.get("url") or e.get("webpage_url"),
    }


def _flat_search(source: str, query: str, n: int, retries: int = 2) -> list:
    """Search phang (extract_flat, KHONG download) tren mot nguon.
    Dung chung cho search_song va cho viec xep hang candidate khi resolve.

    LUON truyen "<key><n>:<query>" lam target thuc (khong dung default_search):
    default_search chi prepend khi yt-dlp KHONG parse duoc input la URL, va
    o do generic extractor nhan "scsearch3:..." -> tra 0 ket qua im lang.
    Co "scsearch3:" trong chinh URL thi SearchInfoExtractor bat duoc dung.
    """
    target = f"{_SOURCE_SEARCH_KEY[source]}{n}:{query}"
    opts = {
        "quiet": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
    }
    _apply_ydl_auth(opts)
    for attempt in range(retries):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
            entries = [e for e in (info.get("entries") or []) if e]
            if entries:
                return [_mk_result(e, source) for e in entries]
        except Exception:  # noqa: BLE001 - thu lai roi tra rong
            pass
        if attempt + 1 < retries:
            time.sleep(1 + attempt)
    return []


def _search_source(source: str, query: str, n: int) -> list:
    if source == "youtube":
        res = _flat_search(source, query, n)
        if res:
            return res
        # ytsearch bi chan -> scrape trang results (van dung khi Cloudflare/
        # anti-bot, nhanh hon viec doi nguon).
        try:
            html = _search_youtube_html(query, n)
        except Exception:  # noqa: BLE001
            return []
        for r in html:
            r["source"] = "youtube"
        return html
    return _flat_search(source, query, n)


def _looks_like_url(text: str) -> bool:
    t = str(text or "").strip().lower()
    return t.startswith(("http://", "https://", "www."))


# ---------------------------------------------------------------------------
# Xep hang candidate: bai nao khop ten duoc yeu cau nhat thi thu truoc.
# Search phang tra ve nhieu ket qua; "ytsearch1"/flat top-1 khong phai luon
# la bai dung (cover, remix, live, 1 phut) -> thu them candidate tiep theo
# thay vi bo cua ca bai.
# ---------------------------------------------------------------------------
def _norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _title_score(want: str, got: str) -> float:
    """Dice tren tap token (bo dau). 1.0 = trung het ten."""
    a = set(_norm_title(want).split())
    b = set(_norm_title(got).split())
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def _rank_candidates(source: str, title: str, n: int, skip: int = 0) -> list:
    """Top-n ket qua search cua `source`, sap xep theo do khop ten.

    PHAI lay _FETCH_PER_SOURCE ket qua roi moi xep hang: thu tu tra ve cua
    search khong tin (bai dung nam o vi tri #5). Lay chi n ket qua roi xep
    la tu xep lai mot tap da sai thu tu.
    `skip` bo qua N ket qua khop nhat (dung khi ladder da thu san top-1).

    Dung cache candidate cua search_song truoc (cung truy van, con hieu luc
    ~10s) — resolve thuong chay ngay sau search_song nen tranh lai mot vong
    mang. Cache het han o duoi van search lai."""
    hit = [r for r in _cached_candidates(title) if r.get("source") == source]
    if len(hit) >= n + skip:
        return hit[skip:skip + n]
    res = _flat_search(source, title, max(_FETCH_PER_SOURCE, n + skip))
    scored = [(_title_score(title, r.get("title")), r) for r in res]
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[skip:skip + n]]


# So ket qua moi nguon dua vao list cua search_song:giu 3 ket qua cua nguon
# chinh de chat luong tot khong bi nguon phu lam loang, nguon sau lay phan con
# trong. Khi nguon chinh bi chan/chan 0 ket qua -> nguon sau lap cho trong.
_MAX_PER_SOURCE = 3
# So ket qua GOI TAY DUNG ra moi nguon de xep hang. QUAN TRONG: thu tu tra ve
# cua search KHONG tin — do khop ten moi tin. Do: query "Tam Thai Tu JACK J97"
# tra ve "Tam Thai Tu - Jack - J97" (khop 100%) o vi tri #5, con 4 ket qua
# dau la bai khac cung album (score 0.5-0.62). Lay thang 3 ket qua dau thi
# agent nghe nham bai. Phai lay rong va xep lai.
_FETCH_PER_SOURCE = 10
# Nguon phu (YouTube) chi dung them 4-6s cho moi search. Neu nguon chinh da
# co bai KHOP TUYET DOI (score 1.0) thi bo qua, de do tre chon bai du co
# chat luong tot hon thi van hoi cac nguon sau.
_SEARCH_ENOUGH_SCORE = float(os.environ.get("YTDLP_SEARCH_ENOUGH_SCORE", "0.95"))
# So ket qua moi nguon giu trong cache candidate cho resolve dung lai (xem
# _cache_candidates). Nho hon _MAX_PER_SOURCE de agent van thay du chon du
# nguon chinh, lon hon RESOLVE_CANDIDATES de resolve co du ung vien thu.
_CAND_KEEP = 8


def _search_all(query: str, n: int = 5) -> list:
    out, seen = [], set()
    counts = {}
    per_source = {}
    for src in SOURCES:
        if _source_cooldown_active(src):
            counts[src] = "cooldown"
            continue
        res = _search_source(src, query, _FETCH_PER_SOURCE)
        # Xep theo do khop ten GOC de dung source dau tien; chi cat con
        # _MAX_PER_SOURCE ket qua cho source do.
        scored = [(_title_score(query, r.get("title")), r) for r in res]
        scored.sort(key=lambda x: -x[0])
        kept = scored[:_MAX_PER_SOURCE]
        if res:
            _clear_source_failed(src)
        counts[src] = f"{len(res)}->{len(kept)}"
        # Cache giu them ket qua (agent co the chon bai ngoai top-3).
        per_source[src] = [r for _, r in scored[:_CAND_KEEP]]
        for _, r in kept:
            dedup = (r.get("youtube_url") or r.get("title") or "").strip().lower()
            if dedup in seen:
                continue
            seen.add(dedup)
            out.append(r)
        if kept and kept[0][0] >= _SEARCH_ENOUGH_SCORE:
            counts["early_exit"] = f"{kept[0][0]:.2f}"
            break
    _cache_candidates(query, per_source)
    sys.stderr.write(
        "[music] search " + ", ".join(
            f"{k}={v}" for k, v in counts.items()) + "\n")
    sys.stderr.flush()
    return out[:n]


# ---------------------------------------------------------------------------
# Cache candidate theo chuoi truy van: search_song vua tra ket qua, resolve
# goi lai _flat_search cung truy van do -> ton them mot vong mang 5-10s cho
# ket qua da co san. Giu lai danh sach xep hang theo ten cho moi truy van
# (gioi han so luong de khong phinh bo nho).
# ---------------------------------------------------------------------------
_CAND_TTL = 600
_CAND_MAX = 24
_candidates = {}


def _cache_candidates(query: str, per_source: dict) -> None:
    """`per_source`: {source: [candidates theo thu tu do khop giam dan]}."""
    merged = []
    for src in SOURCES:
        for r in per_source.get(src, []):
            r.setdefault("source", src)
            merged.append(r)
    if not merged:
        return
    with _lock:
        _candidates[query.strip().lower()] = {
            "ts": time.time(), "list": merged[:_CAND_MAX]}


def _cached_candidates(query: str) -> list:
    with _lock:
        hit = _candidates.get(query.strip().lower())
        if not hit:
            return []
        if time.time() - hit["ts"] > _CAND_TTL:
            _candidates.pop(query.strip().lower(), None)
            return []
    return hit["list"]


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


def _radio_url(key: str) -> str:
    base = PUBLIC_BASE or f"http://{_lan_ip()}:{PORT}"
    return f"{base}/radio/{key}.mp3"


# ---------------------------------------------------------------------------
# Radio (radio-browser.info) — KHAC bai hat: khong pre-load, khong file dong.
# Radio la stream VO HAN, nen server chi transcode khi robot mo ket noi, va
# luon tra 200 (khong Range/206): radio khong co "byte offset" de resume —
# khi firmware ket noi lai giua chung, no muon am thanh HIENTAI.
# ---------------------------------------------------------------------------
# Mirror theo docs.radio-browser.info (chot vien nao do cung duoc).
_RADIO_MIRRORS = tuple(m for m in str(os.environ.get(
    "RADIO_MIRRORS",
    "https://all.api.radio-browser.info,"
    "https://de1.api.radio-browser.info,"
    "https://at1.api.radio-browser.info,"
    "https://nl1.api.radio-browser.info")).split(",") if m)
_radio_mirror = {"base": ""}       # vien dang song (khong do lai moi request)
_radio_search_cache = {}           # query -> {t, list} (TTL 1h)
_radio_stations = {}               # key -> {name, url, ...} (phuc vu /radio)
_radio_bad = {}                    # url -> het han (URL chet, khong dung lai)
# TTL ngan: tram radio hay CHAP CHON (playlist sliding window -> segment host
# doi theo lan fetch; do duoc: ZING BOLERO lan 1 EOF sau 5.6s / 0 byte, lan 2
# ngay sau do lai chay 30 KB/s). 30 phut se chan ca lan thu lai thanh cong.
_RADIO_BAD_TTL = 600               # 10 phut
_RADIO_UA = "xiaozhi-music-mcp/1.0"


def _radio_mark_bad(url: str, why: str = "") -> None:
    """Danh dau URL tram khong dung duoc (playlist tai duoc nhung khong co
    audio — gap that voi HLS cua Zing: segment host khong phan hoi)."""
    with _lock:
        _radio_bad[url] = time.time() + _RADIO_BAD_TTL
    sys.stderr.write(f"[radio] danh dau tram loi {_RADIO_BAD_TTL // 60} phut: "
                     f"{url[:90]} ({why})\n")
    sys.stderr.flush()


def _radio_is_bad(url: str) -> bool:
    with _lock:
        exp = _radio_bad.get(url)
    return bool(exp and exp > time.time())


def _radio_api(path: str, timeout: float = 12.0):
    """GET JSON tu radio-browser, chot vien. Bat buoc User-Agent (API tu choi
    UA mac dinh cua urllib) va khong trailing slash (tra 301 neu thieu)."""
    order = [_radio_mirror["base"]] if _radio_mirror["base"] else []
    order += [m for m in _RADIO_MIRRORS if m not in order]
    last_err = None
    for base in order:
        try:
            req = urllib.request.Request(base + path,
                                          headers={"User-Agent": _RADIO_UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if not _radio_mirror["base"]:
                _radio_mirror["base"] = base
                sys.stderr.write(f"[radio] mirror OK: {base}\n")
                sys.stderr.flush()
            return data
        except Exception as exc:  # mirror chet -> thu vien ke tiep
            last_err = exc
    raise RuntimeError(f"radio-browser API loi: {last_err}")


def _radio_sort(query_norm: str, items: list) -> list:
    """Tram chua bi danh dau loi len truoc, roi do khop ten, roi luot nghe.

    Tram vua loi (URL chet: playlist 200 nhung segment khong tai duoc — gap
    that voi ZING BOLERO HLS cua Zing CDN) bi day xuong cuoi de LLM/nguoi dung
    khong ton 12-20s watchdog moi lan xin lai."""
    return sorted(items,
                  key=lambda s: (not _radio_is_bad(s["url"]),
                                 _title_score(query_norm, s["name"]),
                                 s.get("clicks", 0)),
                  reverse=True)


def _radio_search(query: str, limit: int = 40) -> list:
    """Tim dia theo ten.

    KHONG loc theo codec/HLS: nguon chi can DOC DUOC BOI FFMPEG la du, vi
    firmware chi nhan MP3 24 kHz mono tu /radio (server transcode). Loc HLS
    o day la loi thiet ke — nhieu dia Viet Nam (VD VOV) chi co playlist.m3u8.
    Sap xep theo DO KHOP TEN truoc, clickcount sau: API byname khop chuoi con
    nen "VOV" keo ve ca tram Nga (Novovoronezh)."""
    q = query.strip().lower()
    with _lock:
        hit = _radio_search_cache.get(q)
    if hit and time.time() - hit["t"] < 3600:
        # Sap xep lai ca nhanh cache: tram co the bi danh dau loi SAU khi
        # cache duoc tao (xem _radio_mark_bad).
        return _radio_sort(q, hit["list"])
    path = ("/json/stations/byname/" + urllib.parse.quote(query.strip()) +
            f"?limit={limit}&order=clickcount&reverse=true&hidebroken=true")
    raw = _radio_api(path)
    out = []
    for st in (raw if isinstance(raw, list) else []):
        url = (st.get("url_resolved") or st.get("url") or "").strip()
        if not url:
            continue
        codec = str(st.get("codec") or "UNKNOWN").upper()
        hls = bool(st.get("hls")) or \
            url.lower().split("?")[0].endswith(".m3u8")
        out.append({
            "name": (st.get("name") or "").strip(),
            "country": (st.get("country") or "").strip(),
            "tags": (st.get("tags") or "").strip(),
            "bitrate": int(st.get("bitrate") or 0),
            "codec": codec,
            "hls": hls,
            "clicks": int(st.get("clickcount") or 0),
            "url": url,
        })
    out = _radio_sort(q, out)
    for s in out:
        s.pop("clicks", None)  # khong can gui cho LLM
    with _lock:
        _radio_search_cache[q] = {"t": time.time(), "list": out}
    return out


def _radio_pick(name: str) -> dict:
    """Chon dia khop ten nhat: tim trong cac ket qua da search truoc (de LLM
    chon tu danh sach), khong thay thi tim moi bang ten do. Tram dang bi danh
    dau loi duoc bo qua neu con lua chon khac."""
    want = _norm_title(name)
    cands = []
    with _lock:
        cached = list(_radio_search_cache.values())
    for hit in cached:
        cands.extend(hit["list"])
    if not cands or max((_title_score(want, c["name"]) for c in cands),
                        default=0) < 0.34:
        cands.extend(_radio_search(name, 24))
    good = [c for c in cands if not _radio_is_bad(c["url"])]
    pool = good or cands
    best, best_score = None, 0.0
    for st in pool:
        s = _title_score(want, st["name"])
        if s > best_score:
            best, best_score = st, s
    if best is None:
        raise RuntimeError(f"khong tim thay dia '{name}'")
    if not good and best is not None:
        sys.stderr.write(f"[radio] canh bao: moi tram khop '{name}' deu dang "
                         f"bi danh dau loi — thu lai tram cu\n")
        sys.stderr.flush()
    return best


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
# Nguon nhac (YouTube + SoundCloud) — day la lop DAN PHONG chinh khong phai
# phu luc.
#
# Ly do: cookies/proxy/POT chi giai quyet tam thoi. Da xay ra nhieu lan
# "duoc vai bua lai hong": YouTube chan IP datacenter (bot-check), cookie het
# han, proxy doi IP lam session bi revoke. Service nhac bi bo rat nhieu.
# SoundCloud thi nguon doc lap: khong can cookies, khong can PO token, khong
# bi chan theo IP (da verify: search + resolve + CDN HLS tra ve binh thuong
# tu IP datacenter). Nguon doc lap => service con song khi YouTube che.
#
# Zing MP3 KHONG dua vao day — da do thi bang API that, khong phai do la doan:
#   1. Search API tra {"err":-403,"msg":"You don't have permission"} — apiKey
#      nhung trong extractor da bi Zing thu hoi, va Zing khong cong bo key moi.
#      Khong co search = khong tim duoc bai theo ten (chi biet ID la moi lay).
#   2. Resolve bai that: yeu cau tai khoan VIP ("The song is only for VIP
#      accounts") — phan lon bai Vpop/Vpopular phai VIP, so voi robot la
#      phan lon bai khong lay duoc.
#   3. _GEO_COUNTRIES=['VN'] trong extractor: yeu cau cookies de qua
#      geo-check; Render dat o US nen tang them mot tang chong chan nua.
#   Ket qua: chi dung duoc khi biet san URL Zing + bai free + co cookies VN.
# (Canh bao "khong ho tro zing" in o duoi, sau khi SOURCES da parse.)
# ---------------------------------------------------------------------------
_SOURCE_SEARCH_KEY = {"soundcloud": "scsearch", "youtube": "ytsearch"}
# SoundCloud đứng đầu: không cần cookies/PO token, không bị bot-check theo IP
# (đã verify end-to-end), nên là nguồn ổn định nhất. YouTube để sau làm dự
# phòng cho bài chỉ có trên YouTube. Đổi thứ tự bằng YTDLP_SOURCES.
_DEFAULT_SOURCES = "soundcloud,youtube"


def _parse_sources(raw: str) -> tuple:
    out = []
    for s in str(raw or "").split(","):
        s = s.strip().lower()
        if not s:
            continue
        if s not in _SOURCE_SEARCH_KEY:
            sys.stderr.write(
                f"[music] YTDLP_SOURCES: bo qua nguon '{s}' (hop le: "
                f"{', '.join(_SOURCE_SEARCH_KEY)})\n")
            continue
        if s not in out:
            out.append(s)
    return tuple(out) or (_DEFAULT_SOURCES.split(",")[0],)


SOURCES = _parse_sources(os.environ.get("YTDLP_SOURCES", _DEFAULT_SOURCES))
if "zing" in os.environ.get("YTDLP_SOURCES", "").lower():
    # _parse_sources da bo qua 'zing' (khong nam trong _SOURCE_SEARCH_KEY) va
    # in canh bao; dong nay nho la them mot lan nua cho biet ly do.
    sys.stderr.write(
        "[music] nguon 'zing' khong ho tro: search API -403 (apiKey bi thu hoi), "
        "phan lon bai la VIP, va geo-restricted VN\n")
# Format ưu tiên của SoundCloud: progressive http_mp3 (đi _fetch_chunked +
# _transcode_local, nhanh/resume được) trước HLS hls_aac_160k (m3u8 -> phải
# _download_ffmpeg, chậm và reconnect nhiều). Có fallback về bestaudio/best.
_SC_FORMAT = "bestaudio[protocol^=http]/bestaudio/best"
# So candidate (ung vien) moi nguon thu them khi ung vien dau that bai.
RESOLVE_CANDIDATES = max(1, int(os.environ.get("YTDLP_RESOLVE_CANDIDATES", "2")))
# Nguon vua gap bot-check -> tam bo qua bao giay (mac dinh 15 phut) cho cac
# request sau. Ly do: YouTube that bai ton mot ladder 4 tier ~10-20s moi bai;
# khi IP da bi chan thi doi lan tiep cung that. Source nao giu duoc thi thu
# lai; dat 0 de tat cooldown (moi request deu thu lai).
SOURCE_COOLDOWN = max(0, int(os.environ.get("YTDLP_SOURCE_COOLDOWN", "900")))

# Prefetch: search vừa tìm ra bài khớp tuyệt đối thì tải luôn trong lúc agent
# còn đang suy nghĩ/chờ -> lúc robot phát, file đã có sẵn, độ trễ về 0.
# BẬT MẶC ĐỊNH: băng thông/dĩa Render free chịu được, và độ trễ là thứ user
# cảm nhận trực tiếp. Tắt bằng MUSIC_PREFETCH=0 nếu muốn tiết kiệm.
PREFETCH = os.environ.get("MUSIC_PREFETCH", "1").strip().lower() not in (
    "0", "false", "no", "off")
# Ngưỡng khớp tên để prefetch: Dice score. 0.9 = phải gần như trùng tên bài
# (sai thì thành tải nhầm + tốn băng thông vô ích).
_PREFETCH_MIN_SCORE = 0.9
_source_fail_until = {}
_src_lock = threading.Lock()


def _source_cooldown_active(source: str) -> bool:
    until = _source_fail_until.get(source)
    if not until or until <= time.time():
        return False
    return True


def _mark_source_failed(source: str) -> None:
    if SOURCE_COOLDOWN <= 0:
        return
    with _src_lock:
        _source_fail_until[source] = time.time() + SOURCE_COOLDOWN


def _clear_source_failed(source: str) -> None:
    with _src_lock:
        _source_fail_until.pop(source, None)


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


def _entry_from_info(title: str, info: dict, source: str = "youtube") -> dict:
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
        "source": source,
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


def _extract_one(target: str, title: str, source: str, clients=(), fmt=None,
                 search_key: str = None) -> dict:
    """Mot lan extract cua yt-dlp -> entry (direct_url + headers).
    `target` la URL cua bai (candidate) hoac chuoi ten (khi search_key du dung
    default_search cua yt-dlp de tim bai)."""
    opts = {"quiet": True, "noplaylist": True}
    if search_key:
        opts["default_search"] = search_key
    if fmt:
        opts["format"] = fmt
    if clients:
        opts["extractor_args"] = {
            "youtube": {"player_client": list(clients)}}
    _apply_ydl_auth(opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)
    if not info:
        raise yt_dlp.utils.DownloadError("extract trả về rỗng")
    return _entry_from_info(title, info, source)


def _resolve_youtube(title: str) -> dict:
    # Ladder phòng hờ "Requested format is not available": một số video không
    # khớp selector hẹp (formats bị drop khi thiếu JS runtime / format lạ)
    # -> nới dần bestaudio[abr<=192] -> bestaudio -> best -> default, mới bỏ cuộc.
    _format_tiers = (
        "bestaudio[abr<=192]/bestaudio[abr<=128]/bestaudio/best",
        "bestaudio/best",
        "best",
        None,  # selector mặc định của yt-dlp
    )
    last_err = None
    won_via = ""
    tried = []
    entry = None
    got_info = False
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
            try:
                entry = _extract_one(title, title, "youtube", todo, fmt,
                                     search_key="ytsearch1")
                got_info = True
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
        if got_info:
            # URL chưa tải được thật (403...) thì tier này vô dụng: robot chỉ
            # nghe được silence primer -> thử tier client kế tiếp.
            if _probe_direct_url(entry):
                won_via = label
                break
            sys.stderr.write(
                f"[music] tier '{label}' cho URL khong tai duoc "
                f"-> thu tier ke tiep\n")
            sys.stderr.flush()
            entry = None
            got_info = False
        if label not in tried:
            tried.append(label)
    # Hết cả ladder -> thử vài video khác cùng tên (xếp hạng theo độ khớp tên).
    # ytsearch1 hay trúng video 1 phút/live/cover -> thay vì bỏ cả bài thì thử
    # bài kế tiếp. Bỏ qua kết quả #1 vì ladder vừa thử nó rồi.
    if entry is None and not _is_client_level_err(last_err):
        for cand in _rank_candidates("youtube", title, RESOLVE_CANDIDATES,
                                     skip=1):
            try:
                cand_entry = _extract_one(
                    cand["youtube_url"], cand.get("title") or title,
                    "youtube", fmt=_format_tiers[0])
            except Exception as e:  # noqa: BLE001 - thử video tiếp theo
                last_err = e
                sys.stderr.write(
                    f"[music] candidate youtube that bai: {str(e)[:120]}\n")
                sys.stderr.flush()
                continue
            if _probe_direct_url(cand_entry):
                entry = cand_entry
                tried.append("candidate")
                won_via = "candidate"
                break
    if entry is not None:
        if entry.get("client") in ("", "n/a"):
            # Nhánh HLS/m3u8 không mang '__yt_dlp_client' -> dùng tên tier làm
            # manh mối client.
            entry["client"] = won_via
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
        f"youtube: không lấy được format nào cho '{title}' "
        f"(tier_da_thu={tried or ['default']}, "
        f"deno={'yes' if shutil.which('deno') else 'NO'}, "
        f"yt-dlp={yt_dlp.version.__version__}, "
        f"cookies={'on' if COOKIES_FILE else 'off'}, "
        f"proxy={'on' if YTDLP_PROXY else 'off'}, "
        f"pot={'on' if POT_AVAILABLE else 'off'}, "
        f"pot_srv={'up' if _srv else ('down' if POT_AVAILABLE else 'n/a')}): "
        f"{last_err}{_hint}")


def _resolve_soundcloud(title: str) -> dict:
    """SoundCloud: không có client ladder, không cần cookies/POT, không bị
    bot-check theo IP. Ứng viên xếp hạng theo độ khớp tên rồi thử tuần tự.

    Ưu tiên format progressive (http_mp3) trước HLS: SoundCloud có cả
    hls_aac_160k (m3u8) và http_mp3. m3u8 buộc phải đi _download_ffmpeg
    (ffmpeg tải+encode một lần, chậm và dễ reconnect), còn progressive đi
    _fetch_chunked + _transcode_local như YouTube — nhanh và resume được.
    Selector có fallback về bestaudio/best nếu không có bản progressive.
    """
    if _looks_like_url(title):
        cands = [{"title": title, "youtube_url": title}]
    else:
        cands = _rank_candidates("soundcloud", title, RESOLVE_CANDIDATES)
    if not cands:
        raise RuntimeError("soundcloud: search không có kết quả cho tên này")
    last_err = None
    for cand in cands:
        try:
            entry = _extract_one(
                cand["youtube_url"], cand.get("title") or title, "soundcloud",
                fmt=_SC_FORMAT)
        except Exception as e:  # noqa: BLE001 - thử ứng viên tiếp theo
            last_err = e
            sys.stderr.write(
                f"[music] soundcloud candidate '{cand.get('title')}' that bai: "
                f"{str(e)[:120]}\n")
            sys.stderr.flush()
            continue
        if not _probe_direct_url(entry):
            last_err = RuntimeError("URL không tải được (probe)")
            continue
        if entry.get("client") in ("", "n/a"):
            entry["client"] = "soundcloud"
        sys.stderr.write(
            f"[music] soundcloud OK '{entry['title']}' "
            f"(format_id={entry.get('format_id') or 'n/a'})\n")
        sys.stderr.flush()
        return entry
    raise RuntimeError(
        f"soundcloud: hết {len(cands)} ứng viên cho '{title}', last={last_err}")


def _resolve(key: str, title: str) -> dict:
    """Dispatcher đa nguồn: thử lần lượt các nguồn theo SOURCES. Nguồn vừa
    gặp bot-check được tạm bỏ qua (cooldown) để request sau không mất thêm
    10-20 s chờ một nguồn đang chết."""
    with _lock:
        cached = _resolved.get(key)
    if cached and cached.get("expires", 0) > time.time():
        return cached
    errors = []
    for idx, src in enumerate(SOURCES):
        if _source_cooldown_active(src):
            errors.append(f"{src}: đang cooldown (nguồn vừa bị chặn)")
            continue
        t0 = time.time()
        try:
            entry = (_resolve_soundcloud(title) if src == "soundcloud"
                     else _resolve_youtube(title))
        except Exception as e:  # noqa: BLE001 - thử nguồn tiếp theo
            errors.append(str(e))
            # Chỉ bot-check mới đáng đánh dấu cooldown; "không tìm thấy bài"
            # là chuyện của bài đó, không phải nguồn chết.
            if _is_client_level_err(e):
                _mark_source_failed(src)
            sys.stderr.write(
                f"[music] nguon '{src}' that bai sau {time.time() - t0:.0f}s\n")
            sys.stderr.flush()
            continue
        entry["source"] = src
        with _lock:
            _resolved[key] = entry
        sys.stderr.write(
            f"[music] resolve OK nguon='{src}' sau {time.time() - t0:.0f}s "
            f"({idx + 1}/{len(SOURCES)} nguon)\n")
        sys.stderr.flush()
        return entry
    raise RuntimeError(
        f"không lấy được '{title}' từ bất kỳ nguồn nào (SOURCES="
        f"{','.join(SOURCES)}): " + " || ".join(errors))

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
        rec = _preload.setdefault(key, {})
        rec.update(fields)
        # Cờ prefetch chỉ tồn tại để "một việc nền" dùng làm khoá. Khi
        # worker kết thúc (kể cả failed) mà quên xoá, khoá đó kẹt vĩnh viễn
        # và mọi prefetch sau đều bị bỏ qua -> sau bài đầu tiên thì hết tải
        # trước. Xoá ở đây để mọi đường thoát đều tự dọn.
        if fields.get("state") in ("ready", "failed"):
            rec["prefetch"] = False


# Trạng thái preload giữa các mốc:
#   "running" (resolve + tải source) -> "encoding" (ffmpeg đang ghi file
#   .part, file LỚN DẦN) -> "ready" | "failed".
# Mốc "encoding" tồn tại để /stream theo file đang lớn thay vì chờ xong mới
# phát (xem _follow_encoding) — đây là thứ bỏ 26s im lặng trên Render.
_PRELOAD_ENCODING = "encoding"


def _publish_atomic(part: str, final: str) -> None:
    """Đổi tên .part -> .mp3 (atomic cho người đọc). Có thử lại vài lần: trên
    Windows os.replace fail (PermissionError) nếu đang có một đọc file .part
    mở song song — chỉ xảy ra ở máy dev, Render (Linux) không bị."""
    for attempt in range(5):
        try:
            os.replace(part, final)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _count_cache_files() -> int:
    try:
        return len([n for n in os.listdir(MEDIA_DIR)
                    if n.endswith(".mp3") and not n.startswith("_")])
    except OSError:
        return 0


def _preload_busy(key: str) -> bool:
    """Bài đang được preload viết ra đĩa. Phải gộp CẢ "encoding" chứ không
    chỉ "running": sau khi tải xong source, ffmpeg encode vào .part và
    _stream_body đang phát chính file đó cho robot. Nếu _prune_cache chỉ nhìn
    "running" thì nó sẽ xoá .part giữa lúc robot đang nghe -> bài bị cụt."""
    return _preload_state(key) in ("running", _PRELOAD_ENCODING)


def _prune_cache() -> None:
    """Giu toi da _CACHE_MAX_FILES bai (moi bai ~1-12 MB; Render free chi co
    ~1 GB dia). Bo bai cu nhat truoc, khong dot bai dang preload.

    Quan trong: file tam `.part` / `.src` CUNG phai duoc don. Preload that bai
    giua chung (het deadline tai, ffmpeg loi) bo lai chung 5-12 MB ma khong
    bao gio doi toi file `.mp3` -> khong bao gio duoc don, dia Render day dan
    sau vai chuc bai. Chi xoa file cua bai KHONG con preload dang chay
    (xem _preload_busy: phai tinh ca "encoding")."""
    try:
        names = [n for n in os.listdir(MEDIA_DIR)
                 if n.endswith(".mp3") and not n.startswith("_")]
        temps = [n for n in os.listdir(MEDIA_DIR)
                 if n.endswith((".part", ".src")) and not n.startswith("_")]
    except OSError:
        return

    for n in temps:
        if _preload_busy(os.path.splitext(n)[0]):
            continue  # dang ghi: dung xoa, con dung cho lan ghi hien tai
        try:
            os.remove(os.path.join(MEDIA_DIR, n))
            sys.stderr.write(f"[music] prune temp: {n}\n")
            sys.stderr.flush()
        except OSError:
            pass

    if len(names) <= _CACHE_MAX_FILES:
        return

    def _mtime(n):
        try:
            return os.path.getmtime(os.path.join(MEDIA_DIR, n))
        except OSError:
            return 0.0

    names.sort(key=_mtime)
    for n in names[: len(names) - _CACHE_MAX_FILES]:
        if _preload_busy(n[:-4]):
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
            # Chuyen ve "running" o DAU moi chu ky, khong giu "encoding" cua
            # chu ky truoc: vua xoa .part (dong 1410) nen reader theo file do
            # se doc trong file rong / file dang bi xoa. "running" bao cho
            # _stream_body quay lai gui silence primer cho an toan.
            _preload_state_update(key, state="running")
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
                # .part da la MP3 hoan chinh ngay tu dau -> danh dau "encoding"
                # de robot theo file dang lon thay vi cho tai xong.
                _preload_state_update(key, state=_PRELOAD_ENCODING,
                                      note="ffmpeg-m3u8")
                _download_ffmpeg(url, headers, part)
                t_download = time.time() - t_step
                t_transcode = 0.0
            else:
                _fetch_chunked(url, headers, src)
                src_bytes = _size_of(src)
                t_download = time.time() - t_step
                t_step = time.time()
                # .part bat dau la MP3 24 kHz mono dung chuan cua firmware va
                # ffmpeg ghi progressive -> robot co the nghe ngay.
                _preload_state_update(key, state=_PRELOAD_ENCODING,
                                      note="transcode")
                _transcode_local(src, part)
                t_transcode = time.time() - t_step
                try:
                    os.remove(src)
                except OSError:
                    pass
            if _size_of(part) < _MEDIA_MIN_BYTES:
                raise RuntimeError(
                    f"mp3 qua nho sau transcode: {_size_of(part)}")
            _publish_atomic(part, final)  # reader khong thay file nua do
            _preload_state_update(key, state="ready", err="")
            # Chia thoi gian theo cong doan: resolve (mang/POT) - tai
            # (googlevideo/proxy) - transcode (CPU instance). Day la so lieu de
            # biet nut that that su thay vi toi uu nham cho.
            sys.stderr.write(
                f"[music] preload OK '{entry['title']}' ({_size_of(final)} B, "
                f"{time.time() - t0:.0f}s = resolve {t_resolve:.0f}s + tai "
                f"{t_download:.0f}s ({src_bytes or _size_of(part)} B) + "
                f"transcode {t_transcode:.0f}s, cycle {cycle + 1}, "
                f"source={entry.get('source') or 'n/a'}, "
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
    # Don file tam cua bai nay + file tam cua cac bai that bai truoc do. Khong
    # goi o nhanh thanh cong -> file .part/.src con lai tu bai that bai se
    # ngay lai chua 5-12 MB ma khong bao gio duoc quet lai.
    for p in (_part_path(key), _src_path(key)):
        try:
            os.remove(p)
        except OSError:
            pass
    _prune_cache()


def _preload_start(key: str, title: str) -> None:
    """Khoi tai/preload bai (single-flight). Goi tu get_song_url (truoc khi
    robot GET) va tu route /stream (neu get_song_url chua run).

    Phai kiem tra `_preload_busy` (ca "running" LAN "encoding"), KHONG chi
    "running": robot goi get_song_url (spawn worker 1) roi GET /stream
    (goi lai ham nay). Neu chi so "running", luc worker 1 da chuyen sang
    "encoding" (sau khi tai xong source) thi lan goi thu hai KHONG bi chan
    -> spawn worker 2 cho cung key. Hai worker cung tai va cung chay ffmpeg
    vao MOT file .part: robot nhan audio hong (hai encoder ghi lech offset
    giua nhau) + bang thong 2 lan. Do la nguyen nhan log in trung
    "chunked OK ..." va "primer->encode switch ..." hai lan."""
    with _lock:
        st = (_preload.get(key) or {}).get("state")
        if st in ("running", _PRELOAD_ENCODING):
            return
        if st == "ready" and _size_of(_final_path(key)) > 0:
            return
        _preload[key] = {"state": "running", "t0": time.time(), "err": ""}
    threading.Thread(target=_preload_worker, args=(key, title),
                     daemon=True).start()


def _ffmpeg_chunks(direct_url: str, headers: dict = None,
                    live: bool = False):
    # 1 lần convert duy nhất: nguồn -> PCM (swr + filter DSP) -> MP3 160 kbps.
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "warning",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
    ]
    # -reconnect_at_eof CHI dung cho VOD (file googlevideo dut giua chung):
    # voi stream LIVE (radio HLS...) moi segment ket thuc bang EOF binh
    # thuong -> co at_eof lam ffmpeg treo vong lap reconnect ma khong ra byte
    # nao (do duoc tren live VOV: 0 B voi at_eof, 262 KB khong co).
    if not live:
        cmd += ["-reconnect_at_eof", "1"]
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


# Byte primer (silence MP3) da gui cho tung key. Firmware dem `stream_pos` =
# TONG byte da nhan (gồm cả primer), nen khi client gui Range ta can biet
# bao nhieu byte primer da di truoc do de moi tinh duoc offset trong file
# nhac. Ghi o day ngay luc chuyen tu primer sang file.
_primer_sent = {}


def _note_primer(key: str, n: int) -> None:
    with _lock:
        _primer_sent[key] = max(_primer_sent.get(key, 0), n)


def _primer_for(key: str) -> int:
    with _lock:
        return _primer_sent.get(key, 0)


def _follow_encoding(key: str, file_off: int, t0: float, ctx: dict):
    """Phát theo file .part đang được ffmpeg ghi (state="encoding"), thay vì
    chờ encode xong rồi mới chuyển sang file.

    Vì sao cần: Render free throttle CPU ~14 lần so với máy dev, nên transcode
    một bài 3 phút mất 26s — chờ xong thì robot ngồi im 26s. Nhưng ffmpeg ghi
    MP3 progressive: byte đầu tiên có mặt sau chưa tới 1s, và tốc độ ghi
    (~160 KB/s) VƯỢT xa tốc độ ESP32 kéo (~20 KB/s) -> robot nghe được ngay
    rồi nhận nốt phần còn lại, không bao giờ đói giữa bài.

    Firmware TỰ gửi `Range: bytes=<stream_pos>-` khi nối lại (xem
    music_player.cc), nên khi robot bị Render reset giữa bài nó nhận tiếp
    đúng phần còn lại thay vì nghe lại từ đầu. `file_off` ở đây là offset
    trong FILE NHẠC đã trừ phần primer đã gửi (xem _stream_body).

    Ghi chú đọc file: mỗi vòng mở/đóng lại theo tên để không giữ handle khi
    file bị đổi tên .part -> .mp3 (giữ handle sẽ chặn os.replace trên
    Windows và gây stale-offset trên Linux)."""
    final, part = _final_path(key), _part_path(key)
    while True:
        # Sau khi đổi tên, .part biến mất -> đọc nốt từ .mp3.
        path = part if _size_of(part) > file_off else final
        if _size_of(path) > file_off:
            try:
                with open(path, "rb") as f:
                    f.seek(file_off)
                    buf = f.read(64 * 1024)
            except OSError:
                buf = b""
            if buf:
                file_off += len(buf)
                ctx["file_off"] = file_off
                yield buf
                continue
        state = _preload_state(key)
        if state == "ready":
            # Đã đổi tên: đọc nốt phần cuối (thường đã hết ở nhánh trên).
            if _size_of(final) > file_off:
                continue
            ctx["reason"] = "done"
            return
        if state == "failed":
            ctx["reason"] = "failed"
            return
        if time.time() - t0 > _BODY_WAIT_MAX:
            ctx["reason"] = "timeout"
            return
        time.sleep(0.2)  # file chưa lớn thêm -> đợi, không gửi byte rỗng


def _live_fallback(key: str, title: str, entry: dict, reason: str):
    """Fallback cuối: pipe ffmpeg trực tiếp từ direct URL (preload hỏng hoặc
    quá _BODY_WAIT_MAX) — hành vi cũ. Trả None nếu resolve lỗi (hết đường ->
    đóng response để firmware thử lại)."""
    sys.stderr.write(f"[music] {reason} -> live pipe fallback key={key}\n")
    sys.stderr.flush()
    with _lock:
        cached = _resolved.get(key)
    if cached and cached.get("expires", 0) > time.time():
        entry = cached  # URL preload đã resolve sẵn -> không tốn extract
    else:
        try:
            entry = _resolve(key, title)  # URL mới nhất cho fallback
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[music] live pipe fallback resolve loi: {e}\n")
            sys.stderr.flush()
            return  # hết đường -> đóng response de firmware thu lai
    return _ffmpeg_chunks(entry["direct_url"], entry.get("http_headers"))


def _stream_body(key: str, title: str, entry: dict = None, start: int = 0):
    """Body cho ket noi /stream chua co file (4 truong hop).

    `entry` chi can khi phai fallback live pipe; route /stream truyen None de
    khong resolve dong bo (tranh extract lan 2 chay song song voi preload).
      1. File xong    -> Doc toan bo file, dung (primer neu da gui ~0.4s).
      2. Dang ENCODE  -> Theo file .part dang lon, phat ngay khi co byte dau
                         tien (xem _follow_encoding). Day la nhanh nhat.
      3. Dang tai src -> primer silence pace ~realtime, cho den khi (2)/(1).
      4. Preload loi / qua _BODY_WAIT_MAX -> live pipe giam cap.
    """
    final, part = _final_path(key), _part_path(key)
    t0 = time.time()
    # `start` > 0 = client ket noi lai bang "Range: bytes=start-". Tong byte
    # client da nhan = primer + offset trong file, nen tach ra:
    #   start <  p -> van dung o vung primer, gui nốt primer rồi vào file 0
    #   start >= p -> het primer, vao thang file tai `start - p`
    p = _primer_for(key) if start > 0 else 0
    resume_off = max(0, start - p)
    primer_left = max(0, p - start)  # so byte primer con phai gui
    primer_sent = min(start, p)
    # Client da nghe du phan primer cua connection truoc -> khong phai phat
    # lai (tranh tua 1-2 giay "im" moi truoc khi nhac vao lai).
    primed = start > 0
    silence = b""
    off = 0
    silence_err = False
    while True:
        state = _preload_state(key)
        if _size_of(final) > 0:
            if primer_sent:
                _note_primer(key, primer_sent)
            if primed and time.time() - t0 > 0.5:
                sys.stderr.write(
                    f"[music] primer->file switch key={key} sau "
                    f"{time.time() - t0:.1f}s\n")
                sys.stderr.flush()
            try:
                with open(final, "rb") as f:
                    if resume_off:
                        f.seek(resume_off)
                    while True:
                        buf = f.read(32 * 1024)
                        if not buf:
                            return
                        yield buf
            except OSError as e:
                sys.stderr.write(f"[music] doc file loi: {e}\n")
                sys.stderr.flush()
                return
        if state == _PRELOAD_ENCODING and _size_of(part) > 0:
            # ffmpeg dang ghi .part -> phat theo ngay, khong cho encode xong.
            if primed:
                sys.stderr.write(
                    f"[music] primer->encode switch key={key} sau "
                    f"{time.time() - t0:.1f}s (theo file dang encode)\n")
                sys.stderr.flush()
            if primer_sent:
                _note_primer(key, primer_sent)
            ctx = {"file_off": resume_off, "reason": ""}
            yield from _follow_encoding(key, resume_off, t0, ctx)
            if ctx["reason"] == "done":
                return
            reason = ("preload failed" if ctx["reason"] == "failed"
                      else f"cho file qua {_BODY_WAIT_MAX}s")
            yield from _live_fallback(key, title, entry, reason) or ()
            return
        if state == "failed" or time.time() - t0 > _BODY_WAIT_MAX:
            reason = ("preload failed" if state == "failed"
                      else f"cho file qua {_BODY_WAIT_MAX}s")
            yield from _live_fallback(key, title, entry, reason) or ()
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
        if primer_left <= 0 and start > 0:
            # Da gui du phan primer ma client da nghe -> vao vong cho file.
            continue
        n = min(8 * 1024, len(silence) - off)
        if n <= 0:
            off = 0
            continue
        if primer_left > 0:
            n = min(n, primer_left)
        primed = True
        yield silence[off:off + n]
        off = (off + n) % len(silence)
        primer_sent += n
        if primer_left > 0:
            primer_left -= n
        # Pace ~realtime: neu gap hon toc do robot phat thi backlog im bi
        # gom trong buffer va robot phai nghe no TRUOC bai that sau khi switch.
        time.sleep(n / _PRIMER_PACE)


def _range_start(request) -> int:
    """Offset bat dau theo header Range cua client, hoac 0 neu khong co.

    Firmware (music_player.cc) khi ket noi lai KHONG gui Range -> server phuc
    vu file tu byte 0 -> robot nghe lai tu dau bai (mat ~20 giay da phat).
    Khi firmware sua de gui Range, ham nay tra offset dung va phuc vu phan con
    lai.

    Chi chap nhan "bytes=N-" va "bytes=N-M". Range mau/phuc vu theo tung doan
    (multipart) bi bo qua -> tra 0 cho an toan; client se thu lai khong kem
    Range va van chay dung (chi ton them mot vong ket noi)."""
    rng = (request.headers.get("range") or "").strip()
    if not rng.lower().startswith("bytes="):
        return 0
    spec = rng[6:].split(",")[0].strip()
    if "-" not in spec:
        return 0
    # `bytes=-100` (suffix range) co chu '-' nhung `first` rong -> int("") nem
    # ValueError -> tra 0. Neu de qua, client se nhan tu byte 0 thay vi
    # 100 byte cuoi, van la "chay sai". Tra 0 an toan cho ca truong hop.
    first, _, last = spec.partition("-")
    try:
        start = int(first)
        if last.strip():
            int(last)  # "N-M": co gan canh, van dung
    except ValueError:
        return 0
    return start if start > 0 else 0


def _partial_file(path: str, start: int, size: int):
    """206 Partial Content cho /stream: tra phan con lai cua file MP3 da
    encode xong. Client nghe tiep dung tu offset, khong phai tu byte 0."""
    def _iter():
        with open(path, "rb") as f:
            f.seek(start)
            while True:
                buf = f.read(64 * 1024)
                if not buf:
                    break
                yield buf
    return StreamingResponse(
        _iter(),
        status_code=206,
        media_type="audio/mpeg",
        headers={
            "Content-Range": f"bytes {start}-{size - 1}/{size}",
            "Content-Length": str(size - start),
            "Accept-Ranges": "bytes",
        })


@stream_app.get("/stream/{key}.mp3")
def stream(key: str, request: Request):
    # File da co tren dia -> phuc vu ngay (Content-Length, khong can resolve).
    # Con song duoc sau khi Render restart: _titles mat nhung file con lai.
    size = _size_of(_final_path(key))
    if size > 0:
        start = _range_start(request)
        if start <= 0:
            return FileResponse(
                _final_path(key), media_type="audio/mpeg",
                headers={"Accept-Ranges": "bytes"})
        if start >= size:
            # Client xin offset da het file -> 416 + Content-Range de client
            # dung lai thay vi lap vo han (chi xay ra voi client chuan HTTP;
            # firmware hien tai khong gui Range).
            return Response(status_code=416, headers={
                "Content-Range": f"bytes */{size}",
                "Accept-Ranges": "bytes",
                "Content-Type": "audio/mpeg",
            })
        # 206 + Content-Range: client biet chinh xac no nhan phan nao, nen
        # khong the nao gi la audio lap lai tu dau bai.
        return _partial_file(_final_path(key), start, size)
    with _lock:
        title = _titles.get(key)
    if not title:
        raise HTTPException(404, "unknown stream id - hay goi get_song_url truoc")
    # KHONG resolve dong bo o day: preload thread da/dang resolve -> resolve lai
    # o day la extract thu 2 chay song song (log 2 dong "resolved") va giu ket
    # noi cua robot cho toi khi extract xong (khong gui duoc primer).
    # _stream_body tu lay entry khi phai fallback live pipe.
    _preload_start(key, title)  # idempotent: chay truoc/doi primer o duoi
    # Client ket noi lai giua chung (Render rut reset) gui Range -> phuc vu
    # dung phan con lai. Khong gui Range van chay binh thuong.
    return StreamingResponse(
        _stream_body(key, title, None, _range_start(request)),
        media_type="audio/mpeg",
        headers={"Content-Type": "audio/mpeg", "Accept-Ranges": "bytes"})


# ---------------------------------------------------------------------------
# /radio: stream song dong, khong gioi han do dai (khac /stream la file co dinh)
# ---------------------------------------------------------------------------
def _radio_body(key: str, url: str):
    """Primer ngan + stream MP3 transcode tu truc bo (vo han).

    Primer che ~2.5s lan ffmpeg ket noi truc do (do duoc ~4s); neu ffmpeg ra
    byte TRUOC do thi dung ngay — khong de robot nghe khoang im thua. Thread
    pump dua vao queue de nguoi doc kiem soat toc do (backpressure) — tai
    khong bao gio cham hon nguoi nghe.

    Chong treo + chong tien trinh mo:
    - drain co watchdog 20s: ffmpeg khong ra byte nao (tram chet, network
      sung...) -> dong response, firmware thay mat ket noi nen retry 3 lan
      roi dung sach se, thay vi treo vo thuong trong vong pre-buffer.
    - client ngat: (a) response gen .close() -> finally set stop_evt -> pump
      thoat -> gen.close() kill ffmpeg; (b) ket noi tren Render/uvicorn chi
      that bai sau ~1-2 phut backpressure nen pump co gioi han rieng: queue
      TRUNG lien tuc 30s = coi la client ngat -> break + gen.close(). Moi
      request chi de lai ffmpeg trong vong ~30s, khong tich dan."""
    # 256 chunk x 16 KB = ~4 MB ~ 80s audio: du de buffer WiFi gap (robot
    # co jitter buffer 1.7s) nhung han chet trong truong hop client ngat.
    q = queue.Queue(maxsize=256)
    stop_evt = threading.Event()
    done = object()

    def pump():
        gen = _ffmpeg_chunks(url, live=True)
        full_since = None
        try:
            for chunk in gen:
                if stop_evt.is_set():
                    break
                try:
                    q.put(chunk, timeout=0.5)
                    full_since = None
                except queue.Full:
                    # Queue day = nguoi doc (HTTP response) khong lay ra duoc
                    # -> socket de nghich (client dang nghe). Neu queue TRUNG
                    # 30s liên tuc thi coi la client da ngat (robot stop ->
                    # FIN; Render/uvicorn chi phat hien khi socket write that
                    # bai sau ~1-2 phut backpressure — phai co gioi han de
                    # khong de ffmpeg + 4 MB queue chay oan lan nua).
                    if full_since is None:
                        full_since = time.time()
                    elif time.time() - full_since > 30:
                        sys.stderr.write(
                            "[radio] queue day lien tuc 30s -> coi la client "
                            "dat ngat, dong stream\n")
                        sys.stderr.flush()
                        break
        except Exception as exc:
            sys.stderr.write(f"[radio] ffmpeg loi: {exc}\n")
            sys.stderr.flush()
        finally:
            try:
                gen.close()
            except Exception:
                pass
            # Dax cap CHUONGRU queue (du lieu cua client da mat) de
            # put(done) khong bi chan; de NGUYEN item done: neu reader con
            # song se thuoc duoc `done` va thoat trong sach.
            try:
                while True:
                    _tag, _ = q.get_nowait()
            except queue.Empty:
                pass
            q.put(_qd)

    worker = threading.Thread(target=pump, daemon=True)
    worker.start()
    total = 0
    try:
        primer = _ensure_silence()
        pace = _primer_pace()
        deadline = time.time() + 2.5
        pos = 0
        step = 16 * 1024
        saw_done = False
        while pos < len(primer) and time.time() < deadline:
            try:
                first = q.get(timeout=0.2)
            except queue.Empty:
                piece = primer[pos:pos + step]
                pos += len(piece)
                yield piece
                time.sleep(len(piece) / pace)
                continue
            if first is done:
                saw_done = True   # ffmpeg chet truoc khi ra byte -> dung lai
            else:
                yield first
                total += len(first)
            break
        if saw_done:
            # ffmpeg thoat ngay, 0 byte: URL chet (vd HLS Zing: playlist 200
            # nhung segment host khong phan hoi). Danh dau de lan sau khong
            # chon lai tram nay.
            _radio_mark_bad(url, "ffmpeg thoat ngay, 0 byte")
        else:
            live_deadline = time.time() + 12
            while True:
                try:
                    item = q.get(timeout=1.0)
                except queue.Empty:
                    if time.time() > live_deadline:
                        if total == 0:
                            _radio_mark_bad(url, "khong ra audio trong 12s")
                        sys.stderr.write(
                            f"[radio] khong nhan duoc audio 12s ({total} B) "
                            f"-> dong ket noi: {url[:80]}\n")
                        sys.stderr.flush()
                        return
                    continue
                if item is done:
                    # ffmpeg ket thuc ma khong ra byte nao -> URL chet (phai
                    # bat o DAY nua: ffmpeg co the mat vai giay moi bo cuoc,
                    # luc do primer loop da xong nen nhanh saw_done khong chay).
                    if total == 0:
                        _radio_mark_bad(url, "ffmpeg ket thuc, 0 byte audio")
                    return
                live_deadline = time.time() + 12   # co du lieu -> gia han
                total += len(item)
                yield item
    finally:
        # Nguoi doc di (client ngat/timeout): danh thuc pump dang ket o put,
        # de no thoat vong lap -> dong ffmpeg. Khong co dong nay, moi
        # connection dai se de lai 1 ffmpeg chay mai tren host.
        stop_evt.set()
        try:
            worker.join(timeout=3.0)
        except Exception:
            pass


@stream_app.get("/radio/{key}.mp3")
def radio(key: str):
    with _lock:
        st = _radio_stations.get(key)
    if not st:
        raise HTTPException(404, "unknown radio id - hay goi get_radio_url truoc")
    sys.stderr.write(f"[radio] stream start key={key} '{st['name']}'\n")
    sys.stderr.flush()
    # LUON 200: firmware gui Range khi ket noi lai (attempt 2/3) — radio khong
    # co offset de resume, tra 206/416 la sai. Firmware chap nhan 200 (xem
    # music_player.cc) va reset decoder de ngam tiep am thanh HIENTAI.
    return StreamingResponse(
        _radio_body(key, st["url"]),
        media_type="audio/mpeg",
        headers={"Content-Type": "audio/mpeg", "Cache-Control": "no-store"})


# /health ở ROOT cho Render healthCheckPath — đăng ký qua custom_route nên
# không cần auth; FastAPI không có route này (Mount đặt SAU nên custom_route
# được match trước). KHÔNG được comment decorator này: render.yaml đặt
# healthCheckPath=/health, mất route này thì Render báo deploy failed dù
# server đã chạy tốt.
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "cookies": bool(COOKIES_FILE),
        "pot": POT_AVAILABLE,
        "pot_server": _pot_server_up(timeout=1.0),
        "proxy": bool(YTDLP_PROXY),
        "radio": {
            "mirror": _radio_mirror["base"] or None,
            "stations": len(_radio_stations),
            "queries": len(_radio_search_cache),
        },
        "sources": {
            "enabled": list(SOURCES),
            "cooldown": {k: round(max(0, v - time.time()))
                         for k, v in _source_fail_until.items()
                         if v > time.time()},
            "resolve_candidates": RESOLVE_CANDIDATES,
        },
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
def _maybe_prefetch(res: list, query: str) -> None:
    """Tải trước bài khớp gần như tuyệt đối khi search vừa trả về.

    Vì sao: chuỗi thật của agent là search_song -> (LLM suy nghĩ, đọc danh
    sách, điền khiên robot chờ) -> get_song_url. Giữa hai lệnh đó đã mất vài
    giây, trong khi resolve+download mất 6-9s. Nếu search đã biết bài nào khớp
    tuyệt đối thì tải luôn trong lúc đó -> lúc robot phát, file đã nằm sẵn
    trên đĩa, /stream trả FileResponse ngay, độ trễ về 0.

    Chỉ prefetch khi:
      - bài đứng đầu khớp QUERY >= _PREFETCH_MIN_SCORE (gần như đúng tên bài,
        tránh tải nhầm bài chỉ giống lệch vài từ rồi phí băng thông);
      - bài đó chưa có trong cache và không đang preload;
      - không có bài nào đang prefetch (giới hạn 1 việc nền, tránh tranh
        tải với chính bài robot đang yêu cầu).
    """
    if not PREFETCH or not res or not query:
        return
    top = res[0]
    title = (top.get("title") or "").strip()
    if not title or _title_score(query, title) < _PREFETCH_MIN_SCORE:
        return
    key = _key_of(title)
    with _lock:
        if any(v.get("prefetch") for v in _preload.values()):
            return  # đang prefetch bài khác
        st = (_preload.get(key) or {}).get("state")
        if st in ("running", _PRELOAD_ENCODING) or (
                st == "ready" and _size_of(_final_path(key)) > 0):
            return  # đang chạy hoặc đã có sẵn
        _preload[key] = {"state": "running", "t0": time.time(), "err": "",
                         "prefetch": True}
        _titles[key] = title
    threading.Thread(target=_preload_worker, args=(key, title),
                     daemon=True).start()
    sys.stderr.write(f"[music] prefetch '{title}'\n")
    sys.stderr.flush()


@mcp.tool()
def search_song(keyword: str) -> str:
    """Tìm bài hát theo tên (hoặc tên + ca sĩ). Trả về tối đa 5 kết quả:
    title, uploader, duration_sec, source, youtube_url. Dùng get_song_url để
    lấy stream URL mp3 cho robot phát. Tìm trên TẤT CẢ nguồn đang bật
    (mặc định SoundCloud + YouTube) nên khi YouTube bị bot-check vẫn còn
    kết quả từ nguồn khác. Kết quả đã xếp theo độ khớp tên, nguồn chính
    (SoundCloud) đứng trước nếu bài trùng nhau."""
    res = _search_all(keyword, 5)
    sys.stderr.write(f"[music] search '{keyword}' -> {len(res)} ket qua\n")
    for r in res[:3]:
        sys.stderr.write(
            f"[music]   - {r.get('title')} | {r.get('uploader')} "
            f"[{r.get('source')}]\n")
    sys.stderr.flush()
    _maybe_prefetch(res, keyword)  # tải trước bài khớp nhất (nền)
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


@mcp.tool()
def search_radio(keyword: str) -> str:
    """Tìm đài phát thanh (radio) theo tên/quốc gia/chủ đề. Trả về tối đa 8
    trạm: name, country, tags, bitrate, codec, hls, url (nguồn MP3/AAC/HLS
    đều được — server transcode về MP3 cho robot). Dùng get_radio_url để lấy
    stream_url cho robot. Ví dụ: 'VOV', 'Nhạc trữ tình', 'Vietnam'."""
    res = _radio_search(keyword)
    sys.stderr.write(f"[radio] search '{keyword}' -> {len(res)} tram MP3\n")
    for s in res[:3]:
        sys.stderr.write(
            f"[radio]   - {s['name']} | {s['country']} | {s['bitrate']}kbps\n")
    sys.stderr.flush()
    return json.dumps({"results": res[:8]}, ensure_ascii=False)


@mcp.tool()
def get_radio_url(name: str) -> str:
    """Chuẩn bị một đài phát thanh để robot phát. Trả về NGAY {"status":
    "ready", "stream_url": "..."} — robot gọi self.music.play(stream_url).
    Radio là stream liên tục không có đầu/cuối nên KHÔNG cần chờ tải: robot
    mở URL là nghe trực tiếp. **Lúc phát thì micro bị tắt** (firmware tắt
    codec input để wake-word không cắt ngang phát nhạc) nên không ra lệnh
    bằng giọng được — chỉ có 2 cách dừng: `self.music.stop` từ AI hoặc **nhấn
    nút wakeup trên robot** (board gọi `MusicPlayer::Stop()`). Nên
    gọi search_radio trước để lấy name chính xác của trạm."""
    st = _radio_pick(name)
    key = _key_of("radio:" + st["name"] + "|" + st["url"])
    with _lock:
        _radio_stations[key] = st
    sys.stderr.write(
        f"[radio] pick '{st['name']}' ({st['bitrate']}kbps, {st['country']}) "
        f"-> {key}\n")
    sys.stderr.flush()
    return json.dumps({
        "status": "ready",
        "id": key,
        "stream_url": _radio_url(key),
    }, ensure_ascii=False)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        q = " ".join(sys.argv[2:])
        print(json.dumps(_search_all(q, 3), ensure_ascii=False, indent=2))
    else:
        sys.stderr.write(
            f"[music] transport={MCP_TRANSPORT} port={PORT} "
            f"(base={PUBLIC_BASE or 'auto-LAN'})\n")
        sys.stderr.write(
            f"[music] audio: {AUDIO_SAMPLE_RATE} Hz x{AUDIO_CHANNELS}, "
            f"{AUDIO_BITRATE} mp3, preset={AUDIO_PRESET}\n")
        sys.stderr.write(f"[music] filters: {AUDIO_FILTERS or '(none)'}\n")
        sys.stderr.write(
            f"[music] sources: {', '.join(SOURCES)} "
            f"(candidates/nguon={RESOLVE_CANDIDATES}, "
            f"cooldown={SOURCE_COOLDOWN}s)\n")
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
