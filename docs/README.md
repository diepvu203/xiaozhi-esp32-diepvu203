# XiaoZhi ESP32 - Master Documentation Index & AI Guide

> **Dành cho AI Assistant / Lập trình viên:** Khi được yêu cầu "đọc tài liệu trong docs" hoặc bắt đầu một nhiệm vụ, hãy tham khảo ngay bảng **Tra cứu nhanh theo tác vụ (Task-to-Doc Matrix)** bên dưới để đọc đúng file chuyên biệt trước khi đọc hoặc sửa code.

---

## 1. Bản đồ phần cứng & Dự án hiện tại

| Hạng mục | Chi tiết phần cứng / Cấu hình | File tài liệu chính |
|---|---|---|
| **Chip xử lý** | ESP32-S3 (ESP-IDF v6.0.2 / legacy v5.5) | [`esp-idf-6-migration.md`](esp-idf-6-migration.md) |
| **Board OLED** | `bread-compact-wifi`: OLED SSD1306 128x64, Mic/Spk I2S, Motor L298N, ToF VL53L0X, LED GPIO 11 | [`robot-movement.md`](robot-movement.md), [`emoji-gif-display.md`](emoji-gif-display.md) |
| **Board LCD** | `bread-compact-wifi-lcd`: Màn hình màu ST7789 240x240 SPI, Motor L298N, ToF VL53L0X, Lamp GPIO 18 | [`bread-compact-wifi-lcd.md`](bread-compact-wifi-lcd.md) |
| **Động cơ & Cảm biến** | 2x N20 Motor qua L298N; Cảm biến ToF VL53L0X chống rơi mép bàn | [`robot-movement.md`](robot-movement.md), [`bread-compact-wifi-lcd.md`](bread-compact-wifi-lcd.md) |
| **Âm thanh & Loa** | Audio pipeline: WakeNet, Opus codec, DSP EQ/Filters | [`audio-quality-tuning.md`](audio-quality-tuning.md), [`../main/audio/README.md`](../main/audio/README.md) |
| **Phát nhạc & Radio** | HTTP MP3 stream từ MCP Cloud Proxy (`tools/zing-music-mcp`) | [`music-streaming.md`](music-streaming.md), [`radio-streaming.md`](radio-streaming.md), [`../tools/zing-music-mcp/README.md`](../tools/zing-music-mcp/README.md) |
| **Kiến trúc Cloud / AI** | AI Server (ASR -> LLM -> TTS), MCP tools, WebSocket / MQTT | [`firmware-knowledge-notes.md`](firmware-knowledge-notes.md), [`websocket.md`](websocket.md), [`mcp-protocol.md`](mcp-protocol.md) |

---

## 2. Tra cứu nhanh theo tác vụ (Task-to-Doc Matrix)

Nếu bạn (hoặc AI) cần làm tác vụ dưới đây, hãy mở ngay file tương ứng:

| Tác vụ / Vấn đề cần giải quyết | File cần đọc | Nội dung trọng tâm |
|---|---|---|
| 🐞 **Gặp lỗi crash, giật tiếng, lỗi mạng, lỗi stream** | [`troubleshooting.md`](troubleshooting.md) | 18 lỗi thường gặp (lệch format log `%lld`, FFmpeg `-re`, buffer overrun, điếc mic sau khi dừng nhạc...) |
| 🤖 **Lập trình xe robot, động cơ L298N, chống rơi vực ToF** | [`robot-movement.md`](robot-movement.md)<br>[`bread-compact-wifi-lcd.md`](bread-compact-wifi-lcd.md) | Sơ đồ chân động cơ, giải quyết nhiễu điện từ EMI, thuật toán chống rơi bàn, cấu hình chân theo từng board |
| 📺 **Cấu hình board LCD màu ST7789 (`bread-compact-wifi-lcd`)** | [`bread-compact-wifi-lcd.md`](bread-compact-wifi-lcd.md) | Pinout riêng của board LCD (ToF SDA=17, SCL=39, motor, lamp GPIO 18, ST7789 SPI) |
| 😊 **Tùy chỉnh màn hình OLED SSD1306, biểu cảm emoji, GIF** | [`emoji-gif-display.md`](emoji-gif-display.md) | Bộ nhớ OLED, render frame GIF, biểu cảm trạng thái AI |
| 🎵 **Tích hợp phát nhạc (YouTube/SoundCloud/NCT), fix stream nhạc** | [`music-streaming.md`](music-streaming.md)<br>[`../tools/zing-music-mcp/README.md`](../tools/zing-music-mcp/README.md) | Kiến trúc MCP streamable HTTP, proxy transcode MP3, preload cache disk, nút tắt nhạc |
| 📻 **Phát Radio trực tuyến (Live streams)** | [`radio-streaming.md`](radio-streaming.md) | Streaming radio liên tục, công cụ MCP tìm trạm radio |
| 🔊 **Chỉnh chất lượng âm thanh loa (EQ, DSP, chống rè)** | [`audio-quality-tuning.md`](audio-quality-tuning.md) | Bộ lọc DSP IIR, cấu hình EQ cho loa nhỏ 8Ω/3W, chống méo tiếng |
| 💡 **Điều khiển đèn LED qua giọng nói** | [`led-control.md`](led-control.md) | GPIO 11, MCP tool bật/tắt/đổi màu đèn |
| 🧠 **Hiểu luồng Firmware ↔ MCP Tool ↔ Server LLM** | [`firmware-knowledge-notes.md`](firmware-knowledge-notes.md) | Hai kênh gửi dữ liệu lên LLM, cơ chế timeout MCP tool, luồng Wake Word |
| 🔌 **Tạo board mới hoặc thêm variant phần cứng** | [`custom-board.md`](custom-board.md) | Chuỗi liên kết `config.json` -> `build.py` -> `Kconfig` -> `CMakeLists.txt` -> code board |
| 🔄 **Nâng cấp / Tương thích ESP-IDF v6** | [`esp-idf-6-migration.md`](esp-idf-6-migration.md) | API breaking changes, I2S/GPIO mới, các thư viện tương thích |
| 📝 **Quy chuẩn viết code C/C++** | [`code_style.md`](code_style.md) | Naming convention, formatting `.clang-format`, quản lý bộ nhớ |
| 🌐 **Đặc tả giao thức mạng** | [`websocket.md`](websocket.md)<br>[`mqtt-udp.md`](mqtt-udp.md)<br>[`mcp-protocol.md`](mcp-protocol.md) | Định dạng JSON gói tin, chu kỳ kết nối, MCP client/server |

