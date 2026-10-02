# Điều Khiển LED Qua Giọng Nói

> **Board**: bread-compact-wifi — LED điều khiển bằng GPIO 11

## 1. Phần cứng

| Hằng số | Chân | Ghi chú |
|---------|------|---------|
| `LAMP_GPIO` | GPIO 11 | LED điều khiển bằng giọng nói (**trước là GPIO 18**) |
| `BUILTIN_LED_GPIO` | GPIO 48 | LED trạng thái của board (`SingleLed`) |

Thay đổi trong `main/boards/bread-compact-wifi/config.h`:
```cpp
// A MCP Test: Control a lamp (LED connected to GPIO 11)
#define LAMP_GPIO GPIO_NUM_11
```

## 2. Cách dùng

Lệnh giọng nói: **"mở đèn led"** / **"bật đèn"** / **"tắt đèn"**.

MCP tools (đăng ký trong `InitializeTools()` của board, class `LampController` ở
`main/boards/common/lamp_controller.h`):

| Tool | Mô tả |
|------|-------|
| `self.lamp.turn_on` | Bật đèn (`gpio_set_level(gpio_num_, 1)`, `power_ = true`) |
| `self.lamp.turn_off` | Tắt đèn (`gpio_set_level(gpio_num_, 0)`, `power_ = false`) |
| `self.lamp.get_state` | Trả `{"power": true}` hoặc `{"power": false}` |

## 3. Trạng thái

- ✅ Đã build, nạp và test OK trên phần cứng (LED GPIO 11 hoạt động)

## 4. Ghi chú liên quan

Xem thêm [`troubleshooting.md`](troubleshooting.md) mục *"Không phản hồi giọng nói"* —
triệu chứng nhận wake word nhưng thiết bị im lặng, nguyên nhân là server trả
`{"type":"error","message":"fetch failed"}` (lỗi model LLM phía server, không phải firmware).