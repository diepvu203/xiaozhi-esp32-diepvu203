#!/usr/bin/env python3
"""Verify resolve ladder + stream route behaviour without network access.

Chay: python verify_resolve_ladder.py

Kiem tra (khong goi YouTube):
  1. `_parse_client_ladder` parse dung cu phap tier.
  2. Tier dau bi bot-check -> tu nhay sang tier ke tiep va ghi lai
     `entry["client"]` tu `__yt_dlp_client` cua format.
  3. Tat ca tier fail -> RuntimeError co `tier_da_thu` (khong nuot loi).
  4. `stream()` KHONG resolve dong bo (het extract lan 2 chay song song voi
     preload) va tra streaming body ngay.
  5. `_stream_body` voi `entry=None` (duong route thuc te): gui silence
     primer truoc, va dong response khi preload failed + resolve cung loi.
  6. Da nguon: YouTube bot-check -> tu nhay sang SoundCloud, gan cooldown
     cho nguon chet, va xep hang candidate theo do khop ten.
"""

import os
import sys

import yt_dlp

import server

FAILED = []


def check(name, cond, detail=""):
    status = "OK  " if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


BOT_CHECK = ("ERROR: [youtube] xyz: Sign in to confirm you're not a bot. "
             "Use --cookies-from-browser or --cookies for the authentication.")
# Tier token-free trong ladder mac dinh (xem _DEFAULT_CLIENT_LADDER).
TOKEN_FREE_TIER = ["tv", "web_embedded", "tv_downgraded"]


# ---------------------------------------------------------------------------
# 1. Parse ladder
# ---------------------------------------------------------------------------
check("parse ladder 'a,b|c|'",
      server._parse_client_ladder("a,b|c|") == (("a", "b"), ("c",), ()),
      repr(server._parse_client_ladder("a,b|c|")))
check("ladder rong -> 1 tier rong (client mac dinh)",
      server._parse_client_ladder("") == ((),))
check("ladder mac dinh co nhom token-free o tier 2",
      server._DEFAULT_CLIENT_LADDER.split("|")[1] == "tv,web_embedded,tv_downgraded",
      server._DEFAULT_CLIENT_LADDER)


# ---------------------------------------------------------------------------
# 2/3. Ladder fallback (gia lap yt-dlp)
# ---------------------------------------------------------------------------
class FakeYDL:
    """yt-dlp gia: tier 1 bi bot-check, tier 2 (token-free) thanh cong."""

    calls = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, title, download=False):
        clients = (self.opts.get("extractor_args") or {}).get(
            "youtube", {}).get("player_client")
        FakeYDL.calls.append(clients)
        if clients == TOKEN_FREE_TIER:
            return {
                "title": "Fake Song",
                "url": "https://example.invalid/videoplayback",
                "format_id": "251",
                "abr": 128,
                "__yt_dlp_client": "web_embedded",
            }
        raise yt_dlp.utils.DownloadError(BOT_CHECK)


real_ydl = yt_dlp.YoutubeDL
real_probe0 = server._probe_direct_url
server._probe_direct_url = lambda entry: True  # probe co test rieng o muc 3b
# Các check dưới đây chỉ kiểm ladder YouTube -> khoá SOURCES lại 1 nguồn.
# Phần multi-source được kiểm riêng ở mục 6.
real_sources = server.SOURCES
server.SOURCES = ("youtube",)
yt_dlp.YoutubeDL = FakeYDL
try:
    entry = server._resolve("verifykey1", "fake song")
    check("tier 2 duoc dung sau khi tier 1 bi chan",
          FakeYDL.calls == [None, TOKEN_FREE_TIER],
          f"calls={FakeYDL.calls}")
    check("entry.client lay tu __yt_dlp_client",
          entry.get("client") == "web_embedded", entry.get("client"))
    check("entry.direct_url duoc lay", bool(entry.get("direct_url")))

    class FailYDL(FakeYDL):
        def extract_info(self, title, download=False):
            FakeYDL.calls.append(
                (self.opts.get("extractor_args") or {}).get(
                    "youtube", {}).get("player_client"))
            raise yt_dlp.utils.DownloadError(BOT_CHECK)

    yt_dlp.YoutubeDL = FailYDL
    FakeYDL.calls = []
    try:
        server._resolve("verifykey2", "fake song 2")
        check("tat ca tier fail -> RuntimeError", False, "khong raise")
    except RuntimeError as e:
        check("tat ca tier fail -> RuntimeError co tier_da_thu",
              "tier_da_thu" in str(e), str(e)[:80])
        check("thu du tat ca tier",
              len(FakeYDL.calls) == len(server._CLIENT_TIERS),
              f"{len(FakeYDL.calls)}/{len(server._CLIENT_TIERS)}")
