# MCP Server Nhạc Việt cho Xe Robot XiaoZhi — Cloud Stream Proxy

Server MCP tìm nhạc trên **YouTube** (qua `yt-dlp`) và phục vụ **stream MP3
on-the-fly qua HTTP** (FastAPI `StreamingResponse` + ffmpeg pipe), gộp cùng
MCP server (`search_song`, `get_song_url`) trong **một process, một cổng**
(`/mcp` streamable-http + `/stream/<id>.mp3` + `/health`).
**100% RAM, không ghi file tạm ra đĩa.** Robot chỉ nhận 1 URL stream duy nhất
và tự stream qua WiFi của nó.

## Tool cung cấp

| Tool | Input | Output |
|------|-------|--------|
| `search_song` | `keyword` (tên bài / ca sĩ) | Tối đa 5 kết quả: `title`, `uploader`, `duration_sec`, `youtube_url` |
| `get_song_url` | `title` | Trả NGAY `{"status":"ready","stream_url":"<PUBLIC_BASE>/stream/<id>.mp3"}` — không cần poll/chờ |

Cơ chế: khi robot (hoặc VLC) mở `/stream/<id>.mp3`, server resolve direct URL
googlevideo bằng yt-dlp (cache RAM ~30 phút) rồi pipe `ffmpeg` (mặc định
CBR 160k, xem `AUDIO_BITRATE`) thẳng ra HTTP response.

## Cấu hình (biến môi trường)

| Biến | Ý nghĩa |
|---|---|
| `PORT` | Cổng HTTP. Mặc định 8080 (Render tự set) |
| `MCP_TRANSPORT` | `stdio` — chạy qua `mcp_pipe.py` kết nối `MCP_ENDPOINT` (run.bat và Render đều dùng). `streamable-http` — serve `POST /mcp` trực tiếp (mặc định trong server.py nếu không set) |
| `PUBLIC_BASE` | Base URL công khai, vd `https://xiaozhi-music.onrender.com`. Bỏ trống khi chạy laptop → tự dùng `http://<IP-LAN>:<PORT>` |
| `MCP_ENDPOINT` | URL "Điểm cuối MCP" từ xiaozhi.me (cho `mcp_pipe.py`) |
| `AUDIO_SAMPLE_RATE` | Rate gửi xuống robot. Mặc định `24000` — phải khớp `AUDIO_OUTPUT_SAMPLE_RATE` của board để ESP32 không resample thêm |
| `AUDIO_CHANNELS` | Số kênh. Mặc định `1` (loa robot là mono) |
| `AUDIO_PRESET` | Bộ lọc DSP bù trừ loa. Mặc định `speaker`. Xem bảng dưới |
| `AUDIO_BITRATE` | Bitrate MP3. Mặc định `160k` — mức cao nhất libmp3lame cho phép ở 24 kHz (MPEG-2 LSF) |
| `AUDIO_FILTERS` | Chuỗi `ffmpeg -af` tuỳ ý, **đè** preset khi được set. `""` = tắt DSP |

Server in ra dòng `[music] audio: 24000 Hz x1, 160k mp3, filters='...'` khi khởi động để bạn biết cấu hình đang chạy.

### Chất lượng âm thanh

Thứ tự ảnh hưởng thực tế: **âm sắc (EQ) > méo/clip > bitrate**. Trần cứng: 24 kHz → Nyquist 12 kHz, và loa nhỏ trên board không tái tạo được sub-bass.

**Preset (`AUDIO_PRESET`)** — đổi preset rồi restart là nghe khác ngay, nên cứ thử A/B:

| Preset | Đặc điểm | Đáp tuyến đo được (48 kHz → 24 kHz mono) |
|---|---|---|
| `speaker` (mặc định) | Bù trừ loa nhỏ: cắt sub-bass, ấm hơn, bớt "hộp", rõ tiếng | 50 Hz −12 · 90 Hz −2.5 · 200 Hz **+3.5** · 800 Hz −2.5 · 3.2 kHz **+2.5** · 10 kHz +1.5 |
| `flat` | Gần như nguyên bản, chỉ cắt sub-bass + chống clip | 90 Hz −3 · còn lại ~0 |
| `warm` | Bass nhiều hơn (nhấn 200 Hz +6 dB) | 200 Hz **+5.8** |
| `bright` | Treble nhiều hơn (3.5 kHz +4, 10 kHz +3) | 3.5 kHz **+3.5** · 10 kHz **+1.7** |
| `loud` | To/nhỏ đều giữa các bài (`loudnorm`) + EQ `speaker` | như `speaker`, mức to được chuẩn hoá |
| `none` | Không DSP. Thô nhất và **dễ rè nhất** (bass sâu làm màng loa rung) | 0 dB toàn dải |

Preset nào cũng kết thúc bằng `alimiter=limit=0.841` (−1.5 dBFS) để PCM sau khi giải mã không bị clip khi nhân software volume trong firmware.

**Bitrate (`AUDIO_BITRATE`)** — 128k → 160k. Đo lại trên tín hiệu nhạc tổng hợp 20 s @24 kHz (SNR so với PCM gốc):

| Cấu hình | Bitrate thực | SNR |
|---|---|---|
| CBR 128k | 129k | 18.5 dB |
| **CBR 160k** | **161k** | **18.6 dB** |
| VBR `-q:a 0` (V0) | 73k | 15.6 dB |
| VBR `-q:a 2` (V2) | 55k | 13.3 dB |
| CBR 160k + `-cutoff 11000` | 161k | 16.9 dB (tệ hơn) |

→ Giữ **CBR 160k**, không dùng VBR (VBR ở container này tụt xuống 55–73 kbps), không set `-cutoff`. 160k là **trần** của libmp3lame ở 24 kHz: xin `192k` cũng chỉ ra 160k. Băng thông 20 KB/s (robot pre-buffer 4 s ≈ 80 KB).

**Board 16 kHz** (ESP32-S3 SuperMini...) → `AUDIO_SAMPLE_RATE=16000` + `AUDIO_BITRATE=64k`.

### Ví dụ chỉnh

| Muốn gì | Cách làm |
|---|---|
| Sáng/thoáng hơn | `AUDIO_PRESET=bright` |
| Bass nhiều hơn | `AUDIO_PRESET=warm` |
| Nghe thử bản "nguyên bản" | `AUDIO_PRESET=flat` |
| Tự chỉnh EQ | `AUDIO_FILTERS=highpass=f=90,equalizer=f=250:t=q:w=1:g=5,alimiter=limit=0.841:level=disabled` |
| Tắt hết DSP | `AUDIO_PRESET=none` hoặc `AUDIO_FILTERS=` |

## Cài đặt (laptop dev, 1 lần)

```powershell
pip install -r requirements.txt
```

## Chạy trên laptop (dev)

Double-click `run.bat` (đã dán MCP_ENDPOINT vào trong; `run.bat` tự set
`MCP_TRANSPORT=stdio`) hoặc:

```powershell
cd d:\xiaozhi\xiaozhi-esp32-diepvu203\tools\zing-music-mcp
$env:MCP_ENDPOINT = "<URL Điểm cuối MCP>"
$env:MCP_TRANSPORT = "stdio"   # MCP stdio qua mcp_pipe; HTTP stream chạy nền
python mcp_pipe.py server.py
```

- Web xiaozhi.me hiện **Đã kết nối** + 2 tool là OK.
- Windows Firewall lần đầu hỏi **Allow** cho Python port 8080 (Private).

## Deploy lên Render.com (server 24/7, robot chạy WiFi nào cũng hát được)

Server gộp MCP + stream vào **một process trên Render**: `mcp_pipe.py` kết
nối OUT tới `MCP_ENDPOINT` (wss api.xiaozhi.me) để đăng ký 2 tool nhạc 24/7;
`server.py` chạy stdio, thread nền phục vụ `/health` + `/stream/<id>.mp3`.

