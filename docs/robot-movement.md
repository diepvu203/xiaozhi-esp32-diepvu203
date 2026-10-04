# Robot Xe: Motor N20 + L298N + ToF Sensor

> **Board**: bread-compact-wifi — OLED SSD1306 128x64, LED GPIO 11, firmware v2.4.2

> The robot features have also been ported to the separate LCD variant
> `bread-compact-wifi-lcd`. Its display selection and pin assignments differ;
> see [bread-compact-wifi-lcd.md](./bread-compact-wifi-lcd.md) before wiring or
> building that variant.

## 1. Sơ đồ chân

### Motor (L298N)

| L298N | ESP32 | Ghi chú |
|-------|-------|---------|
| IN1 | GPIO1 | Motor trái |
| IN2 | GPIO2 | Motor trái |
| IN3 | GPIO21 | Motor phải |
| IN4 | GPIO8 | Motor phải |
| ENA/ENB | mức cao cố định (jumper/3V3) | Tốc độ không đổi, chưa dùng PWM |

### ToF (VL53L0X)

| VL53L0X | ESP32-S3 | Ghi chú |
|---------|----------|---------|
| SDA | GPIO40 | I2C (`TOF_I2C_SDA`) |
| SCL | GPIO39 | I2C (`TOF_I2C_SCL`) |
| XSHUT | GPIO10 | (`TOF_XSHUT`) |
| — | 2.8V | Dùng `USE_I2C_2V8=1` |

> **Lưu ý**: KHÔNG dùng GPIO35/36/37 (PSRAM octal trên ESP32-S3).
> GPIO39/40 trước là nút âm lượng → đã bỏ 2 nút bấm tăng/giảm âm lượng.

## 2. MCP Tools (tên thật trong code)

### Động cơ — `main/boards/common/motor_controller.h`

**Một tool duy nhất** `self.motor.move` với property `action`:

| action | Ý nghĩa |
|--------|---------|
| `forward` | Tiến (auto-stop sau 1000ms) |
| `backward` | Lùi (auto-stop sau 1000ms) |
| `left` | Xoay trái tại chỗ |
| `right` | Xoay phải tại chỗ |
| `forward_until_stop` | Tiến liên tục đến khi nhận `stop` (tối đa `kMaxContinuousMs` = 10s) |
| `backward_until_stop` | Lùi liên tục đến khi nhận `stop` |
| `stop` | Dừng cả 2 động cơ |

### Đèn LED — `main/boards/common/lamp_controller.h`

| Tool | Mô tả |
|------|-------|
| `self.lamp.turn_on` | Bật đèn |
| `self.lamp.turn_off` | Tắt đèn |
| `self.lamp.get_state` | Trả `{"power": true/false}` |

### Nhạc — `InitializeTools()` trong `compact_wifi_board.cc`

| Tool | Mô tả |
|------|-------|
| `self.music.play(url, name)` | Phát nhạc từ stream URL (lỗi nếu đang phát) |
| `self.music.stop` | Dừng nhạc đang phát |
| `self.music.get_state` | Trả `{"playing": true/false}` |

## 3. Hằng số quan trọng (`motor_controller.h`)

| Hằng số | Giá trị | Ý nghĩa |
|---------|---------|---------|
| `kNoFloorMm` | 30 | Ngưỡng mất sàn: đọc > 30mm → chặn lệnh tiến |
| `kErrorMm` | 8191 | Giá trị trả về khi cảm biến không đọc được |
| `kMonitorPollMs` | 30 | Chu kỳ giám sát sàn khi motor đang chạy tiến |
| `kBackOffMs` | 200 | Thời gian lùi lại khi phát hiện mất sàn giữa chừng |
| `kMaxContinuousMs` | 10000 | Giới hạn lệnh `*_until_stop` |
| `kMaxRunSessionMs` | 3000 | Giới hạn 1 phiên chạy (gộp lệnh nối tiếp) |
| `kRunGapMs` | 5000 | Khoảng cách để coi là phiên chạy mới |
| `kWiggleCooldownMs` | 10000 | Cooldown lắc lư |

## 4. Lắc lư phản hồi Wake Word

- Khi hô wake word, robot lắc nhanh 1 nhịp trái-phải (~0.1s, 50ms mỗi hướng)
- Hook `Board::OnWakeWordDetected()` (virtual no-op, `main/boards/common/board.h:89`)
- Được gọi từ `Application::HandleWakeWordDetectedEvent()` **CHỈ khi state == Idle**
- `CompactWifiBoard::OnWakeWordDetected()` override → `GetMotor().Wiggle()`
- Chạy trong task one-shot riêng (không block main task, không cản audio channel)
- Cooldown 10s: wake word bắn nhiều sự kiện (Idle + abort Speaking) nên trước đây robot lắc 2 lần

## 5. Chiến lược an toàn chống rơi