finally:
    yt_dlp.YoutubeDL = real_ydl
    server._probe_direct_url = real_probe0
    with server._lock:
        server._resolved.pop("verifykey1", None)
        server._resolved.pop("verifykey2", None)


# ---------------------------------------------------------------------------
# 6. Đa nguồn: YouTube bot-check -> tự nhảy sang SoundCloud + cooldown
# ---------------------------------------------------------------------------
check("_parse_sources bo qua nguon la",
      server._parse_sources("youtube,zingmp3") == ("youtube",))
check("_parse_sources giu thu tu",
      server._parse_sources("soundcloud,youtube") == ("soundcloud", "youtube"))
check("_parse_sources rong -> fallback youtube",
      server._parse_sources("") == ("youtube",))
check("mac dinh co soundcloud (nguon du phong)",
      "soundcloud" in server._DEFAULT_SOURCES.split(","),
      server._DEFAULT_SOURCES)
check("_title_score: ten trung -> 1.0",
      abs(server._title_score("Tam Thai Tu", "TÂM THÁI TỬ") - 1.0) < 1e-9,
      str(server._title_score("Tam Thai Tu", "TÂM THÁI TỬ")))
check("_title_score: 'Tam Thai Tu English' < 'Tam Thai Tu'",
      server._title_score("TAM THÁI TỬ", "TAM THÁI TỬ Tiếng Anh")
      > server._title_score("TAM THÁI TỬ", "TAM THÁI TỬ Tiếng Anh cover"),
      str(server._title_score("TAM THÁI TỬ", "TAM THÁI TỬ Tiếng Anh")))
check("_looks_like_url nhan dien URL",
      server._looks_like_url("https://soundcloud.com/x/y")
      and not server._looks_like_url("JACK - J97"))


class MultiSourceYDL(FakeYDL):
    """YouTube luôn bot-check; SoundCloud trả format HLS (không client)."""
    order = []

    def extract_info(self, target, download=False):
        opts = self.opts
        if opts.get("extract_flat"):
            # search phang: phan biet nguon bang target "<key><n>:<query>"
            # (scsearch... vs ytsearch...) chu khong phai default_search.
            target = str(target or "")
            if target.startswith("scsearch"):
                return {"entries": [{
                    "title": "Tam Thai Tu (JACK J97)",
                    "url": "https://soundcloud.com/artist/tam-thai-tu",
                    "duration": 245, "extractor_key": "Soundcloud"}]}
            return {"entries": []}
        if "soundcloud" in str(target):
            MultiSourceYDL.order.append("soundcloud")
            return {"title": "Tam Thai Tu", "url": "https://example.invalid/h.m3u8",
                    "format_id": "hls", "ext": "m3u8"}
        MultiSourceYDL.order.append("youtube")
        raise yt_dlp.utils.DownloadError(BOT_CHECK)


yt_dlp.YoutubeDL = MultiSourceYDL
server._probe_direct_url = lambda entry: True
server.SOURCES = ("youtube", "soundcloud")
server._source_fail_until.clear()
MultiSourceYDL.order = []
try:
    entry6 = server._resolve("verifykey6", "Tam Thu Tu JACK J97")
    check("YouTube chan -> tu nhay sang SoundCloud",
          entry6.get("source") == "soundcloud",
          f"source={entry6.get('source')}")
    check("co thu YouTube truoc khi doi nguon",
          MultiSourceYDL.order[0] == "youtube"
          and "soundcloud" in MultiSourceYDL.order[1:],
          str(MultiSourceYDL.order[:3]))
    check("nguon YouTube bi chan -> co cooldown",
          server._source_cooldown_active("youtube"))
    check("nguon thang -> khong bi cooldown",
          not server._source_cooldown_active("soundcloud"))

    # Request sau: nguon chết bị bỏ qua ngay (không mất thêm 10-20s).
    before = len(MultiSourceYDL.order)
    server._resolve("verifykey7", "Tam Thu Tu JACK J97")
    check("request sau bo qua nguon dang cooldown",
          MultiSourceYDL.order[before:] == ["soundcloud"],
          str(MultiSourceYDL.order[before:]))
finally:
    yt_dlp.YoutubeDL = real_ydl
    server._probe_direct_url = real_probe0
    server.SOURCES = real_sources
    server._source_fail_until.clear()
    with server._lock:
        server._resolved.pop("verifykey6", None)
        server._resolved.pop("verifykey7", None)


