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


# ---------------------------------------------------------------------------
# 1. Parse ladder
# ---------------------------------------------------------------------------
check("parse ladder 'a,b|c|'",
      server._parse_client_ladder("a,b|c|") == (("a", "b"), ("c",), ()),
      repr(server._parse_client_ladder("a,b|c|")))
check("ladder rong -> 1 tier rong (client mac dinh)",
      server._parse_client_ladder("") == ((),))
check("ladder mac dinh co nhom token-free o tier 2",
      server._DEFAULT_CLIENT_LADDER.split("|")[1] == "visionos,tv,web_embedded",
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
        if clients == ["visionos", "tv", "web_embedded"]:
            return {
                "title": "Fake Song",
                "url": "https://example.invalid/videoplayback",
                "format_id": "251",
                "abr": 128,
                "__yt_dlp_client": "web_embedded",
            }
        raise yt_dlp.utils.DownloadError(BOT_CHECK)


real_ydl = yt_dlp.YoutubeDL
yt_dlp.YoutubeDL = FakeYDL
try:
    entry = server._resolve("verifykey1", "fake song")
    check("tier 2 duoc dung sau khi tier 1 bi chan",
          FakeYDL.calls == [None, ["visionos", "tv", "web_embedded"]],
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
    with server._lock:
        server._resolved.pop("verifykey1", None)
        server._resolved.pop("verifykey2", None)


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