1. **Đo trước khi chạy**: mỗi lệnh `move` (trừ `stop`) đo ToF 1 lần → ghi log `ToF distance before move: N mm`
2. **Chặn tiến khi mất sàn**: `forward` / `forward_until_stop` với `mm > kNoFloorMm` → `throw std::runtime_error("Cannot move forward: table edge detected (cliff)...")` → LLM đọc lỗi, cảnh báo người dùng
3. **Giám sát giữa chừng**: task `motor_floor_monitor` đo mỗi 30ms **chỉ khi** `moving_forward_ == true`; phát hiện mất sàn → lùi lại 200ms rồi dừng
4. **Chống chạy dài**: LLM hay gửi nhiều lệnh forward nối tiếp (mỗi lệnh auto-stop 1s) → gộp lệnh cách nhau < 5s thành 1 phiên, quá 3s thì từ chối: *"Robot has been moving continuously for too long. It needs to rest."*

## 6. Nhiễu điện từ: không nghe được khi motor chạy

### Chẩn đoán — KHÔNG phải lỗi firmware
- MCP tool chỉ gọi `gpio_set_level()` (không block), auto-stop dùng `esp_timer` (task riêng)
- Task audio (thu âm, wake word) chạy độc lập, vẫn hoạt động khi motor chạy

### Nguyên nhân thật: EMI từ L298N + motor N20
1. **Tia lửa chổi than motor** → nhiễu rộng dải lọt vào mic INMP441 → wake word không nhận diện
2. **Sụt áp nguồn chung** (ESP32 + L298N dùng chung nguồn) → motor kéo dòng làm nguồn sụt, I2S/WiFi lỗi
3. **GND bẩn**: dòng motor qua dây GND chung tạo nhiễu cộng vào tín hiệu mic
4. **Tiếng ồn cơ học**: mic gần motor → SNR giảm

### Phần cứng đã lắp thực tế
| Linh kiện | Vị trí | Tác dụng |
|-----------|--------|----------|
| Tụ 104 (100nF) | Song song **2 cực mỗi động cơ** N20 | Chống tia lửa/EMI từ chổi than tại nguồn phát |
| Tụ 104 (100nF) | Song song **đường 3.3V nuôi mic** (gần chân mic) | Lọc nhiễu nguồn cho INMP441 |
| Tụ hóa 2200µF/16V | Song song **nguồn 5V chính** | Chống sụt áp khi motor khởi động/đổi chiều |

### Cách xác nhận nhanh
- Quay tay động cơ (không cấp điện) rồi nói lệnh → nghe được bình thường = chắc chắn do nhiễu điện
- Cấp nguồn motor từ nguồn riêng → hết chứng tỏ do sụt áp/nhiễu nguồn

## 7. File quan trọng

| File | Nội dung |
|------|----------|
| `main/boards/bread-compact-wifi/config.h` | `LAMP_GPIO`, `MOTOR_L_IN1/IN2`, `MOTOR_R_IN1/IN2`, `TOF_I2C_SDA/SCL`, `TOF_XSHUT` |
| `main/boards/bread-compact-wifi/compact_wifi_board.cc` | `InitializeTools()`, `GetMotor()`, `OnWakeWordDetected()` |
| `main/boards/common/motor_controller.h` | `MotorController`, `Wiggle()`, `Move()`, tool `self.motor.move` |
| `main/boards/common/lamp_controller.h` | `LampController`, 3 tool `self.lamp.*` |
| `main/boards/common/tof_sensor.h` | `TofSensor`, `ReadDistanceMm()`, `IsReady()` |
| `main/boards/common/board.h` | Hook `virtual void OnWakeWordDetected() {}` |

## 8. Build ToF VL53L0X (ghi chú)

- Component: `components/vl53l0x/` (đã vendor vào repo); nguồn private core/platform glob trong `main/CMakeLists.txt` cho cả `CONFIG_BOARD_TYPE_BREAD_COMPACT_WIFI` và `CONFIG_BOARD_TYPE_BREAD_COMPACT_WIFI_LCD`
- Thêm `target_compile_definitions(... USE_I2C_2V8=1)`
- Kiểm chứng: `nm` thấy đủ symbol, `libmain.a` chứa 9 file .obj vl53
- **KHÔNG dùng GPIO35/36/37** — các chân này là PSRAM octal trên ESP32-S3

## 9. Vấn đề đã biết

- Server xiaozhi.me **TỪ CHỐI** text tùy ý qua `SendWakeWordDetected` → callback `motor.SetWakeNotifier()` đã **TẮT gọi** `SendRobotAlert` (xem comment tại chỗ trong `compact_wifi_board.cc:192-202`)
- Muốn robot tự báo cáo sự kiện (rơi, pin yếu) → cần tự host server hoặc dùng `ToggleChatState` + tool trạng thái
- Quy ước build: không tự chạy build trong session; báo user build bằng
  `python scripts/build.py bread-compact-wifi --name bread-compact-wifi-128x64`

## 10. Trạng thái

- [x] Code điều khiển bánh xe + LED + ToF
- [x] Nhiễu điện từ đã khắc phục bằng tụ lọc
- [ ] Test đầy đủ lệnh giọng nói trên phần cứng: "đi tới", "đi lùi", "xoay trái", "xoay phải", "dừng lại"
- [ ] Sau này: lệnh phức tạp (đi khoảng cách, PWM tốc độ)