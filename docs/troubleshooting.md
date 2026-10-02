# Troubleshooting & Debugging Notes

> Tổng hợp các lỗi đã gặp, cách debug và fix. Dùng làm tham khảo cho việc maintain sau này.

---

## 1. Music Player: Log format %lld → %lu (music_player.cc)

### Vấn đề
Log `'Stream finished, ~%lld k samples written, %d decode errors'` bị lệch argument → log ra số giả `1070485864` (không phải số decode error thật).

### Nguyên nhân root cause
- ESP-IDF **KHÔNG hỗ trợ `%lld`** (long long)
- Con số `1070485864` thực ra là **lỗi mạng** (TCP abort / socket not connected), không phải decode MP3 hỏng
- Firmware đang đếm decode errors kể cả khi gặp lỗi transport

### Sửa
```cpp
// Trước (sai)
ESP_LOGI(TAG, "Stream finished, ~%lld k samples written, %d decode errors",
         (long long)(pcm_written / 1000), decode_errors);

// Sau (đúng)
ESP_LOGI(TAG, "Stream finished, ~%lu k samples written, %d decode errors",
         (unsigned long)(pcm_written / 1000), decode_errors);
```

---

## 2. Music Streaming: FFmpeg -re flag (server.py)

### Vấn đề
FFmpeg decode quá nhanh so với realtime → buffer tràn / underrun

### Sửa
Thêm `-re` flag vào ffmpeg command:
```python
# Trước
["ffmpeg", "-i", url, "-f", "mp3", "-b:a", "128k", "pipe:1"]

# Sau
["ffmpeg", "-re", "-i", url, "-f", "mp3", "-b:a", "128k", "pipe:1"]
```
- `-re`: đọc input theo tốc độ realtime (native frame rate)
- Giúp ffmpeg không "vượt mặt" network speed

---

## 3. Music Streaming: Percent decode (music_player.cc)

### Vấn đề
`percent-ll-d` format specifier sai → warning build / log sai

### Sửa
```cpp
// Trước
ESP_LOGI(TAG, "Download: %lld%%", percent);

// Sau
ESP_LOGI(TAG, "Download: %lu%%", percent);
```

---

## 4. LVGL Warning: nullptr notification_label_ (lvgl_display.cc)

### Vấn đề
LVGL warning khi truy cập `notification_label_` chưa init (nullptr)

### Sửa
Thêm nullptr check trước khi dùng:
```cpp
if (notification_label_ != nullptr) {
    lv_label_set_text(notification_label_, text);
}
```

---

## 5. Radio Server: Hanging ffmpeg when client stops (server.py)

### Vấn đề
Khi user dừng phát (client disconnect), server giữ ffmpeg chạy mãi → rò rỉ tiến trình.

### Nguyên nhân
Code dọn queue `while True: q.get_nowait()` xóa toàn bộ queue → `q.put(done)` không bao giờ thực thi → reader không nhận "done" → ffmpeg không bị kill.

### Sửa (dòng 2162, 2200-2205)
```python
# Trước
_qd = ("done", None)
# ...
while True:
    _tag, _ = q.get_nowait()
q.put(_qd)

# Sau
done = object()
# ...
try:
    while True:
        _tag, _ = q.get_nowait()
except queue.Empty:
    pass
q.put(done)
```

### Kết quả
- Client ngắt → `done` signal đến reader → ffmpeg dừng ngay
- Watchdog 12s xử lý client ngắt đột ngột
- Mark URL "bad" 10 phút → tránh failover chọn lại trạm chết

---

## 6. Radio Server: Queue full 30s = client disconnect (server.py)

### Vấn đề
Client ngắt nhưng Render/uvicorn chỉ phát hiện socket write fail sau ~1-2 phút backpressure

### Giải pháp
Queue 256 chunks. Nếu queue **FULL liên tục 30s** → coi là client đã ngắt:
```python
if full_since is None:
    full_since = time.time()
elif time.time() - full_since > 30:
    break  # Dọn queue + kill ffmpeg
```

---

## 7. Radio Server: 12s watchdog no audio (server.py)

### Vấn đề
Trạm Zing Bolero HLS: playlist 200 OK nhưng segment host không phản hồi → ffmpeg treo

### Giải pháp
```python
live_deadline = time.time() + 12
while True:
    item = q.get(timeout=1.0)
    if time.time() > live_deadline:
        if total == 0:
            _radio_mark_bad(url, "không ra audio trong 12s")
        return  # Dừng stream, ffmpeg bị kill ở finally block
    live_deadline = time.time() + 12  # Gia hạn khi có data
```

---

## 8. ESP-IDF Build: Partition size cho assets

### Vấn đề
Emoji/GIF assets lớn → vượt partition `storage` hoặc `factory`

### Giải pháp
- Kiểm tra `partitions.csv`: `storage` partition ≥ 1MB
- Build với `idf.py partition-table` để xem size
- Nên dùng `main/CMakeLists.txt` chọn asset theo board variant

