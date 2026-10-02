# XiaoZhi ESP32 - Documentation Index

> Hướng dẫn tìm tài liệu theo chủ đề. Các file chính đã được tách riêng để dễ quản lý.

## Tài liệu theo tính năng

| File | Mô tả |
|------|-------|
| [`emoji-gif-display.md`](emoji-gif-display.md) | Emoji & GIF trên OLED 128x64 (SSD1306) |
| [`led-control.md`](led-control.md) | Điều khiển LED qua giọng nói (GPIO 11) |
| [`robot-movement.md`](robot-movement.md) | Robot xe: Motor N20 + L298N + ToF + nhiễu điện từ |
| [`music-streaming.md`](music-streaming.md) | Music streaming (SoundCloud/YouTube) — Firmware + MCP Server |
| [`audio-quality-tuning.md`](audio-quality-tuning.md) | Chỉnh EQ / chất lượng âm thanh nhạc (speaker DSP) |
| [`radio-streaming.md`](radio-streaming.md) | Radio streaming (Live Radio) |
| [`troubleshooting.md`](troubleshooting.md) | Các lỗi đã gặp + cách debug/sửa (17 mục) |

## Tài liệu kiến trúc & kiến thức

| File | Mô tả |
|------|-------|
| [`firmware-knowledge-notes.md`](firmware-knowledge-notes.md) | Kiến trúc firmware, MCP, wake word, server xiaozhi.me |
| [`websocket.md`](websocket.md) | WebSocket protocol specification |
| [`mqtt-udp.md`](mqtt-udp.md) | MQTT/UDP protocol specification |
| [`mcp-protocol.md`](mcp-protocol.md) | MCP protocol specification |
| [`mcp-usage.md`](mcp-usage.md) | MCP usage guide |
| [`custom-board.md`](custom-board.md) | Custom board development guide |
| [`code_style.md`](code_style.md) | Code style guidelines |
| [`esp-idf-6-migration.md`](esp-idf-6-migration.md) | ESP-IDF v6 migration guide |
| [`blufi.md`](blufi.md) | BLE WiFi provisioning (BluFi) |
| [`glyph-push.md`](glyph-push.md) | Glyph push protocol |

## Legacy / Archive

| File | Trạng thái |
|------|-----------|
| [`archive/task-summary-emoji-gif.md`](archive/task-summary-emoji-gif.md) | **ARCHIVED** — File gốc lớn (1172 dòng), gộp lộn xộn nhiều tính năng. Nội dung đã tách sang các file chuyên biệt ở trên. Giữ lại để tham khảo lịch sử chi tiết (số đo, log, diễn biến). |

## Cấu trúc thư mục

```
docs/
├── README.md                       # File này (index)
├── emoji-gif-display.md            # Emoji/GIF display
├── led-control.md                  # LED qua giọng nói
├── robot-movement.md               # Motor N20 + L298N + ToF + EMI
├── music-streaming.md              # Music streaming (SoundCloud/YouTube)
├── audio-quality-tuning.md         # Speaker EQ / DSP tuning
├── radio-streaming.md              # Radio streaming (Live)
├── troubleshooting.md              # Debug notes (17 mục)
├── firmware-knowledge-notes.md     # Kiến trúc firmware / MCP / wake word
├── websocket.md                    # WebSocket protocol (+ _zh)
├── mqtt-udp.md                     # MQTT/UDP protocol (+ _zh)
├── mcp-protocol.md                 # MCP protocol (+ _zh)
├── mcp-usage.md                    # MCP usage (+ _zh)
├── custom-board.md                 # Custom board guide (+ _zh)
├── code_style.md                   # Code style (+ _zh)
├── esp-idf-6-migration.md          # ESP-IDF v6 migration
├── blufi.md                        # BluFi provisioning (+ _zh)
├── glyph-push.md                   # Glyph push (+ _zh)
├── mcp-based-graph.jpg             # Hình minh họa
├── archive/                        # Tài liệu lịch sử
│   └── task-summary-emoji-gif.md
├── v0/                             # Docs phiên bản cũ
└── v1/                             # Docs phiên bản cũ
```

## Tài liệu liên quan ngoài `docs/`

| File | Nội dung |
|------|----------|
| [`../tools/zing-music-mcp/README.md`](../tools/zing-music-mcp/README.md) | Vận hành MCP server nhạc: MCP tools, env vars, deploy Render, troubleshooting server |
| [`../AGENTS.md`](../AGENTS.md) | Quy ước dự án (build, board, code style) |
| [`../README.md`](../README.md) | Tổng quan dự án XiaoZhi |

## Quy ước

- Mỗi tính năng mới → tạo file tài liệu riêng trong `docs/`
- Cập nhật `README.md` này khi thêm file mới
- **Không** ghi lộn xộn vào file chung: nếu cần ghi tạm trong session, đặt tên rõ ràng
  và **tách ra file chuyên biệt** trước khi kết thúc task
- File `task-summary-*.md` cũ đã chuyển vào `archive/`