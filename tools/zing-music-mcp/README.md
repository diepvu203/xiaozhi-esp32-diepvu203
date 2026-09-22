# MCP Server Nhạc Việt cho Xe Robot XiaoZhi — Cloud Stream Proxy

Server MCP tìm nhạc trên **YouTube** (qua `yt-dlp`) và phục vụ **stream MP3
128kbps on-the-fly qua HTTP** (FastAPI `StreamingResponse` + ffmpeg pipe).
**100% RAM, không ghi file tạm ra đĩa.** Robot chỉ nhận 1 URL stream duy nhất
và tự stream qua WiFi của nó.

## Tool cung cấp

| Tool | Input | Output |
|------|-------|--------|
| `search_song` | `keyword` (tên bài / ca sĩ) | Tối đa 5 kết quả: `title`, `uploader`, `duration_sec`, `youtube_url` |
| `get_song_url` | `title` | Trả NGAY `{"status":"ready","stream_url":"<PUBLIC_BASE>/stream/<id>.mp3"}` — không cần poll/chờ |

Cơ chế: khi robot (hoặc VLC) mở `/stream/<id>.mp3`, server resolve direct URL
googlevideo bằng yt-dlp (cache RAM ~30 phút) rồi pipe `ffmpeg -b:a 128k` thẳng
ra HTTP response.

## Cấu hình (biến môi trường)

| Biến | Ý nghĩa |
|---|---|
| `PORT` | Cổng HTTP. Mặc định 8619 (Render tự set) |
| `PUBLIC_BASE` | Base URL công khai, vd `https://xiaozhi-music.onrender.com`. Bỏ trống khi chạy laptop → tự dùng `http://<IP-LAN>:<PORT>` |
| `MCP_ENDPOINT` | URL "Điểm cuối MCP" từ xiaozhi.me (cho `mcp_pipe.py`) |

## Cài đặt (laptop dev, 1 lần)

```powershell
pip install -r requirements.txt
```

## Chạy trên laptop (dev)

Double-click `run.bat` (đã dán MCP_ENDPOINT vào trong) hoặc:

```powershell
cd d:\xiaozhi\xiaozhi-esp32-diepvu203\tools\zing-music-mcp
$env:MCP_ENDPOINT = "<URL Điểm cuối MCP>"
python mcp_pipe.py server.py
```

- Web xiaozhi.me hiện **Đã kết nối** + 2 tool là OK.
- Windows Firewall lần đầu hỏi **Allow** cho Python port 8619 (Private).

## Deploy lên Render.com (server 24/7, robot chạy WiFi nào cũng hát được)

1. Push thư mục này lên 1 repo GitHub (dùng `Dockerfile` + `render.yaml` sẵn có).
2. Render → New → Blueprint → chọn repo. Hoặc New Web Service → Docker.
3. Environment variables:
   - `MCP_ENDPOINT` = URL Điểm cuối MCP từ xiaozhi.me
   - `PUBLIC_BASE` = `https://<tên-service>.onrender.com`
4. Deploy. Lưu ý gói **free sẽ ngủ sau ~15 phút không traffic** (MCP tự nối
   lại nhờ reconnect của `mcp_pipe`, lần mở stream đầu mất thêm ~30-50 giây);
   chạy ổn định thì dùng gói Starter (~$7/tháng). HuggingFace Spaces chạy
   được cùng Dockerfile (port 8619 trong Dockerfile, HF dùng 7860 — đổi
   `EXPOSE`/`PORT` khi cần).
5. Lưu ý: YouTube có thể gắt hơn với IP datacenter — nếu resolve bị chặn
   thì nạp cookies Google vào yt-dlp (`cookiesfrombrowser`).

## Cấu hình prompt trên xiaozhi.me (Vai trò)

```
Khi tôi xin phát nhạc hoặc tìm nhạc: nếu chưa rõ bài nào thì gọi
search_song để liệt kê và hỏi tôi chọn; khi đã chốt bài thì gọi
get_song_url (trả về ngay) rồi gọi self.music.play với stream_url.
Nếu lỗi thì báo tôi. Không tự phát nhạc khác khi tôi không yêu cầu.
```

## Kiến trúc & giới hạn

```
Robot (mọi WiFi có Internet) ──stream──▶ /stream/<id>.mp3 (FastAPI + ffmpeg)
        ▲                                        ▲
        │ self.music.play(url)          resolve yt-dlp (cache RAM)
        │                                        │
MCP: xiaozhi.me ◀──wss── mcp_pipe.py ──stdio── server.py
```

- **Đã test**: `/health` OK; `/stream/b866437922.mp3` trả MP3 hợp lệ
  (5:00.53, 128kbps, 48kHz stereo, 4.8MB) — convert on-the-fly, 0 file đĩa.
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
