# Music Streaming (SoundCloud/YouTube) — Firmware + MCP Server

> **Kiến trúc**: Firmware (ESP32) ↔ MCP Server (Python/FastAPI, `tools/zing-music-mcp/`) ↔ SoundCloud/YouTube (yt-dlp)

> **Tài liệu vận hành đầy đủ** (env vars, deploy, troubleshooting server): xem
> [`tools/zing-music-mcp/README.md`](../tools/zing-music-mcp/README.md) — file này **không lặp lại**.

## 1. Vì sao phải tự dựng

- xiaozhi.me có tool `search_music` nhưng là **kho nhạc Trung**, server stream Opus
  qua kênh audio thường, firmware chỉ decode — **không sửa được nguồn**.
- Muốn nhạc Việt → tự dựng chuỗi: MCP server ngoài (PC/Render) tìm + transcode →
  trả URL MP3 → firmware stream URL đó ra loa.

## 2. Luồng hoạt động

```
User: "phát nhạc <tên bài>"
    ↓
LLM gọi search_song(keyword)      → server trả tối đa 5 kết quả (xếp theo độ khớp tên)
    ↓
LLM chọn bài → get_song_url(title) → server trả NGAY {"status":"ready","stream_url":"<PUBLIC_BASE>/stream/<id>.mp3"}
    ↓
LLM gọi self.music.play(stream_url) → MusicPlayer::Play()
    ↓
GET /stream/<id>.mp3  (firmware)
    ↓ server: resolve direct URL (yt-dlp, cache RAM TTL 30') → transcode → MP3 24kHz mono → pipe:1
    ↓
MusicPlayer: HTTP chunk → decode esp_audio_dec → push AudioService queue → loa
```

## 3. MCP Tools

| Tool | Nguồn | Mô tả |
|------|-------|-------|
| `search_song(keyword)` | server | Tối đa 5 kết quả: `title`, `uploader`, `duration_sec`, `source`, `youtube_url` |
| `get_song_url(title)` | server | Trả NGAY `status: ready` + `stream_url` — không cần poll |
| `self.music.play(url, name)` | firmware | Phát nhạc từ stream URL (throw nếu đang phát) |
| `self.music.stop` | firmware | Dừng nhạc đang phát |
| `self.music.get_state` | firmware | Trả `{"playing": true/false}` |

- Nếu web xiaozhi.me chỉ hiện 2 tool → **reset MCP endpoint + flash firmware mới nhất**
- Radio dùng chung `self.music.play/stop` — xem [`radio-streaming.md`](radio-streaming.md)

## 4. Firmware

### `main/audio/music_player.cc` / `.h`
- `MusicPlayer::GetInstance()`, `Play(url)`, `Stop()`, `IsPlaying()`
- FreeRTOS task riêng (`StreamTask`), **không tham gia state machine**
- HTTP(S) chunked → decode `esp_audio_dec` (KHÔNG dùng `esp_audio_simple_dec`) → push PCM
- Kết nối: `http_client` timeout 15s, **retry mở kết nối 3 lần cách 700ms**
- Pre-buffer: `kPreBufferBytes = kReadBufferBytes` = **64KB** (`kReadBufferBytes = 64*1024`, PSRAM)
- Reconnect giữa chừng: attempt 2/3 + **HTTP Range** (`stream_pos`) để không nghe lại từ đầu

### `main/audio/audio_service.cc` / `.h` — API mới cho music

| API | Mục đích |
|-----|----------|
| `GetOutputSampleRate()` | Trả sample rate codec (24000 default) cho MusicPlayer resample |
| `SetMusicPlaying(bool)` | Mute TTS khi nhạc phát + **tắt codec input** (mic) để wake-word không cắt ngang |
| `PushPcmToPlaybackQueue(pcm, max_queue_depth)` | Push PCM với jitter buffer depth (music=20, TTS=8) |
## 5. Đa nguồn: SoundCloud là nguồn chính

**Vì sao**: YouTube chạy vài chục bài rồi chết — cookie/PO token bị Google **revoke
session** khi IP không cố định (Render free xoay IP), hoặc IP datacenter bị gắn cờ →
`Sign in to confirm you're not a bot`.