---

## 3. Danh mục chi tiết toàn bộ tài liệu

### 3.1. Tính năng & Phần cứng (Hardware & Features)

| File | Mô tả chi tiết |
|---|---|
| [`troubleshooting.md`](troubleshooting.md) | **Tổng hợp lỗi & cách fix:** Chi tiết 18 lỗi đã gặp trong quá trình làm robot, phát nhạc, audio pipeline. |
| [`robot-movement.md`](robot-movement.md) | **Robot xe cơ bản:** Motor N20 + Driver L298N + Cảm biến ToF VL53L0X, xử lý nhiễu điện từ EMI (board OLED). |
| [`bread-compact-wifi-lcd.md`](bread-compact-wifi-lcd.md) | **Biến thể LCD ST7789:** Ghi chú port tính năng robot & ToF sang board LCD, bảng chân GPIO chi tiết. |
| [`emoji-gif-display.md`](emoji-gif-display.md) | **Màn hình OLED 128x64:** Hiển thị Emoji động & GIF tương ứng với trạng thái trợ lý ảo. |
| [`music-streaming.md`](music-streaming.md) | **Music Streaming:** Luồng phát nhạc từ SoundCloud/YouTube/NCT qua MCP Server và firmware. |
| [`audio-quality-tuning.md`](audio-quality-tuning.md) | **Chất lượng âm thanh:** Căn chỉnh EQ / DSP speaker để cải thiện âm bass/treble cho loa nhỏ. |
| [`radio-streaming.md`](radio-streaming.md) | **Radio trực tuyến:** Phát live stream các kênh radio toàn cầu. |
| [`led-control.md`](led-control.md) | **Điều khiển LED:** Điều khiển LED qua giọng nói trên GPIO 11. |

### 3.2. Kiến trúc & Giao thức (Architecture & Protocols)

| File | Mô tả chi tiết |
|---|---|
| [`firmware-knowledge-notes.md`](firmware-knowledge-notes.md) | **Kiến trúc Firmware & Server:** Cơ chế 2 kênh gửi tin lên LLM, timeout MCP tool, Wake Word. |
| [`custom-board.md`](custom-board.md) | **Hướng dẫn thêm board mới:** Quy trình khai báo board chuẩn theo kiến trúc XiaoZhi (+ bản `_zh`). |
| [`websocket.md`](websocket.md) | **Giao thức WebSocket:** Đặc tả tin nhắn JSON, luồng audio streaming hai chiều (+ bản `_zh`). |
| [`mqtt-udp.md`](mqtt-udp.md) | **Giao thức MQTT + UDP:** Phương thức truyền nhận dữ liệu qua MQTT/UDP (+ bản `_zh`). |
| [`mcp-protocol.md`](mcp-protocol.md) | **Giao thức MCP:** Mô hình Model Context Protocol triển khai trên thiết bị nhúng (+ bản `_zh`). |
| [`mcp-usage.md`](mcp-usage.md) | **Sử dụng MCP Tool:** Hướng dẫn định nghĩa và gọi MCP tools trên firmware (+ bản `_zh`). |
| [`code_style.md`](code_style.md) | **Quy tắc viết code:** Tiêu chuẩn code C/C++, format, đặt tên (+ bản `_zh`). |
| [`esp-idf-6-migration.md`](esp-idf-6-migration.md) | **Chuyển đổi ESP-IDF v6:** Tài liệu hướng dẫn tương thích ESP-IDF v6.x. |
| [`blufi.md`](blufi.md) | **Cấu hình WiFi qua Bluetooth:** BluFi provisioning (+ bản `_zh`). |
| [`glyph-push.md`](glyph-push.md) | **Glyph Push:** Giao thức đẩy glyph font/hiển thị (+ bản `_zh`). |