---

## 9. MCP Tool Timeout (firmware)

### Lưu ý
- Handler tool chạy trên **MAIN TASK** → không nên block > 2-3s
- `vTaskDelay()` trong handler KHÔNG làm treo audio/wifi (task riêng), chỉ hoãn main task
- Poll loop `vTaskDelay(30ms) + đọc cảm biến` = chờ + kiểm tra xen kẽ
- Delay thẳng 1 nhịp lớn → KHÔNG kịp phát hiện sự kiện giữa chừng
- Firmware KHÔNG đặt deadline cho tool call (ReplyResult chờ bao lâu cũng gửi được)

---

## 10. Server xiaozhi.me: Từ chối text tùy ý

### Kết quả test
Gửi `SendWakeWordDetected("Hi LyLy, robot vừa phát hiện mép bàn...")` → server trả lỗi:
> "Detect is only for wake words, do not send long texts"

Chỉ chấp nhận text trùng wake word đã đăng ký ("Hi,Lily" / "Hi,莉莉").

→ Kênh này chỉ dùng cho wake word thật. Muốn firmware chủ động gửi nội dung cho LLM: phải tự host server.

## 11. Firmware: Không phản hồi giọng nói (server "fetch failed")

### Triệu chứng
Wake word "Hi Lily" nhận OK, MQTT session mở OK, nhưng server gửi message type `error`
rồi goodbye → session đóng ngay, thiết bị im lặng.

### Fix chẩn đoán
Sửa nhánh unknown-type trong `OnIncomingJson()` tại `main/application.cc` (~dòng 658)
để log toàn bộ JSON:
```cpp
} else {
    char* message_json = cJSON_PrintUnformatted(root);
    ESP_LOGW(TAG, "Unknown message type: %s, content: %s", type->valuestring,
             message_json ? message_json : "(null)");
    cJSON_free(message_json);
}
```
Log bắt được: `{"type":"error","message":"fetch failed","session_id":"..."}`

### Nguyên nhân gốc
`"fetch failed"` là lỗi Node.js phía **server xiaozhi (api.tenclass.net)** — server không
gọi được API model LLM ngược dòng (model **DeepSeek** đang cấu hình bị lỗi).

**KHÔNG phải lỗi firmware hay phần cứng.**

### Xử lý
Đổi model trên server từ DeepSeek sang **Qwen** → thiết bị trả lời bình thường.

---

## 12. Firmware: MP3 decoder (music_player.cc, 06–07/09/2026)

### 12.1 Lỗi build: thiếu component `esp_http_client`
Thêm dependency vào `main/CMakeLists.txt` / `idf_component.yml`.

### 12.2 `Decoder MP3 ... not registered` / `Fail to open decoder MP3 ret -7`
- Nguyên nhân: dùng `esp_audio_simple_dec` (không đăng ký sẵn decoder MP3)
- Sửa: dùng **`esp_audio_dec`** và register decoder MP3 trước khi open

### 12.3 ⚠️ Sự cố Kconfig.projbuild — bài học
Sửa `main/Kconfig.projbuild` sai (không nằm trong `menu ... endmenu` hợp lệ) làm
**toàn bộ board biến mất khỏi menuconfig** → build không chọn được board.
Xử lý: dùng `git diff` để xem lại thay đổi Kconfig, khôi phục cấu trúc menu.

**Bài học**: `main/Kconfig.projbuild` là file **cấu hình build** — mọi thay đổi phải giữ
đúng cấu trúc menu, và quy ước dự án là **hỏi trước khi sửa** các file build
(`scripts/build.py`, `sdkconfig*`, `CMakeLists.txt`).

---

## 13. Firmware: WiFi RX buffer quá thấp → jitter khi stream nhạc

### Chẩn đoán (đo, không đoán)
- Decode OK (24000 Hz 1ch, không error), kết nối OK, pre-buffer 24576 OK — **nhưng vẫn giật**
- Soi chuỗi audio: `NoAudioCodec::Write` dùng `i2s_channel_write(portMAX_DELAY)` (blocking,
  pacing I2S đúng) → đường loa chuẩn. Giật = **queue cạn (underrun)**
- **Đo tốc độ server** (curl 20s): **240 KB/s ≈ 1.9 Mbps** — nhanh hơn realtime
  (16KB/s @128k) **15 lần** → server/YouTube KHÔNG throttle. Bottleneck ở ESP32

### Nguyên nhân
`sdkconfig.defaults.esp32s3`: `STATIC_RX=3`, `DYNAMIC_RX=6`, `RX_BA_WIN=3` — **cực thấp**
(IDF mặc định 10/32/6). Khi nhạc burst dữ liệu → tràn RX buffer → mất gói →
retransmit → jitter. Log boot xác nhận: `static rx buffer num: 3`, `dynamic rx buffer num: 6`, `rx ba win: 3`