1. Push cả repo lên GitHub. Có 2 cách:
   - **Blueprint (khuyến nghị):** Render → New → Blueprint → chọn repo
     (dùng `render.yaml` ở **gốc repo**, trỏ `./Dockerfile` gốc).
   - **Web Service:** New Web Service → Docker → dùng `Dockerfile` ở gốc repo
     (COPY `tools/zing-music-mcp/*`).
2. Environment variables (dashboard Render):
   - `MCP_ENDPOINT` = link Điểm cuối MCP `wss://api.xiaozhi.me/mcp/?token=...`
     lấy từ xiaozhi.me — **BẮT BUỘC** (token xoay vòng thì cập nhật lại rồi restart).
   - `PUBLIC_BASE` = `https://<tên-service>.onrender.com` — **BẮT BUỘC** (không
     set thì stream URL trả IP nội bộ container, robot không tải được nhạc).
   - `MCP_TRANSPORT` = `stdio` (`render.yaml` đã set).
3. Deploy: log phải thấy `Successfully connected to WebSocket server`, vào
   xiaozhi.me thấy 2 tool quay lại, `GET /health` trả `{"ok":true}`.
4. Lưu ý:
   - Gói **free ngủ sau ~15 phút không traffic** → khi ngủ tool biến mất;
     chặn bằng cron ping `/health` mỗi 10 phút (cron-job.org) hoặc upgrade
     Starter (~$7/tháng).
   - YouTube chặn IP datacenter → xem mục **"YouTube chặn 'Sign in to confirm
     you're not a bot'"** bên dưới.

## YouTube chặn "Sign in to confirm you're not a bot"

IP datacenter của Render bị YouTube gắn cờ → bước `_resolve` (lấy direct URL)
thất bại, log thấy đúng câu lỗi này. `search_song` vẫn chạy (extract_flat
không bị check) nên robot "tìm được bài nhưng không phát được".

Sửa bằng cookies (cách chính thức yt-dlp khuyến nghị):

1. **Export cookies** từ máy đang đăng nhập YouTube (chọn 1 cách):
   - Extension **"Get cookies.txt LOCALLY"** (Chrome/Edge/Firefox) → mở
     youtube.com → Export → được `cookies.txt` (định dạng Netscape).
   - Hoặc CLI (trên máy đã đăng nhập):
     ```powershell
     yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download https://www.youtube.com
     ```
2. **Encode base64** (PowerShell, thực hiện trong thư mục chứa cookies.txt):
   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\cookies.txt"))
   ```
3. **Self-check base64 trước khi paste vào Render** — byte đầu PHẢI là `35`
   (`0x23` = `'#'`). Nếu là `239` (`0xEF`) là **UTF-8 BOM** — chính BOM gây
   lỗi `does not look like a Netscape format cookies file` (Python đọc file
   ở text mode, BOM thành ký tự ẩn `U+FEFF` bám trước `# Netscape...` →
   regex magic của `http.cookiejar` không match → `LoadError`):
   ```powershell
   $b64 = '<dán base64 vào đây>'
   $d = [Convert]::FromBase64String($b64)
   $d[0]                              # 35 = OK; 239 = có BOM
   [Text.Encoding]::UTF8.GetString($d, 0, 30)   # ẨN BOM -> nhìn "đúng" nhưng chưa đủ!
   ```
   Nếu file có BOM thì strip trước khi encode lại:
   ```powershell
   $d = [IO.File]::ReadAllBytes("$PWD\cookies.txt")
   if ($d[0] -eq 0xEF -and $d[1] -eq 0xBB -and $d[2] -eq 0xBF) { $d = $d[3..($d.Length-1)] }
   [Convert]::ToBase64String($d)
   ```
   (Server cũng tự strip UTF-8 BOM lúc boot — self-check để chắc file không
   hỏng theo cách khác.)
4. Render dashboard → **Environment** → thêm biến `YTDLP_COOKIES_B64` =
   dán chuỗi base64 (biến secret, `sync:false` trong render.yaml — **KHÔNG
   commit nội dung cookies vào git**) → Save → service tự redeploy.