| | YouTube | SoundCloud |
|---|---|---|
| Auth | cookie + PO token (dễ hỏng) | không cần gì |
| Bot-check theo IP | có | không |
| Sống được | vài chục bài | lâu dài |
| Bài Việt | đầy đủ | nhiều (J97, Sơn Tùng…) |

- Mặc định `YTDLP_SOURCES=soundcloud,youtube` (đổi được)
- `YTDLP_SOURCE_COOLDOWN` (900s): nguồn vừa gặp bot-check thì bỏ qua 15 phút
- `YTDLP_RESOLVE_CANDIDATES` (2): thử thêm N video cùng tên khi ứng viên đầu hỏng
- `_SC_FORMAT = "bestaudio[protocol^=http]/bestaudio/best"` — **ưu tiên progressive
  `http_mp3` hơn HLS `hls_aac_160k`** (m3u8 phải `_download_ffmpeg`, chậm + hay
  reconnect; progressive đi `_fetch_chunked` + `_transcode_local`, resume được).
  Đo được: **>60s → 13s**
- `_rank_candidates`: lấy đủ **10** ứng viên (`_FETCH_PER_SOURCE`) **rồi mới** xếp hạng
  theo độ khớp tên (`_MAX_PER_SOURCE=3` chỉ áp cho kết quả trả về) — trước đây lấy 3
  rồi xếp hạng một tập đã sai thứ tự nên bài đúng bị chôn ở #5

### ZingMP3 — thử đủ, KHÔNG làm được (kết luận)
1. Search API → `-403 You don't have permission` (đúng thuật toán HMAC-SHA512 vẫn bị chặn)
2. yt-dlp `ZingMp3IE` **không có `_search`** — chỉ nhận URL `zingmp3.vn/bai-hat/...`
3. CDN `zmdjs.zmdcdn.me` trả 404 nếu path không có đúng `128kbps` + param ký → cần signed URL từ API đang bị chặn

## 6. HTTP Range / resume — fix "nghe lại từ đầu"

**Triệu chứng**: robot nghe đúng một đoạn rồi nhảy về đầu, lặp 3/3 lần rồi hết.

**Nguyên nhân**: Render đóng TCP giữa chừng (proxy free đóng idle ~60s). Firmware GET lại
**cùng URL không kèm `Range`** → server phục vụ từ byte 0 → decoder dựng lại frame đầu.

**Sửa**: `GET /stream/{id}.mp3` hỗ trợ `Range: bytes=N-`:
- Trả `206 Partial Content` + `Content-Range: bytes N-/(total-prime)`
- **Trừ phần primer đã gửi**: firmware tính cả byte primer, không trừ thì robot nhảy vào giữa bài
- Hết byte → `416` + `Content-Range: bytes */<total>`
- **Kiểm chứng**: mô phỏng đúng kịch bản robot (đọc 20000 B → reset → `Range: bytes=20000-`)
  → 50000 B ghép lại liên tục, **không một byte nào phát lại**

**Lưu ý radio**: `/radio/{key}.mp3` **luôn trả 200, bỏ qua Range** — radio không có byte
offset để resume; firmware chấp nhận 200 và reset decoder để nghe tiếp âm thanh hiện tại.

## 7. Dọn cache & rò đĩa

- **Rò đĩa trên Render**: preload fail giữa chừng để lại file `.part`/`.src` 5–12 MB, mà
  `_prune_cache` chỉ quét `.mp3` → file tạm **không bao giờ** được dọn (Render free ~1GB).
  Sửa: prune cả file tạm + gọi `_prune_cache()` ở **nhánh `failed`**.
- Cache candidate TTL 10 phút — `_rank_candidates` dùng chung cache của `search_song`
  (trước đây resolve search lại y hệt, tốn ~10s)
## 8. Chống xen tiếng TTS khi phát nhạc

