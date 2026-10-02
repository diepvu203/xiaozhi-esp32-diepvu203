# Radio Streaming (Live Radio) — Firmware + Server

> **Kiến trúc**: Firmware (ESP32) ↔ MCP Server (Python/FastAPI) ↔ Radio Browser API ↔ Live Stream (HLS/MP3)

## Tổng quan

Hệ thống cho phép robot phát đài radio trực tuyến (stream liên tục, không có đầu/cuối) thông qua:
1. **Firmware**: Dùng `MusicPlayer::Play(stream_url)` (cùng code với music)
2. **Server**: Radio proxy — tìm đài qua Radio Browser API, stream qua ffmpeg HLS→MP3 transcode
3. **MCP**: Tools trên xiaozhi.me + firmware

## Server: Radio Module (`tools/zing-music-mcp/server.py`)

### Tìm đài
- `search_radio(query, limit=40)`: tìm đài theo tên qua Radio Browser API
- Mirror tự động chọn (`at1.api.radio-browser.info`, `nl1.api.radio-browser.info`...)
- Cache tìm kiếm TTL 1 giờ (`_radio_search_cache`)

### Sắp xếp & Failover
- **`_radio_sort()`**: Trạm chưa bị đánh dấu lỗi lên trước → độ khớp tên → lượt nghe
- **`_radio_bad`**: URL chết (playlist 200 nhưng segment không tải được) → đánh dấu 10 phút (600s TTL)
- **Failover**: Firmware retry 3 lần → server trả các trạm khác theo thứ tự ưu tiên

### Stream (`/radio/{key}.mp3`)
- `_radio_body()`: Primer 2.5s + stream MP3 transcode từ ffmpeg (live=True, vô hạn)
- **Backpressure**: Queue 256 chunks x 16KB (~4MB, ~80s audio)
- **Watchdog**: 
  - 20s drain: ffmpeg không ra byte → đóng response, firmware retry
  - 30s queue full liên tục → coi là client ngắt → dọn queue + kill ffmpeg
  - 12s live watchdog: không nhận audio → `_radio_mark_bad()` + return
- **Client ngắt**: `response.close()` → `stop_evt.set()` → pump thoát → `gen.close()` kill ffmpeg

### MCP Tools (Server)
- `search_radio(query, limit)`: tìm đài
- `get_radio_url(name)`: chuẩn bị đài → trả `status: ready` + `stream_url` + `id` ngay lập tức

## Firmware

### Phát radio
- Gọi `search_radio` → chọn tên chính xác → gọi `get_radio_url(name)` → lấy `stream_url`
- Gọi `self.music.play(stream_url)` → `MusicPlayer` stream HTTP → decode → loa
- **Lưu ý**: Radio là stream liên tục → **KHÔNG** cần chờ tải, mở URL là nghe ngay

### Dừng radio (2 cách)
1. **AI gọi**: `self.music.stop()` từ LLM
2. **Hardware**: Nhấn nút wakeup trên robot → board gọi `MusicPlayer::Stop()`
- **Lưu ý**: Khi phát radio → micro bị tắt (firmware tắt codec input để wake-word không cắt ngang) → **KHÔNG** ra lệnh bằng giọng được

## Fix quan trọng: Server không còn treo khi client dừng phát

### Vấn đề
Khi user dừng phát (ngắt kết nối), server giữ ffmpeg chạy mãi → rò rỉ tiến trình.

### Nguyên nhân
Code dọn queue `while True: q.get_nowait()` xóa toàn bộ queue → `q.put(done)` không bao giờ thực thi → reader không nhận "done" → ffmpeg không bị kill.

### Sửa (`server.py` dòng 2162, 2200-2205)

**Trước:**
```python
_qd = ("done", None)
# ...
while True:
    _tag, _ = q.get_nowait()  # Infinite loop, xóa hết queue
q.put(_qd)  # Không bao giờ chạy đến đây
```

**Sau:**
```python
done = object()  # Sentinel object đúng
# ...
try:
    while True:
        _tag, _ = q.get_nowait()
except queue.Empty:
    pass
q.put(done)  # Luôn thực thi → reader nhận "done" → ffmpeg dừng
```

### Kết quả
- Khi client ngắt: gửi `done` → ffmpeg dừng ngay
- Watchdog 12s xử lý client ngắt đột ngột
- Mark URL "bad" 10 phút → tránh chọn lại
- **Server không còn giữ tiến trình ffmpeg vô hạn**

## Radio Browser API
- Endpoint: `/json/stations/search?name={query}&limit={limit}&hidebroken=true`
- User-Agent bắt buộc: `xiaozhi-music-mcp/1.0`
- Không trailing slash (trả 301 nếu thiếu)