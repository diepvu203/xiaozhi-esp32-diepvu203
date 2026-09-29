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
check("_parse_sources rong -> fallback nguon mac dinh dau",
      server._parse_sources("") == (server._DEFAULT_SOURCES.split(",")[0],),
      str(server._parse_sources("")))
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
real_sources3b = server.SOURCES
server._CLIENT_TIERS = server._parse_client_ladder("|visionos|visionos,tv")
# Test rieng phan YouTube -> chi bat nguon nay, khong de mac dinh
# (soundcloud,youtube) chen them calls cua nguon khac vao danh sach.
server.SOURCES = ("youtube",)
yt_dlp.YoutubeDL = Tier3YDL
server._probe_direct_url = lambda entry: True
FakeYDL.calls = []
try:
    server._resolve("verifykey4", "fake song 4")
    check("tier sau khong lap client da thu (visionos chi thu 1 lan)",
          FakeYDL.calls == [None, ["visionos"], ["tv"]], f"calls={FakeYDL.calls}")
finally:
    server._CLIENT_TIERS = real_tiers
    server.SOURCES = real_sources3b
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
# 6. _search_all: xep hang theo do khop ten + early-exit + gioi han moi nguon
# ---------------------------------------------------------------------------
# Bug thuc te: query "Tam Thai Tu JACK J97" -> bai DUNG (khop 100%) nam o vi
# tri #5, 4 ket qua dau la bai khac cung album. Lay top-N theo thu tu search
# -> agent nghe nham bai.
SC_RESULTS = [
    {"title": "JACK - J97 | HOA TRONG ĐÁI LỐI (OFFICIAL MV)", "uploader": "J97",
     "duration_sec": 200, "source": "soundcloud",
     "youtube_url": "https://soundcloud.com/x/wrong1"},
    {"title": "JACK - J97 | NGƯỜI DƯNG (Album TAM THÁI TỬ)", "uploader": "J97",
     "duration_sec": 200, "source": "soundcloud",
     "youtube_url": "https://soundcloud.com/x/wrong2"},
    {"title": "JACK - J97 | TAM THÁI TỬ", "uploader": "J97",
     "duration_sec": 230, "source": "soundcloud",
     "youtube_url": "https://soundcloud.com/x/exact"},
    {"title": "JACK - J97 | TAM THÁI TỬ (COVER)", "uploader": "zzz",
     "duration_sec": 230, "source": "soundcloud",
     "youtube_url": "https://soundcloud.com/x/wrong3"},
]
real_search_source = server._search_source
server._search_source = lambda src, q, n: list(SC_RESULTS)[:n]
real_sources6 = server.SOURCES
try:
    server.SOURCES = ("soundcloud", "youtube")
    res = server._search_all("Tam Thái Tử JACK J97", 5)
    check("_search_all: bai khop 100% duoc day len dau",
          res and res[0]["title"] == "JACK - J97 | TAM THÁI TỬ",
          res[0]["title"] if res else "rong")
    check("_search_all: chi giu _MAX_PER_SOURCE ket qua moi nguon",
          len(res) == server._MAX_PER_SOURCE, f"n={len(res)}")
    check("_search_all: early-exit khi nguon dau co bai khop tuyet doi "
          "(khong goi nguon sau)",
          all(r["source"] == "soundcloud" for r in res),
          str({r["source"] for r in res}))

    # Khong co bai khop tuyet thi phai van hoi nguon sau (khong early-exit).
    seen_src = []

    def _fake_search(src, q, n):
        seen_src.append(src)
        if src == "soundcloud":
            return [{"title": "KHAC KHAU - bai hat y hoc", "uploader": "x",
                     "duration_sec": 100, "source": src,
                     "youtube_url": "https://soundcloud.com/x/nomatch"}]
        return [{"title": "Tam Thái Tử JACK J97", "uploader": "y",
                 "duration_sec": 230, "source": src,
                 "youtube_url": "https://youtube.com/watch?v=abc"}]

    server._search_source = _fake_search
    seen_src.clear()
    res2 = server._search_all("Tam Thái Tử JACK J97", 5)
    check("_search_all: khong co bai khop -> van hoi nguon sau",
          seen_src == ["soundcloud", "youtube"], f"{seen_src}")
    check("_search_all: ket qua cua nguon sau duoc giu",
          any(r["source"] == "youtube" for r in res2),
          str([r["source"] for r in res2]))