`AudioService::SetMusicPlaying(true)` được gọi **ngay đầu task** (trước handshake):
- Mute TTS (không xen tiếng robot vào nhạc)
- **Tắt codec input (mic)** → wake word không cắt ngang nhạc
- ⚠️ Hệ quả: **không ra lệnh bằng giọng được khi đang phát**. Dừng bằng:
  1. AI gọi `self.music.stop`
  2. **Nhấn nút wakeup** trên robot → board gọi `MusicPlayer::Stop()`

## 9. Nút wakeup dừng được nhạc (29/09/2026)

**Nguyên nhân gốc**: `MusicPlayer` chạy task riêng và **không tham gia state machine**
(không `SetDeviceState`, không đụng mic) → `boot_button_.OnClick` → `app.ToggleChatState()`
chỉ thao tác state hội thoại, task nhạc cứ chạy đến hết bài.

**Sửa** (trong `main/boards/bread-compact-wifi/compact_wifi_board.cc`): nút wakeup gọi
thêm `MusicPlayer::GetInstance().Stop()` rồi mở mic nghe tiếp.

## 10. Sự cố MẠNG thường gặp (KHÔNG phải lỗi code)

| Triệu chứng log | Nguyên nhân | Cách xử lý |
|---|---|---|
| `esp-tls: select() timeout` / `Failed to open a new connection: 32774` / `Failed to open connection` | Laptop ở **5GHz**, robot ở **2.4GHz** — router chập chờn khi truyền TCP giữa 2 băng (ping được nhưng TCP handshake timeout) | Đưa **laptop về 2.4GHz** cùng SSID robot; hoặc tắt Band Steering / AP Isolation |
| `MQTT: Received audio packet with wrong sequence` | WiFi yếu (RSSI -83..-97 dBm) mất gói UDP | Đưa robot gần router |
| Server không truy cập được từ robot (dù laptop OK) | Windows Firewall chặn inbound | Tắt Private profile hoặc tạo rule TCP 8619 Inbound Allow |
| Chỉ thấy `LISTENING` rồi robot timeout | Band isolation / AP isolation | `netstat -an \| findstr 8619`, `Test-NetConnection -Port 8619`, `ping <IP robot>` |

Đo được: laptop 5GHz ↔ robot 2.4GHz → **ping 50% loss, 650ms** → không code nào chữa được.

## 11. Giai đoạn triển khai (lịch sử)

| Giai đoạn | Nội dung |
|---|---|
| 1 | Thử ZingMP3 API → bị `-403` → bỏ, chuyển yt-dlp/YouTube |
| 2 | MCP server `tools/zing-music-mcp/` + `mcp_pipe.py` bridge + `run.bat` |
| 3 | Firmware `MusicPlayer` (06/09/2026) + sửa decoder MP3 (06–07/09) |
| 4 | Ổn định stream: retry/reconnect, WiFi buffer, jitter buffer, `-re` (08–22/09) |
| 5 | Đa nguồn SoundCloud + Range/resume + nút wakeup + EQ (29/09) |
| 6 | Radio streaming (xem [`radio-streaming.md`](radio-streaming.md)) |

## 12. Kết quả đo (không phải ước lượng) — SoundCloud

| Bước | Kết quả |
|---|---|
| `search` "Tam Thái Tử JACK J97" | 3.9s → 10 kết quả, early-exit khi khớp 100% |
| resolve | 3s, `http_mp3_1_0`, probe 206 OK |
| preload từ cache trống | **7.5s** = resolve 3 + tải 3 + transcode 2 |
| `GET /stream/{id}.mp3` | HTTP 200, `Content-Length: 4212524`, `audio/mpeg` |
| ffmpeg decode | rc=0, 3:30.53, 24 kHz mono 160k |
| `verify_resolve_ladder.py` | **63/63 PASS, exit 0** |

## 13. Việc còn lại

- [ ] Test nút wakeup dừng nhạc trên máy thật
- [ ] Nghe lại bài DANHKA sau chuỗi EQ **v2** — xem [`audio-quality-tuning.md`](audio-quality-tuning.md)
- [ ] Nếu muốn phủ bài chỉ có trên YouTube mà không bị chặn: cần proxy sticky/residential sạch
- [ ] Nếu ổn định hơn: cân nhắc `AUDIO_BITRATE=160k`