### 3.3. Tài liệu ngoài thư mục `docs/`

| Đường dẫn | Nội dung |
|---|---|
| [`../tools/zing-music-mcp/README.md`](../tools/zing-music-mcp/README.md) | **Server MCP Nhạc Việt:** FastAPI proxy, yt-dlp, cache disk MP3, deploy Render. |
| [`../main/audio/README.md`](../main/audio/README.md) | **Kiến trúc Audio Service:** AudioEngine, AfeAudioEngine, LiteAudioEngine, PSRAM ring buffer. |
| [`../AGENTS.md`](../AGENTS.md) | **Quy ước dự án & Chỉ dẫn AI:** Lệnh build, kiểm thử, nguyên tắc bất biến của firmware. |
| [`../README.md`](../README.md) | **README gốc:** Giới thiệu tổng quan dự án XiaoZhi. |

### 3.4. Lưu trữ lịch sử (Archive)

- [`archive/task-summary-emoji-gif.md`](archive/task-summary-emoji-gif.md): File log tổng hợp ban đầu (1172 dòng) ghi lại quá trình phát triển tính năng OLED, motor và âm thanh. Đã tách nội dung vào các file chuyên biệt ở trên.

---

## 4. Cấu trúc cây thư mục `docs/`

```text
docs/
├── README.md                       # File chỉ mục này (Master Index & AI Guide)
│
├── [Tính năng & Phần cứng]
├── troubleshooting.md              # Tổng hợp 17 lỗi và cách fix
├── robot-movement.md               # Xe robot: Motor N20 + L298N + ToF (board OLED)
├── bread-compact-wifi-lcd.md       # Cấu hình chân & port robot cho board LCD ST7789
├── emoji-gif-display.md            # Hiển thị biểu cảm Emoji & GIF trên OLED SSD1306
├── music-streaming.md              # Phát nhạc SoundCloud/YouTube/NCT qua MCP
├── audio-quality-tuning.md         # Chỉnh EQ / DSP cho loa
├── radio-streaming.md              # Phát radio trực tuyến
├── led-control.md                  # Điều khiển đèn LED (GPIO 11)
│
├── [Kiến trúc & Giao thức]
├── firmware-knowledge-notes.md     # Cơ chế giao tiếp firmware, MCP tool, server LLM
├── custom-board.md                 # Hướng dẫn tạo và cấu hình board mới (+ _zh)
├── websocket.md                    # Giao thức WebSocket (+ _zh)
├── mqtt-udp.md                     # Giao thức MQTT/UDP (+ _zh)
├── mcp-protocol.md                 # Đặc tả MCP protocol (+ _zh)
├── mcp-usage.md                    # Hướng dẫn dùng MCP (+ _zh)
├── code_style.md                   # Tiêu chuẩn mã nguồn (+ _zh)
├── esp-idf-6-migration.md          # Hướng dẫn di chuyển ESP-IDF v6
├── blufi.md                        # Cấu hình WiFi BluFi (+ _zh)
├── glyph-push.md                   # Giao thức Glyph push (+ _zh)
├── mcp-based-graph.jpg             # Sơ đồ minh họa kiến trúc MCP
│
└── [Lịch sử & Bản cũ]
    ├── archive/                    # File tổng hợp lịch sử
    ├── v0/                         # Tài liệu phiên bản v0
    └── v1/                         # Tài liệu phiên bản v1
```

---

## 5. Quy tắc duy trì tài liệu

1. **Khi phát triển tính năng mới:** Luôn tạo file `.md` riêng trong `docs/`, cập nhật vào bảng tra cứu của `README.md` này.
2. **Khi sửa được một bug hóc búa:** Ghi lại lỗi, nguyên nhân gốc (root cause) và cách sửa vào [`troubleshooting.md`](troubleshooting.md).
3. **Không ghi đè lộn xộn:** Tránh gộp tất cả vào một file lớn làm loãng context của AI.