finally:
    server._search_source = real_search_source
    server.SOURCES = real_sources6


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

# ---------------------------------------------------------------------------
# 7. _prune_cache don file tam (.part/.src) cua bai that bai
# Bug that: preload fail giua chung bo lai 5-12 MB o file .part/.src, nhung
# _prune_cache chi quet .mp3 -> file tam khong BAO GIO duoc don, dia day dan
# (Render free chi ~1 GB). Thu cong thuc da chay lai ba lan khi server that
# bai nhieu bai lien tiep.
# ---------------------------------------------------------------------------
_dir = server.MEDIA_DIR
_p = server._part_path("tmpprunekey")
_s = server._src_path("tmpprunekey")
with open(_p, "wb") as f:
    f.write(b"x" * 1000)
with open(_s, "wb") as f:
    f.write(b"y" * 2000)
check("setup: file tam .part/.src ton tai truoc prune",
      os.path.exists(_p) and os.path.exists(_s))
server._prune_cache()
check("prune xoa file tam .part", not os.path.exists(_p))
check("prune xoa file tam .src", not os.path.exists(_s))

# Nhung file tam cua bai DANG preload phai giu nguyen (con dung cho ghi).
_p2 = server._part_path("runningprunekey")
with open(_p2, "wb") as f:
    f.write(b"z" * 1000)
with server._lock:
    server._preload["runningprunekey"] = {"state": "running", "t0": 0, "err": ""}
try:
    server._prune_cache()
    check("prune GIU file tam cua bai dang preload", os.path.exists(_p2))