# ---------------------------------------------------------------------------
# 3b. Khong lap client da thu + URL 403 thi bo tier (nguyen nhan robot im)
# ---------------------------------------------------------------------------
class Tier3YDL(FakeYDL):
    """yt-dlp gia: chi tier cuoi ('tv') moi tra ve format."""

    def extract_info(self, title, download=False):
        clients = (self.opts.get("extractor_args") or {}).get(
            "youtube", {}).get("player_client")
        FakeYDL.calls.append(clients)
        if clients == ["tv"]:
            return {"title": "Fake Song", "url": "https://example.invalid/x",
                    "format_id": "251", "abr": 128, "__yt_dlp_client": "tv"}
        raise yt_dlp.utils.DownloadError(BOT_CHECK)


real_tiers = server._CLIENT_TIERS
real_probe = server._probe_direct_url
server._CLIENT_TIERS = server._parse_client_ladder("|visionos|visionos,tv")
yt_dlp.YoutubeDL = Tier3YDL
server._probe_direct_url = lambda entry: True
FakeYDL.calls = []
try:
    server._resolve("verifykey4", "fake song 4")
    check("tier sau khong lap client da thu (visionos chi thu 1 lan)",
          FakeYDL.calls == [None, ["visionos"], ["tv"]], f"calls={FakeYDL.calls}")
finally:
    server._CLIENT_TIERS = real_tiers
    with server._lock:
        server._resolved.pop("verifykey4", None)


class OkYDL(FakeYDL):
    """yt-dlp gia: moi tier deu tra format, de test buoc probe."""

    def extract_info(self, title, download=False):
        FakeYDL.calls.append((self.opts.get("extractor_args") or {}).get(
            "youtube", {}).get("player_client"))
        return {"title": "Fake Song", "url": "https://example.invalid/x",
                "format_id": "251", "abr": 128, "__yt_dlp_client": "tv"}


yt_dlp.YoutubeDL = OkYDL
server._probe_direct_url = lambda entry: False
try:
    try:
        server._resolve("verifykey5", "fake song 5")
        check("tier cho URL 403 -> bi bo, khong tra URL chet", False,
              "khong raise")
    except RuntimeError as e:
        check("tier cho URL 403 -> bi bo, khong tra URL chet",
              "tier_da_thu" in str(e), str(e)[:70])
finally:
    server._probe_direct_url = real_probe
    yt_dlp.YoutubeDL = real_ydl
    with server._lock:
        server._resolved.pop("verifykey5", None)


# ---------------------------------------------------------------------------
# 4. Route khong resolve dong bo
# ---------------------------------------------------------------------------
key = "verifykey3"
title = "fake song 3"
with server._lock:
    server._titles[key] = title

resolved_calls = []
real_resolve = server._resolve
real_preload_start = server._preload_start
server._resolve = lambda k, t: resolved_calls.append(k) or {"direct_url": "x"}
server._preload_start = lambda k, t: None
try:
    resp = server.stream(key)
    check("stream() tra 200 ngay", resp.status_code == 200, str(resp.status_code))
    check("stream() khong goi _resolve (het extract 2 lan)",
          resolved_calls == [], f"calls={resolved_calls}")
    check("stream() tra streaming body audio/mpeg",
          resp.headers.get("content-type") == "audio/mpeg")
finally:
    server._resolve = real_resolve
    server._preload_start = real_preload_start


# ---------------------------------------------------------------------------
# 5. _stream_body voi entry=None (duong route thuc te)
# ---------------------------------------------------------------------------
if not server.FFMPEG or not os.path.exists(server.FFMPEG):
    print("[SKIP] khong co ffmpeg -> bo qua check silence primer")
else:
    real_state = server._preload_state
    server._preload_state = lambda k: ""
    try:
        gen = server._stream_body(key, title, None)
        first = next(gen)
        check("primer gui ngay voi entry=None (khong can resolve)",
              first[:3] == b"ID3" or first[:2] == b"\xff\xfb",
              f"magic={first[:3].hex()}")
    finally:
        server._preload_state = real_state

    def boom(k, t):
        raise RuntimeError("resolve gia lap loi")

    server._preload_state = lambda k: "failed"
    server._resolve = boom
    try:
        gen = server._stream_body(key, title, None)
        closed = False
        for _ in range(3):
            try:
                next(gen)
            except StopIteration:
                closed = True
                break
        check("preload failed + resolve loi -> dong response", closed)
    finally:
        server._resolve = real_resolve
        server._preload_state = real_state

print()
if FAILED:
    print(f"THAT BAI {len(FAILED)}: {', '.join(FAILED)}")
    sys.exit(1)
print("Tat ca check PASS")