### Sửa (file nguồn, build áp dụng khi regenerate sdkconfig)
| Thông số | Trước | Sau |
|---|---|---|
| `STATIC_RX` | 3 | **10** |
| `DYNAMIC_RX` | 6 | **24** |
| `RX_BA_WIN` | 3 | **6** |

Chi phí RAM nội bộ ~11KB static + tối đa ~29KB dynamic (PSRAM 8MB dư).

### Jitter buffer firmware
| Thông số | Trước | Sau |
|---|---|---|
| `kReadBufferBytes` | 24KB | **64KB** (`64 * 1024`, PSRAM) |
| `kPreBufferBytes` | 16KB | **64KB** (= `kReadBufferBytes`) |
| `PushPcmToPlaybackQueue` music | default 8 | **20 chunks (~1.7s)** — `music_player.cc:423` (TTS giữ 8) |

> Ghi chú: kế hoạch ban đầu ghi 48KB cho pre-buffer, code hiện hành dùng
> `kPreBufferBytes = kReadBufferBytes` = 64KB.

Tổng jitter tolerance sau sửa: queue 1.7s + inbuf ~4s ≈ **5.7 giây**.

⚠️ Power save (gợi ý Gemini #1): **đã có sẵn** — `WifiStation::SetPowerSaveLevel`
(`78__esp-wifi-connect/wifi_station.cc:309`) set `WIFI_PS_NONE` khi connecting/listening.
⚠️ Tắt thu âm khi phát nhạc (gợi ý #3): **từ chối** — sẽ mất lệnh "dừng nhạc" bằng giọng;
upstream chỉ ~30kbps, không phải thủ phạm.

---

## 14. Đánh giá gợi ý từ Gemini (22/09/2026)

Sếp đưa gợi ý từ Gemini; đánh giá và chỉ áp dụng phần đúng:

| Gợi ý | Kết luận | Ghi chú |
|---|---|---|
| `asyncio.create_task` pre-resolve trong `get_song_url` | ❌ Sai kiến trúc | MCP tool chạy ở main thread (stdio), FastAPI ở thread riêng → không có event loop; yt-dlp là blocking. **Làm lại bằng `threading.Thread`** (`_preresolve_bg`) |
| ffmpeg `-ar 24000 -ac 1` | ✅ Áp dụng | Codec board xuất 24000 Hz mono → firmware **bỏ được resample** |
| `-fflags nobuffer -bufsize 32k` | ⏭️ Bỏ qua | `nobuffer` áp cho input demuxer, có thể gây hại |
| `timeout_ms = 15000` | ✅ Áp dụng | `music_player.cc:103` |
| `SetMusicPlaying(true)` sớm | ✅ Áp dụng | Chuyển lên đầu task (trước handshake) |
| Pre-buffer 16KB | ✅ Áp dụng | Đã bị mất ở lần rewrite trước; thêm lại |
| Null-check `SetStatus` | ✅ Đã có sẵn | Chỉ hạ `ESP_LOGW` → `ESP_LOGD` cho 2 log label-null |

**Bổ sung (Gemini thiếu)**: **retry kết nối 3 lần** (cách 700ms) trong `StreamTask` — đây
mới là fix trực tiếp cho lỗi "văng socket" (`Failed to open connection` → `Music task exit` ngay).

---

## 15. Debug log vô hại trên serial (không cần xử lý)

- `Display: ShowNotification/SetStatus failed: label is nullptr` — OLED chưa tạo label
  trạng thái, chỉ thiếu dòng chữ trạng thái trên màn hình (đã hạ xuống `ESP_LOGD`)
- `wifi:Password length matches WPA2 standards...` — chuẩn driver WiFi
- `AudioCodec: Set output/input enable...` — bật/tắt loa/mic tiết kiệm điện
- `LVGL warning` về `notification_label_` nullptr → đã thêm nullptr check trong
  `lvgl_display.cc`

---

## 16. Build trên Windows

- ⚠️ **KHÔNG** chain `call export.bat && python ...` trong cmd (mất env)
- Dùng .bat wrapper tạm hoặc terminal PowerShell đã source ESP-IDF
- Lệnh chuẩn:
  `python scripts/build.py bread-compact-wifi --name bread-compact-wifi-128x64`
- ⚠️ **KHÔNG tự ý sửa** các file build (`scripts/build.py`, `sdkconfig*`, `CMakeLists.txt`)
  khi chưa hỏi

---
---

## 17. Checklist Debug Music/Radio

1. **Nhiều `underrun #N`** → music task không kịp feed → xem `stats: buffered` (nếu về 0 = mạng chậm; nếu vẫn cao = CPU/thứ tự task)
2. **`errs` tăng nhanh (hàng trăm)** → stream MP3 hỏng → xem lại server ffmpeg
3. **`RSSI` < -70** → sóng yếu, cải thiện vị trí robot
4. **Stats 5 giây/lần**: `stats: buffered X B, errs N, RSSI Y dBm` (qua `esp_wifi_sta_get_ap_info`)