finally:
    with server._lock:
        server._preload.pop("runningprunekey", None)
    try:
        os.remove(_p2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 8. Single-flight phai chan ca "encoding", khong chi "running"
# Bug that do chinh thay doi streaming: robot goi get_song_url (spawn worker)
# roi GET /stream (goi _preload_start lan nua). Worker 1 da chuyen sang
# "encoding" (sau khi tai source) thi lan goi 2 KHONG bi chan -> spawn worker 2
# cho cung key. Hai worker cung chay ffmpeg vao MOT file .part -> robot nhan
# audio hong, va log in trung "chunked OK"/"primer->encode switch".
# ---------------------------------------------------------------------------
_real_worker = server._preload_worker
spawned = []


def _fake_worker(key, title):
    spawned.append(key)


server._preload_worker = _fake_worker
key_sf = "sfkey1"
try:
    for st in ("running", server._PRELOAD_ENCODING):
        spawned.clear()
        with server._lock:
            server._preload[key_sf] = {"state": st, "t0": 0, "err": ""}
        server._preload_start(key_sf, "t")
        check(f"single-flight: state='{st}' -> khong spawn worker trung",
              spawned == [], f"spawned={spawned}")

    # State "ready" + file ton tai -> cung khong spawn (bai da xong).
    spawned.clear()
    with open(server._final_path(key_sf), "wb") as f:
        f.write(b"x" * 200000)
    with server._lock:
        server._preload[key_sf] = {"state": "ready", "t0": 0, "err": ""}
    server._preload_start(key_sf, "t")
    check("single-flight: state='ready' + file co -> khong spawn lai",
          spawned == [], f"spawned={spawned}")

    # State "failed" -> cho phep thu lai.
    spawned.clear()
    with server._lock:
        server._preload[key_sf] = {"state": "failed", "t0": 0, "err": "x"}
    server._preload_start(key_sf, "t")
    check("single-flight: state='failed' -> spawn lai de thu",
          spawned == [key_sf], f"spawned={spawned}")
finally:
    server._preload_worker = _real_worker
    with server._lock:
        server._preload.pop(key_sf, None)
    try:
        os.remove(server._final_path(key_sf))
    except OSError:
        pass

# _prune_busy phai gom ca "encoding" (nếu khong se xoa .part dang robot nghe)
with server._lock:
    server._preload["busykey"] = {"state": server._PRELOAD_ENCODING,
                                 "t0": 0, "err": ""}
try:
    check("_preload_busy: 'encoding' duoc coi la dang chay",
          server._preload_busy("busykey"))
finally:
    with server._lock:
        server._preload.pop("busykey", None)
check("_preload_busy: 'ready' khong con chay",
      not server._preload_busy("nosuchkey"))


# ---------------------------------------------------------------------------
# 9. Prefetch: khoá "mot viec nen" phai mo lai sau khi worker xong
# Bug that: _preload_state_update ghi prefetch=True, neu khong xoa khi ket
# thuc (ready/failed) thi khoá kẹt vinh vien -> chi bai dau tien duoc tai
# truoc, tu bai thu hai tro di lai phai doi 26s transcode.
# ---------------------------------------------------------------------------
_spawned9 = []
server._preload_worker = lambda k, t: _spawned9.append(k)
try:
    # _maybe_prefetch TỰ hash title thành key (_key_of) — test phải dùng key
    # đó, không tự đặt key tùy ý (lần trước test sai chỗ này).
    _pf = server._key_of("pfkey1")
    with server._lock:
        server._preload.pop(_pf, None)
    server._maybe_prefetch([{"title": "pfkey1"}], "pfkey1")
    rec = (server._preload.get(_pf) or {})
    check("prefetch: bai khop tuyet doi -> spawn worker nen",
          _spawned9 == [_pf] and rec.get("prefetch") is True,
          f"spawned={_spawned9} rec={rec.get('prefetch')}")
    check("prefetch: state ban dau = running",
          rec.get("state") == "running", str(rec.get("state")))

    # Worker xong -> khoa phai mo, bai sau moi prefetch duoc.
    server._preload_state_update(_pf, state="ready", err="")
    check("prefetch: 'ready' xoa co khoa",
          not (server._preload.get(_pf) or {}).get("prefetch"),
          str((server._preload.get(_pf) or {}).get("prefetch")))

    # Worker that bai cung phai mo khoa (khong de bi khop roi vinh vien).
    with server._lock:
        server._preload.pop(_pf, None)
    _spawned9.clear()
    server._maybe_prefetch([{"title": "pfkey1"}], "pfkey1")
    server._preload_state_update(_pf, state="failed", err="x")
    check("prefetch: 'failed' cung xoa co khoa",
          not (server._preload.get(_pf) or {}).get("prefetch"))

    # Bai dang chay -> khong prefetch trung (tranh 2 worker cung ghi .part).
    _spawned9.clear()
    with server._lock:
        server._preload.pop(_pf, None)
        server._preload[_pf] = {"state": "running", "t0": 0, "err": "",
                                "prefetch": True}
    server._maybe_prefetch([{"title": "pfkey1"}], "pfkey1")
    check("prefetch: bai dang chay -> khong spawn worker thu hai",
          _spawned9 == [], f"spawned={_spawned9}")

    # Ten khong khop -> khong prefetch (tranh tai nham bai 5 MB).
    _spawned9.clear()
    with server._lock:
        server._preload.pop(_pf, None)
    server._maybe_prefetch([{"title": "mot bai hoan toan khac"}], "pfkey1")
    check("prefetch: ten khong khop -> khong tai truong",
          _spawned9 == [], f"spawned={_spawned9}")
finally:
    server._preload_worker = _real_worker
    with server._lock:
        server._preload.pop(server._key_of("pfkey1"), None)


print()
if FAILED:
    print(f"THAT BAI {len(FAILED)}: {', '.join(FAILED)}")
    sys.exit(1)
print("Tat ca check PASS")