5. Kiểm tra: `GET /health` phải thấy `"cookies": true` — nghĩa là server
   đã decode **VÀ** validate qua Netscape header (file hỏng → server từ chối
   và trả `"cookies": false`, lý do chi tiết nằm trong log Render: dòng
   `YTDLP_COOKIES_B64 rejected: ...`). Rồi thử yêu cầu hát.
6. Cookies hết hạn / YouTube xoay session → export lại + cập nhật env.

Phương án phụ: có proxy IP sạch thì set `YTDLP_PROXY=http://user:pass@host:port`
trên dashboard (không cần cookies).

Trên laptop (IP nhà) **không cần cookies** — resolve trực tiếp vẫn chạy.

**Lỗi "Requested format is not available":** Dockerfile đã cài **deno** (JS
runtime cho yt-dlp) — không có JS runtime thì YouTube **drop formats** ở một
số video và yt-dlp báo đúng lỗi này; `server.py` cũng có retry ladder
(`bestaudio` → `best` → default). Nếu vẫn gặp với 1 video cụ thể thì video đó
có thể bị giới hạn tuổi / không khả dụng — bảo robot thử bài khác.

## Cấu hình prompt trên xiaozhi.me (Vai trò)

```
Khi tôi xin phát nhạc hoặc tìm nhạc: nếu chưa rõ bài nào thì gọi
search_song để liệt kê và hỏi tôi chọn; khi đã chốt bài thì gọi
get_song_url (trả về ngay) rồi gọi self.music.play với stream_url.
Nếu lỗi thì báo tôi. Không tự phát nhạc khác khi tôi không yêu cầu.
```

## Kiến trúc & giới hạn

```
MODE 1 — Cloud (Render, MCP_TRANSPORT=stdio, 24/7, không cần laptop):

  MCP: xiaozhi.me ◀──wss── mcp_pipe.py (trên Render) ──stdio── server.py
  Robot ──GET /stream──▶ https://<tên-service>.onrender.com/stream/<id>.mp3
                         (thread nền: /health + /stream trên PORT Render)

MODE 2 — Local dev (run.bat, laptop mở):

  MCP: xiaozhi.me ◀──wss── mcp_pipe.py (laptop) ──stdio── server.py
  Robot ──GET /stream──▶ http://<IP-laptop>:8080/stream/<id>.mp3
```

- **Đã test**: `/health` OK; `/stream/b866437922.mp3` trả MP3 hợp lệ
  (5:00.53, 160kbps, 48kHz stereo) — convert on-the-fly, 0 file đĩa.
- Direct URL googlevideo bị khóa theo IP + hết hạn → luôn stream qua server
  (IP của server quyết định); không trả direct URL cho robot.

## Firmware (Giai đoạn 3 — ĐÃ LÀM, đã chạy được)

- `main/audio/music_player.*` trên ESP32: stream URL HTTP(S) → decode MP3
  bằng **`esp_audio_dec`** (KHÔNG phải `esp_audio_simple_dec` — simple decoder
  chỉ hỗ trợ WAV/M4A/TS/OGG, không có MP3) → resample về 24 kHz mono → loa qua
  `AudioService::PushPcmToPlaybackQueue`.
- MCP tool `self.music.play(url, name)` / `self.music.stop` /
  `self.music.get_state` đã đăng ký trong `compact_wifi_board.cc`.
- Khi nhạc phát, TTS downlink bị mute (`AudioService::SetMusicPlaying`) để
  không xen tiếng robot nói.
- Cần **build + nạp firmware** mới hát được; server nhạc phải đang chạy.
- Trên web xiaozhi.me sẽ thấy **5 tool**: `search_song`, `get_song_url`
  (từ server) + `self.music.play/stop/get_state` (từ firmware).
- ⚠️ Nếu web chỉ hiện 2 tool: reset Điểm cuối MCP + flash firmware mới nhất
  (firmware phải là bản có 3 tool `self.music.*`).
- ⚠️ Laptop nên để WiFi **2.4GHz cùng băng với robot** (5GHz↔2.4GHz làm TCP
  handshake chập chờn → `Failed to open connection`).
