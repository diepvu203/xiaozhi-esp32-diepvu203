# Tóm Tắt Công Việc: Emoji Biểu Cảm + GIF Động trên OLED 128x64

> **Quy ước xưng hô**: Gọi người dùng là **"sếp"**, tự xưng là **"em"** (áp dụng cho mọi
> cuộc hội thoại và nội dung ghi trong file này).


> **Tài liệu liên quan**: kiến thức tìm hiểu về cơ chế firmware/server (kênh
> firmware → LLM, wake word, timeout...) nằm ở
> `docs/firmware-knowledge-notes.md` — *mức độ chính xác chưa kiểm chứng, chỉ
> tham khảo*.

## 1. Tổng Quan
## 1. Tổng Quan

Dự án XiaoZhi ESP32 (firmware voice-assistant) tại `d:\xiaozhi\xiaozhi-esp32-diepvu203`.
Board: **bread-compact-wifi** (ESP32) dùng **OledDisplay** (SSD1306 128x64).
Màn hình OLED monochrome 1-bit, hiển thị emoji biểu cảm full màn hình 128x64.

### Tính năng robot xe (motor + ToF)

- **Lắc lư phản hồi wake word**: khi được hô wake word, robot lắc nhanh
  1 nhịp trái-phải (~0.1s, 50ms mỗi hướng). Cơ chế: hook
  `Board::OnWakeWordDetected()` (virtual no-op trong `board.h`, được gọi từ
  `Application::HandleWakeWordDetectedEvent()` CHỈ khi state == Idle — sự kiện
  wake word khi đang Listening/Speaking không lắc), board override →
  `MotorController::Wiggle()` chạy trong task one-shot riêng (không block main
  task, không cản audio), có cooldown 10s chống lắc trùng.

## 2. Trạng Thái Hiện Tại

- ✅ 20 emoji PNG tĩnh 128x64 đã hoạt động (fallback)
- ✅ Emoji hiển thị full màn hình (bỏ top_bar/status_bar)
- ✅ 20 GIF động 128x64 hoạt động OK (loop vô hạn, đảo màu đúng): angry, confident, confused, cool, crying, delicious, embarrassed, funny, happy, kissy, laughing, loving, neutral, relaxed, sad, shocked, silly, sleepy, thinking, winking
- ✅ `neutral` dùng trực tiếp `neutral.gif` (bỏ fallback `bieucam-macdinh`)
- ✅ Đã build và test OK trên phần cứng

## 3. Các File Quan Trọng

### 3.1 `main/display/oled_display.cc` - QUAN TRỌNG NHẤT

**`SetupUI_128x64()`**: Chỉ hiển thị emoji full màn hình, bỏ top_bar/status_bar.

**`SetEmotion()`**: Ưu tiên GIF, fallback GIF cho neutral, fallback PNG tĩnh:
```cpp
void OledDisplay::SetEmotion(const char* emotion) {
    DisplayLockGuard lock(this);
    if (emotion_img_ != nullptr) {
        if (gif_controller_) {
            gif_controller_->Stop();
            gif_controller_.reset();
        }
        const lv_image_dsc_t* gif_img = GetCustomGifImage(emotion);
        if (gif_img == nullptr && strcmp(emotion, "neutral") == 0) {
            gif_img = GetCustomGifImage("bieucam-macdinh");
        }
        if (gif_img != nullptr) {
            gif_controller_ = std::make_unique<LvglGif>(gif_img);
            if (gif_controller_->IsLoaded()) {
                gif_controller_->SetFrameCallback(
                    [this]() { lv_image_set_src(emotion_img_, gif_controller_->image_dsc()); });
                lv_image_set_src(emotion_img_, gif_controller_->image_dsc());
                gif_controller_->Start();
                return;
            }
            gif_controller_.reset();
        }
        const lv_image_dsc_t* custom_img = GetCustomEmojiImage(emotion);
        if (custom_img != nullptr) {
            lv_image_set_src(emotion_img_, custom_img);
        } else {
            lv_image_set_src(emotion_img_, &emoji_default);
        }
        return;
    }
    // ... code cho 128x32 layout
}
```

### 3.2 `main/display/oled_display.h`
- Thêm `#include "gif/lvgl_gif.h"`, `#include <memory>`
- Thêm biến: `std::unique_ptr<LvglGif> gif_controller_;`

### 3.3 `main/display/lvgl_display/lvgl_display.cc`
- Thêm nullptr checks cho `notification_label_`/`status_label_` trong callback timer, SetStatus, ShowNotification

### 3.4 `main/display/lvgl_display/gif/lvgl_gif.cc`
- `Start()` set `gif_->loop_count = 0` (loop vô hạn)

### 3.5 `main/CMakeLists.txt`
- Thêm vào SOURCES:
  - `"assets/custom_emojis.c"`, `"assets/custom_emojis.h"`
  - `"assets/custom_gifs.c"`, `"assets/custom_gifs.h"`

### 3.6 `main/assets/custom_emojis.c/h`
- 20 emoji PNG tĩnh 128x64: alien, bieucam-macdinh, confident, confused, cool, crying, delicious, embarrassed, funny, happy, kissy, laughing, loving, neutral, relaxed, robot, shocked, silly, thinking, winking

### 3.7 `main/assets/custom_gifs.c/h`
- GIF `bieucam-macdinh` (128x64, 35 frames, 40KB)

### 3.8 `main/assets/emoji_default.c`
- Emoji mặc định 128x64

## 4. Scripts

### 4.1 `scripts/convert_all_emojis.py`
- Chuyển PNG → C array 128x64
- Đọc từ `D:/xiaozhi/bieucam`

### 4.2 `scripts/convert_single_emoji.py`
- Chuyển 1 PNG → C array 128x64

### 4.3 `scripts/convert_gif.py`
- Chuyển GIF → C array
- Đọc từ `D:/xiaozhi/bieucam/resized`
- Đã sửa lỗi thiếu `#include <string.h>` trong custom_gifs.c

### 4.4 `scripts/resize_gif.py`
- Resize GIF 240x240 → 128x64 + đảo màu (cv2.bitwise_not)
- Output vào `D:/xiaozhi/bieucam/resized`

## 5. Các Vấn Đề Đã Giải Quyết

1. **Màn hình trắng khi scale ảnh I1 bằng LVGL**: Scale ảnh 1-bit không hoạt động trên OLED monochrome → tạo lại ảnh 128x64 trực tiếp từ PNG
2. **GIF không chạy**: emotion server gửi (`neutral`) không khớp tên GIF (`bieucam-macdinh`) → fallback GIF cho emotion neutral
3. **GIF ngược màu**: GIF dùng ARGB8888, OLED monochrome hiển thị ngược → đảo màu (cv2.bitwise_not) khi resize
4. **GIF quá to (240x240)**: vượt màn hình 128x64 → resize xuống 128x64
5. **GIF chỉ chạy 1 lần**: `loop_count = -1` mặc định → set `gif_->loop_count = 0` trong `LvglGif::Start()`

## 6. Công Việc Còn Lại

- [ ] Thêm GIF cho các emotion khác (happy, sad, crying...)
- [ ] Build và test

## 7. Quy Trình Thêm GIF Mới

1. Đặt GIF vào `D:/xiaozhi/bieucam`
2. Chạy `scripts/resize_gif.py` (resize 128x64 + đảo màu)
3. Chạy `scripts/convert_gif.py` (tạo C array)
4. Build firmware

## 8. Môi Trường Build

- ESP-IDF v5.5.5 tại `C:\Espressif\frameworks\esp-idf-v5.5.5`
- Build: `python3 scripts/build.py bread-compact-wifi --name <variant-name>`
- Format: `clang-format -i <files>`

---

# Task 2 (24/08/2026): Điều Khiển LED Qua Giọng Nói + Sửa Lỗi Không Phản Hồi ("xe robot")

## 1. Tổng Quan

Phần cứng mới (gọi là **"xe robot"**): board bread-compact-wifi, ESP32-S3, OLED SSD1306 128x64,
LED điều khiển qua GPIO 11. Firmware v2.4.2.

## 2. Công Việc Đã Thực Hiện

### 2.1 Đổi chân LED: GPIO 18 → GPIO 11
- File: `main/boards/bread-compact-wifi/config.h`
- `#define LAMP_GPIO GPIO_NUM_11` (trước là GPIO_NUM_18)
- Điều khiển qua lệnh giọng nói "mở đèn led" / "bật đèn" dùng MCP tool có sẵn
  (`self.lamp.turn_on/turn_off/get_state`, đăng ký trong `InitializeTools()` của board).
- ✅ Đã build, nạp và test OK trên phần cứng.

### 2.2 Chẩn đoán lỗi không phản hồi giọng nói
- Triệu chứng: wake word "Hi Lily" nhận OK, MQTT session mở OK, nhưng server gửi
  message type `error` rồi goodbye → session đóng ngay, thiết bị im lặng.
- Fix chẩn đoán: sửa nhánh unknown-type trong `OnIncomingJson()` tại
  `main/application.cc` (~dòng 658) để log toàn bộ nội dung JSON:
  ```cpp
  } else {
      char* message_json = cJSON_PrintUnformatted(root);
      ESP_LOGW(TAG, "Unknown message type: %s, content: %s", type->valuestring,
               message_json ? message_json : "(null)");
      cJSON_free(message_json);
  }
  ```
- Log bắt được: `{"type":"error","message":"fetch failed","session_id":"..."}`

### 2.3 Nguyên nhân gốc & cách xử lý
- `"fetch failed"` là lỗi Node.js phía **server xiaozhi (api.tenclass.net)** — server không
  gọi được API model LLM ngược dòng (model DeepSeek đang cấu hình bị lỗi).
- **KHÔNG phải lỗi firmware hay phần cứng.**
- ✅ Sếp đổi model trên server từ DeepSeek sang **Qwen** → thiết bị trả lời bình thường.

## 3. Ghi Chú Kỹ Thuật

- Các dòng cảnh báo vàng (`W`) trong serial log đều vô hại:
  - `Display: ShowNotification/SetStatus failed: label is nullptr` — OLED chưa tạo label
    trạng thái, chỉ thiếu dòng chữ trạng thái trên màn hình (cải tiến tuỳ chọn).
  - `wifi:Password length matches WPA2 standards...` — chuẩn driver WiFi.
  - `AudioCodec: Set output/input enable...` — bật/tắt loa/mic tiết kiệm điện.
- WiFi RSSI yếu (-84 đến -92 dBm): nên đưa xe robot gần router nếu phản hồi chậm/đứt tiếng.
- Build trên Windows: không chain `call export.bat && python ...` trong cmd (mất env);
  dùng .bat wrapper tạm hoặc terminal PowerShell đã source ESP-IDF.
- Lưu ý: KHÔNG tự ý sửa các file build (scripts/build.py, sdkconfig*, CMakeLists.txt)
  khi chưa hỏi sếp.

## 4. Trạng Thái

- ✅ Hoàn thành: LED GPIO 11 hoạt động, lỗi giọng nói đã xử lý (đổi model Qwen).
- [ ] Tuỳ chọn: hiển thị trạng thái lên OLED (xử lý warning `label is nullptr`).
- [ ] Tuỳ chọn: cải thiện WiFi RSSI.

---

# Task 3 (24/08/2026): Điều Khiển Bánh Xe Robot (N20 + L298N)

## 1. Tổng Quan

Gắn 2 động cơ N20 làm bánh xe cho "xe robot", điều khiển qua module L298N.
Các lệnh cơ bản: tiến tới, đi lùi, xoay trái, xoay phải, dừng lại.
Điều khiển bằng giọng nói thông qua MCP tool.

## 2. Sơ Đồ Chân

| L298N | ESP32-S3 | Ghi chú |
|-------|----------|---------|
| IN1   | GPIO1    | Motor trái |
| IN2   | GPIO2    | Motor trái |
| IN3   | GPIO21   | Motor phải |
| IN4   | GPIO8    | Motor phải |
| ENA/ENB | mức cao cố định (jumper/3V3) | Tốc độ không đổi, chưa dùng PWM |

## 3. Các File Đã Thêm/Sửa

### 3.1 `main/boards/common/motor_controller.h` (MỚI)
- Class `MotorController`, header-only, theo pattern `LampController`.
- API nội bộ: `Forward()`, `Backward()`, `TurnLeft()`, `TurnRight()`, `Stop()`.
- Đăng ký **một tool MCP duy nhất**: `self.motor.move` với property `action`
  (`forward` / `backward` / `left` / `right` / `stop`).
  Lưu ý: `forward_until_stop` / `backward_until_stop` **đã tạm bỏ** khỏi tool
  (đơn giản hoá kiểm thử); muốn bật lại thì thêm lại vào `MoveAction`,
  `ApplyAction()`, `ParseAction()` và mô tả tool (hằng số `kMaxContinuousMs`
  vẫn giữ sẵn).
- Auto-stop sau **1000ms** mỗi lệnh di chuyển thông thường (esp_timer one-shot)
  để tránh xe chạy mãi khi mất kết nối. Truyền `0` vào constructor để chạy liên tục.
- ~~Lệnh chạy liên tục: `forward_until_stop`, `backward_until_stop`~~ — **tạm bỏ**.

**Cách thêm lệnh mới sau này:**
1. Thêm giá trị vào enum `MoveAction`
2. Thêm case trong `ApplyAction()` + hàm chuyển động tương ứng
3. Thêm tên action trong `ParseAction()` và cập nhật mô tả tool

### 3.2 `main/boards/bread-compact-wifi/config.h`
- Thêm `MOTOR_L_IN1` (GPIO1), `MOTOR_L_IN2` (GPIO2),
  `MOTOR_R_IN1` (GPIO21), `MOTOR_R_IN2` (GPIO8).

### 3.3 `main/boards/bread-compact-wifi/compact_wifi_board.cc`
- Khởi tạo `static MotorController motor(...)` trong `InitializeTools()`.

## 4. Chống Rơi Khỏi Mép Bàn (VL53L0X)

### 4.1 Sơ đồ chân
| VL53L0X | ESP32-S3 | Ghi chú |
|---------|----------|---------|
| SDA     | GPIO40   | I2C port 1 (trước là nút tăng âm lượng — đã bỏ) |
| SCL     | GPIO39   | (trước là nút giảm âm lượng — đã bỏ) |
| XSHUT   | GPIO10   | Reset cảm biến (KHÔNG dùng GPIO35/36/37 — trùng PSRAM octal!) |
| VIN/GND | 3V3/GND  | Địa chỉ I2C mặc định 0x29 |

### 4.2 Hoạt động chống rơi
- **Dùng component chuẩn `components/vl53l0x`** (pkolt/vl53l0x-esp-idf, copy từ
  dự án test `D:\xiaozhi\test_vl53l0x` của sếp — driver tối thiểu tự viết không
  đủ init cho bản clone nên luôn báo "Start not accepted").
- Wrapper mới `main/boards/common/tof_sensor.h` (class `TofSensor`) bọc component:
  `vl53l0x_create/init/set_profile`, đo bằng `vl53l0x_single_measure()`.
  `HasFloor()` trả về true nếu khoảng cách ≤ **30mm** (`kNoFloorMm`).
- `MotorController::SetDistanceReader(callback)`: đăng ký hàm đọc khoảng cách
  ToF (mm). Mỗi lệnh di chuyển sẽ đo 1 lần trước khi chạy (`MeasureDistanceBeforeMove()`).
- **Task `motor_floor_monitor`**: đo cảm biến **chỉ khi motor đang chạy tiến**
  (`moving_forward_ == true`, poll ~30ms). Khi robot đứng yên / lùi / xoay thì
  không đo — đáp ứng yêu cầu "không đo liên tục khi không cần".
- Với lệnh tiến (`forward`): đo trước, nếu khoảng cách
  > `kNoFloorMm = 30` (hoặc 8191 = lỗi) thì **chặn** (không chạy) và trả lỗi
  cho LLM để AI nói cảnh báo. Lùi/xoay: đo (log) nhưng vẫn chạy.
- **Trong lúc chạy tiến**, nếu `motor_floor_monitor` phát hiện mất sàn
  (khoảng cách > 30mm) giữa chừng → tự dừng ngay + lùi `kBackOffMs = 200` để
  không rơi khỏi mép bàn.

### 4.3 Lỗi đã gặp & sửa (driver tối thiểu tự viết — đã bỏ, dùng component chuẩn)
- **ToF luôn đọc 0 mm**: điều kiện chờ dữ liệu sẵn bị ĐẢO NGƯỢC — thanh ghi
  RESULT_INTERRUPT_STATUS (0x13), bits 2:0 **khác 0** = đo xong, bằng 0 = chưa.
  Code cũ chờ bits về 0 nên đọc ngay kết quả cũ (0mm) → cliff guard tưởng mất
  sàn liên tục. → Sửa thành `(status & 0x07) != 0` mới đọc kết quả.
- **Cảm biến init OK nhưng đo không hoạt động**: chuỗi "continuous mode" cũ
  (ghi 0x80=0x01, 0x00=0x00) không đúng chuẩn VL53L0X — thiếu bước đặt
  "stop variable" và không chờ dữ liệu sẵn trước khi đọc kết quả.
  → Sửa sang khởi tạo chuẩn datasheet + **single-shot**: ghi SYSRANGE_START,
  chờ bit0 của reg 0x13 về 0 (tối đa ~100ms), mới đọc RESULT_RANGE_MM (0x1E),
  rồi xóa ngắt (reg 0x0B).
- **Crash TG1WDT_SYS_RST khi boot**: XSHUT nối GPIO35 — trên ESP32-S3 có
  PSRAM octal thì GPIO35/36/37 là chân của PSRAM, dùng sẽ gây crash.
  → Chuyển XSHUT sang GPIO10.
- **"Sensor not found (id=0xEE rev=0xAA)"**: bản clone VL53L0X trả revision
  khác datasheet (0xAA thay vì 0x21) nhưng vẫn hoạt động bình thường
  → chỉ kiểm tra model ID 0xEE, bỏ kiểm tra revision.
- **Đo luôn ra 8191 mm (kErrorMm, `data.valid = false`)**: `single_measure` chạy
  OK nhưng `RangeStatus != 0` vì chưa chạy **calibration** sau `init()`.
  → Sửa trong `tof_sensor.h`: thêm chuỗi `perform_ref_spad_management` +
  `performance_ref_calibration` (VHV/phase) + `set_offset_calibration(0)` +
  `set_xtalk_compensation_enable(false)`, theo đúng example `single_ranging`
  của component. Nếu SPAD management thất bại thì dùng SPAD mặc định
  (count=3, aperture).
- **Sensơ đọc 0 mm (đặt sát bàn) nhưng báo "mất sàn"**: điều kiện cliff guard
  cũ là `mm > 0 && mm <= kNoFloorMm` → `mm = 0` (có sàn rất gần) lại trả
  `false` (coi là mất sàn). → Sửa trong `compact_wifi_board.cc`:
  `return mm <= TofSensor::kNoFloorMm;` (0 mm là hợp lệ; mất sàn thật sự khi
  đọc > 30 mm — `kNoFloorMm = 30` — hoặc 8191).
- **Robot chạy lùi mãi, kêu "dừng" không dừng**: hai lỗi logic trong
  `motor_controller.h`:
  1. `Stop()` không reset `moving_forward_` → sau lệnh dừng, cliff guard vẫn
     poll và cứ tới mep lại tự lùi 200ms liên tục → "chạy lùi mãi".
     → Sửa `Stop()` thêm `moving_forward_ = false;`.
  2. `BackwardUntilStop()` không set `moving_forward_ = false` (nếu trước đó
     đang tiến thì cờ vẫn true). → Sửa thêm.
  3. Thêm debounce `kCliffSamples = 2` (cần 2 mẫu liên tiếp mất sàn trước khi
     xác nhận cliff) để tránh trigger giả do nhiễu đọc.

- **Báo "có vật cản" và không chịu đi tới — `cliff_detected_` bị kẹt `true`**:
  `CliffGuardLoop` cũ chỉ gọi `cliff_check_()` (để reset cờ) khi `moving_forward_`
  là `true`. Khi phát hiện mép bàn, `Stop()` đặt `moving_forward_ = false`, nên
  loop không đo lại → `cliff_detected_` không bao giờ reset → mọi lệnh "đi tới"
  sau đó vĩnh viễn bị chặn (dù robot đã được nhấc lại lên bàn).
  → Sửa `CliffGuardLoop`: **luôn** poll sensor (cập nhật `cliff_detected_` theo
  trạng thái sàn hiện tại), nhưng chỉ tự dừng + lùi khi `moving_forward_` là
  `true`.
- **Chốt ngưỡng chống rơi = 30mm**: `kNoFloorMm = 30`. Sensor đọc > 30 mm (hoặc
  8191 — lỗi/không có sàn) thì coi là "mất sàn" → dừng ngay. `mm = 0` (sát bàn)
  vẫn là có sàn.
- **AI cảnh báo khi ở mép bàn**: tận dụng cơ chế sẵn có — trong
  `RegisterMcpTools()`, trước lệnh `forward` sẽ đo cảm
  biến, nếu mất sàn thì `throw runtime_error`, LLM đọc được lỗi và chủ động nói
  cảnh báo (vd "phía trước có nguy hiểm, không đi được"). Không cần gửi tín
  hiệu riêng lên server.

### 4.4 Lỗi undefined reference khi build (đã xử lý)
- Triệu chứng: `undefined reference to vl53l0x_create/init/set_profile/single_measure`.
- Nguyên nhân: dự án bật `MINIMAL_BUILD` nên component discovery không nhận
  `components/vl53l0x`, source không được biên dịch vào libmain.a.
- Cách sửa (trong `main/CMakeLists.txt`, bọc trong
  `if(CONFIG_BOARD_TYPE_BREAD_COMPACT_WIFI)`):
  ```cmake
  file(GLOB VL53L0X_SOURCES
      "${CMAKE_CURRENT_SOURCE_DIR}/../components/vl53l0x/src/*.c"
      "${CMAKE_CURRENT_SOURCE_DIR}/../components/vl53l0x/private/core/src/*.c"
      "${CMAKE_CURRENT_SOURCE_DIR}/../components/vl53l0x/private/platform/src/*.c")
  list(APPEND SOURCES ${VL53L0X_SOURCES})
  list(APPEND INCLUDE_DIRS
      "${CMAKE_CURRENT_SOURCE_DIR}/../components/vl53l0x/include"
      "${CMAKE_CURRENT_SOURCE_DIR}/../components/vl53l0x/private/core/inc"
      "${CMAKE_CURRENT_SOURCE_DIR}/../components/vl53l0x/private/platform/inc")
  ```
  và sau `idf_component_register`, thêm `target_compile_definitions(... USE_I2C_2V8=1)`.
- Kiểm chứng: nm thấy đủ symbol, `libmain.a` chứa 9 file .obj vl53.
- Lưu ý: đã bỏ 2 nút bấm tăng/giảm âm lượng (GPIO39/40 chuyển cho ToF).

## 5. Trạng Thái

> **Quy ước build (áp dụng từ Task 3):** Em KHÔNG tự chạy lệnh build nữa.
> Khi cần build, em sẽ thông báo cho sếp, sếp tự build trong terminal đã source ESP-IDF:
> `python scripts/build.py bread-compact-wifi --name bread-compact-wifi-128x64`
> (File `build_motor.bat` ở gốc repo là wrapper tạm, sếp có thể dùng hoặc xóa.)

- [x] Code điều khiển bánh xe cơ bản
- [ ] Build firmware (sếp tự build)

## 5. Vấn Đề: Không Nghe Được Khi Động Cơ Đang Chạy

### 6.1 Kiểm tra firmware — KHÔNG phải lỗi code
- MCP tool chỉ gọi `gpio_set_level()` (không block), auto-stop dùng `esp_timer`
  (task riêng), không có vòng lặp/delay chiếm CPU.
- Task audio (thu âm, wake word) chạy độc lập, vẫn hoạt động khi motor chạy.

### 6.2 Nguyên nhân thật sự: nhiễu điện từ L298N + motor N20
1. **EMI từ chổi than motor**: tia lửa điện phát nhiễu rộng dải, lọt vào mic
   INMP441 thành tiếng ồn lớn hơn giọng nói → wake word không nhận diện được.
2. **Sụt áp nguồn chung**: ESP32 và L298N dùng chung nguồn → motor kéo dòng làm
   nguồn sụt, I2S/WiFi lỗi.
3. **GND bẩn**: dòng motor qua dây GND chung tạo nhiễu cộng vào tín hiệu mic.
4. **Tiếng ồn cơ học**: mic gần motor → SNR giảm.

### 6.3 Cách khắc phục (theo thứ tự ưu tiên)
1. Tách nguồn motor/L298N (pin riêng), chỉ nối GND chung tại MỘT điểm.
2. Tụ gốm 100nF song song 2 cực mỗi motor + tụ hóa 470µF–1000µF ở nguồn L298N.
3. Dây motor đi tách biệt dây mic I2S; đặt L298N xa mic.
4. Nếu còn nhiễu nhẹ: tăng noise suppression trong cấu hình AFE (sdkconfig).

### 6.3b Phần cứng ĐÃ LẮP THỰC TẾ (cập nhật)
- Tụ 104 (100nF) song song **2 cực mỗi động cơ** N20 — chống tia lửa/nhiễu EMI
  từ chổi than ngay tại nguồn phát.
- Tụ 104 (100nF) song song **đường 3.3V nuôi micro** (gần chân micro) — lọc
  nhiễu nguồn cho mic INMP441, wake word ổn định hơn.
- Tụ hóa **2200µF/16V song song nguồn 5V chính** (đầu vào chung) — chống sụt
  áp khi motor khởi động/quay đổi chiều, tránh reset ESP32 và nhiễu audio.
- Ghi chú: mục 6.3 dòng 2 là khuyến nghị ban đầu (100nF/motor + 470µF–1000µF);
  thực tế đã lắp theo 6.3b này.

### 6.4 Cách xác nhận nhanh
- Quay tay động cơ (không cấp điện) rồi nói lệnh → nghe được bình thường =
  chắc chắn do nhiễu điện, không phải firmware.
- Cấp nguồn motor từ nguồn riêng → hết chứng tỏ do sụt áp/nhiễu nguồn.
- [ ] Test trên phần cứng (giọng nói: "đi tới", "đi lùi", "xoay trái", "xoay phải", "dừng lại")
- [ ] Sau này: thêm lệnh phức tạp (đi khoảng cách, PWM tốc độ, v.v.)

## 7. Cấu hình AI trên server (xiaozhi.me)

- **Tính cách / vai trò AI** chỉnh trực tiếp trên trang **xiaozhi.me** (phần
  quản lý thiết bị → prompt/vai trò). Firmware không giữ prompt — đổi tính cách
  không cần build lại.
- Có thể **hướng dẫn AI gọi tool theo yêu cầu** bằng system prompt, ví dụ:
  quy tắc di chuyển (chỉ tiến khi còn sàn), khi nào gọi
  `self.motor.move`, khi nào cảnh báo mép bàn.
- **Giới hạn prompt: ~2000 từ** (quan sát trên trang xiaozhi.me) — viết prompt
  ngắn gọn, ưu tiên quy tắc quan trọng.

---

# Task 4 (05/09/2026): Phát Nhạc Việt — MCP Server (YouTube → MP3 → HTTP LAN)

## 1. Bối cảnh & kiến thức

- xiaozhi.me có sẵn dịch vụ "Âm nhạc" (tool `search_music` chạy trên server của
  họ — log `<< % search_music...`): tìm được nhạc nhưng là kho nhạc Trung.
- Cơ chế nhạc xiaozhi.me: server stream Opus xuống qua kênh audio thường,
  firmware chỉ decode — **không sửa source được kho nhạc** (nằm ở server).
- Muốn nhạc Việt → tự dựng chuỗi: MCP server bên ngoài (PC) tìm + tải nhạc →
  trả URL MP3 LAN → firmware robot stream URL đó ra loa.
- Thử ZingMP3 API trước: đúng thuật toán HMAC-SHA512 (key `88265e23...`,
  secret `2aa2d1...`, sig = hmac512(path + sha256("ctime=...") hoặc +param))
  nhưng vẫn bị chặn `-403 You don't have permission` (anti-bot) → bỏ,
  chuyển sang yt-dlp/YouTube: hoạt động ổn.

## 2. Đã làm (Giai đoạn 2 — MCP server)

- Thư mục mới `tools/zing-music-mcp/`:
  - `server.py` — MCP server (FastMCP, `pip install "mcp<2"` vì mcp 2.x đổi
    API FastMCP→MCPServer) với 2 tool:
    - `search_song(keyword)`: yt-dlp `ytsearch5` `extract_flat`, trả title/
      uploader/duration/youtube_url.
    - `get_song_url(title)`: tải `ytsearch1` `bestaudio`, convert MP3 128k
      bằng ffmpeg từ `imageio-ffmpeg` (imageio-ffmpeg KHÔNG có ffprobe nên
      không dùng postprocessor FFmpegExtractAudio của yt-dlp — tự gọi ffmpeg),
      phục vụ HTTP qua `ThreadingHTTPServer` port **8619**, trả
      `http://<IP-LAN-PC>:8619/<md5(key)>.mp3`. Cache ở `music_cache/`.
  - `README.md` — hướng dẫn cài + chạy + prompt gợi ý cho xiaozhi.me, lưu ý
    firewall/cổng.
- **Đã test thật trên máy**: selftest tìm "xin hay la chia tay" → tải + convert
  OK (4:29, 128kbps, 48kHz stereo); curl tải được `http://192.168.31.187:8619/
  4518c2d231.mp3` → HTTP 200, 4.3MB, file MP3 hợp lệ.

- **SỬA LỖI quan trọng (cùng ngày)**: `mcp-proxy-server` (npm) KHÔNG hỗ trợ
  `--endpoint` (đã đọc source trên unpkg — nó chỉ là proxy stdio đọc
  config.json, không bao giờ nối vào api.xiaozhi.me). Lệnh đúng: script
  **`mcp_pipe.py`** chính thức từ repo `78/mcp-calculator` (v0.2.0):
  biến env `MCP_ENDPOINT` = URL Điểm cuối MCP, chạy
  `python mcp_pipe.py server.py` — bridge WebSocket↔stdio, tự reconnect
  (backoff tới 600s). Đã copy `mcp_pipe.py` vào `tools/zing-music-mcp/`,
  tạo `run.bat` (double-click, dán token vào biến MCP_ENDPOINT), cần
  `pip install websockets python-dotenv`.
- **Đã test bridge THẬT với token thật**: `Successfully connected to
  WebSocket server` → PingRequest đều → ListToolsRequest thành công (web
  xiaozhi.me thấy tool), 45s+ ổn định, 0 lỗi. Đã dọn process + file tạm.
- Bảo mật: token MCP đã từng bị dán vào chat → nên tạo lại Điểm cuối trên
  xiaozhi.me và dán token MỚI vào run.bat.
## 4. Fix lỗi "Mình không tìm thấy bài hát" (cùng ngày)

- Hiện tượng: robot gọi `search_song` nhưng nhận `{"results": []}` → AI trả
  lời không tìm thấy. Test lại thấy yt-dlp `ytsearch` trả `entries=None`
  **không kèm lỗi** — YouTube anti-bot chặn search của yt-dlp một cách
  chập chờn (lúc được lúc không; download watch-page vẫn hoạt động).
- Fix trong `server.py`:
  - `_search_youtube`: retry yt-dlp 2 lần (sleep 1s, 2s); nếu vẫn rỗng →
    fallback `_search_youtube_html` — scrape trực tiếp
    `youtube.com/results?search_query=...&sp=EgIQAQ...` (lọc Type=Video),
    parse JSON `ytInitialData`, lấy `videoRenderer` (title/uploader/
    lengthText→giây/videoId). Không phụ thuộc yt-dlp.
  - `_prepare_mp3`: retry download yt-dlp 2 lần (sleep 2s).
- Đã test: `"Xin hãy là chia tay"` (câu AI nghe sai dấu) và
  `"xin hay la chia tay"` đều ra kết quả qua fallback; selftest E2E
  tìm→tải 79MB→convert MP3→HTTP 200 OK.
## 5. Fix timeout get_song_url (cùng ngày, sau khi search chạy OK)

- Hiện tượng: search_song + liệt kê 5 bài OK, chọn bài xong gọi
  get_song_url nhưng xiaozhi.me MCP timeout ~30s (tải + convert lâu hơn).
- Fix trong `server.py`:
  - `get_song_url` đổi sang **tải nền bất đồng bộ** (`_job_status` +
    `_download_worker` + `_jobs` dict + lock): gọi lần 1 trả ngay
    `status=downloading`, thread sau tải + convert; gọi lại gặp
    `status=ready` kèm url (cache hit trả ngay lập tức); `status=error`
    thì cho phép thử lại. Tool không bao giờ chặn quá vài ms.
  - Giảm dung lượng: format `bestaudio[abr<=128]` (79MB → 2.6MB nguồn,
    MP3 ra 5.1MB) + `match_filter` bỏ bài >15 phút, ytsearch3 để có bài
    dự phòng khi bài đầu bị lọc.
- Đã test E2E: 10 lần poll `downloading` → `ready` với đúng bài
## 6. Tái kiến trúc Cloud Stream Proxy (cùng ngày, theo yêu cầu sếp)

- Sếp chốt: bỏ local HTTP file server + cache đĩa; ESP32 chỉ nhận 1 URL
  stream duy nhất; server deploy được Render/HF. (Direct URL googlevideo
  bị khóa IP + hết hạn → bắt buộc stream qua server — đã phản biện và
  sếp đồng ý mô hình Cloud Stream Proxy.)
- Viết lại `server.py`:
  - BỎ: HTTP file server cũ, `music_cache/`, tải đĩa + retry, job nền
    `_jobs` (đã xóa luôn thư mục music_cache).
  - `get_song_url(title)`: trả NGAY `{"status":"ready","stream_url":
    "<PUBLIC_BASE>/stream/<id>.mp3"}` (id = md5(title)) — hết cảnh poll.
  - FastAPI `GET /stream/{id}.mp3`: khi client kết nối mới resolve direct
    URL (yt-dlp, cache RAM `_resolved` TTL 30 phút) rồi spawn
    `ffmpeg -i <direct> -b:a 128k -f mp3 pipe:1`, `StreamingResponse`
    pipe thẳng ra HTTP — **0 file đĩa**. `/health` cho health check.
  - Env: `PORT` (Render tự set), `PUBLIC_BASE` (bỏ trống → auto-LAN dev).
- Đóng gói: `requirements.txt`, `Dockerfile` (python:3.12-slim + ffmpeg apt),
  `render.yaml` (web Docker, env MCP_ENDPOINT/PUBLIC_BASE secret).
- README viết lại toàn bộ (kiến trúc, deploy Render, prompt mới bỏ poll).

## 7. Giai đoạn 3 — Firmware MusicPlayer (06/09/2026)

- File mới: `main/audio/music_player.h/.cc` — class `MusicPlayer` (singleton):
  - `Play(url)`: tạo FreeRTOS task "music" (8KB stack, prio 4).
  - Task: `esp_http_client` stream URL (HTTP/HTTPS qua `esp_crt_bundle_attach`)
    → `esp_audio_simple_dec` decode MP3 on-the-fly (`CONFIG_AUDIO_DECODER_MP3_SUPPORT=y`
    có sẵn trong sdkconfig) → downmix stereo→mono nếu cần → resample về
    output rate codec bằng `esp_ae_rate_cvt` (RATE_CVT_CFG) → push chunk
    ~2048 samples vào playback queue.
  - `Stop()`: đặt cờ, chờ task exit (tối đa 5s) rồi `ResetDecoder()` flush
    queue → loa im ngay, buffer được free trong task exit path.
  - Cleanup đầy đủ ở cuối task: close resampler/decoder/client + unregister
    default decoder. Không cấp phát lặp lại trong loop ngoài vector tái sử dụng.
- `AudioService`: thêm public `GetOutputSampleRate()` (đọc từ codec, bread
  = 24000) và `PushPcmToPlaybackQueue(vector<int16_t>&&)` — push PCM thẳng
  vào `audio_playback_queue_` (chờ tối đa 2s nếu queue >= 8 chunks, sau đó
  drop chunk), AudioOutputTask ghi ra loa qua đường đi chuẩn (tự bật
  EnableOutput + power timer).
- Board `compact_wifi_board.cc`: đăng ký 3 tool MCP trong `InitializeTools()`:
  `self.music.play(url, name)`, `self.music.stop`, `self.music.get_state`.
- Server: ffmpeg thêm `-ac 1` (mono) — giảm 50% băng thông, firmware vẫn
  resample rate nếu lệch.
- Đã format bằng esp-clang clang-format theo .clang-format repo.
- ⚠️ Chưa build/nạp (em không tự chạy build — sếp build bằng
  `python scripts/build.py bread-compact-wifi --name bread-compact-wifi-128x64`
  rồi `idf.py -p COM8 flash monitor`). Cần test: phát nhạc, dừng nhạc giữa
  chừng, wake word khi đang phát, TTS xen kẽ, mất mạng giữa chừng, RAM
  (free_heap) trước/sau nhiều lần play/stop.
- Lưu ý: robot phải nối WiFi 2.4GHz; server nhạc phải đang chạy (run.bat)
  và PC phải cùng mạng LAN với robot (hoặc dùng URL public khi đã deploy).

- **Đã test laptop**: cài fastapi+uvicorn; `/health` → ok; curl
  `/stream/b866437922.mp3` (key=md5 "mua he 26 den vau") → 4.8MB, ffmpeg
  verify: Duration 5:00.53, 128kb/s, 48kHz stereo — stream on-the-fly OK,
  log `resolved 'Đen – Mùa hè 26 ft. Pink Frog (M/V)'`.
- Lưu ý vận hành: chạy `server.py` trực tiếp (không qua mcp_pipe) sẽ thoát
  ngay do MCP stdio gặp EOF stdin — luôn chạy qua `mcp_pipe.py`/run.bat/
  systemd. Render free ngủ ~15 phút; lần stream đầu sau ngủ chậm hơn.

  "NGÀY MÌNH CHIA TAY - PHAN DUY ANH" (320s), url
  `http://192.168.31.187:8619/b1d4863ef3.mp3` (cache 5.1MB). Tổng ~90s
  cho bài mới; bài đã có trong cache trả ngay.
- README đã cập nhật prompt cho xiaozhi.me (hướng dẫn AI poll lại).
- ⚠️ Cần khởi động lại run.bat để nạp code; cập nhật prompt trên web.

- ⚠️ Sếp phải **khởi động lại run.bat** để server.py nạp code mới.
- Hạn chế còn lại: nếu YouTube chặn cả download của yt-dlp thì
  get_song_url sẽ lỗi (hiếm) — hướng xử lý sau: cookies trình duyệt
  (`--cookies-from-browser`) hoặc dùng videoId từ search fallback.


## 8. Sửa decoder MP3 (06–07/09/2026) — QUAN TRỌNG

### 8.1 Lỗi build: thiếu component `esp_http_client`
- `main/CMakeLists.txt` thiếu `esp_http_client` trong `PRIV_REQUIRES`
  (music_player.cc là file đầu tiên trong `main` include `<esp_http_client.h>`).
- Sửa: thêm `esp_http_client` vào `PRIV_REQUIRES` (sau `mbedtls`).

### 8.2 Lỗi: `Decoder MP3 ... not registered` / `Fail to open decoder MP3 ret -7`
- Nguyên nhân gốc: firmware dùng **`esp_audio_simple_dec`** — API "simple decoder"
  này CHỈ hỗ trợ container **WAV / M4A(MP4) / TS / OGG**, **KHÔNG có MP3**
  (xem `managed_components/espressif__esp_audio_codec/Kconfig`).
- MP3 nằm ở nhóm **`esp_audio_dec`** (decoder "full"), đã bật sẵn qua
  `CONFIG_AUDIO_DECODER_MP3_SUPPORT=y` trong sdkconfig.
- Sửa `music_player.cc` (không cần sửa sdkconfig/Kconfig):

  | Cũ (sai) | Mới (đúng) |
  |---|---|
  | `#include "esp_audio_simple_dec.h"` (+`_default`/`_reg`) | `#include "esp_audio_dec.h"` + `"esp_audio_dec_default.h"` |
  | `esp_audio_simple_dec_handle_t` | `esp_audio_dec_handle_t` |
  | `esp_audio_simple_dec_register_default()` | `esp_audio_dec_register_default()` |
  | `dec_cfg.dec_type = ESP_AUDIO_SIMPLE_DEC_TYPE_MP3` | `dec_cfg.type = ESP_AUDIO_TYPE_MP3` |
  | `esp_audio_simple_dec_open/process/get_info/close` | `esp_audio_dec_open/process/get_info/close` |
  | `esp_audio_simple_dec_raw_t` (có `.eos`) | `esp_audio_dec_in_raw_t` (có `.consumed`, `.frame_recover`) |
  | `esp_audio_simple_dec_out_t` | `esp_audio_dec_out_frame_t` |
  | `esp_audio_simple_dec_info_t` | `esp_audio_dec_info_t` |
  | `esp_http_client_is_complete_data_called` | `esp_http_client_is_complete_data_received` |

### 8.3 ⚠️ Sự cố Kconfig.projbuild — bài học
- Em từng thêm nhầm một `menu "MUSIC_PLAYER_CONFIG"` vào `main/Kconfig.projbuild`
  (không cần thiết) rồi dùng `Set-Content -Encoding UTF8` của PowerShell để sửa.
- Hậu quả: (a) PowerShell **ghi BOM** `\ufeff` vào đầu file → `kconfiglib` báo
  `couldn't parse '\ufeffmenu "Xiaozhi Assistant"'`; (b) xóa menu thừa làm mất
  luôn 2 `endmenu` cuối → `expected endmenu at end`.
- Khắc phục: `git checkout -- main/Kconfig.projbuild` (khôi phục nguyên bản).
- **QUY TẮC TỪ NAY:** không dùng `Set-Content`/`Out-File` của PowerShell cho file
  chứa ký tự non-ASCII (Kconfig, tài liệu tiếng Việt/Trung). Chỉ sửa bằng tool
  editor hoặc Python với `encoding='utf-8'`.

## 9. Chống xen tiếng TTS khi phát nhạc (08/09/2026)

- Hiện tượng: khi AI gọi `self.music.play`, câu xác nhận TTS
  ("Đang phát bài ... cho bạn rồi") vẫn stream song song → lẫn vào nhạc.
- Sửa:
  - `audio_service.h/.cc`: thêm `std::atomic<bool> music_playing_` +
    public `void SetMusicPlaying(bool)`.
  - `OpusCodecTask`: khi push PCM TTS vào playback queue, thêm điều kiện
    `&& !music_playing_.load()` → TTS downlink bị MUTE trong lúc nhạc phát.
    Nhạc vẫn ra loa vì đi đường `PushPcmToPlaybackQueue` riêng.
  - `music_player.cc`: `audio.SetMusicPlaying(true)` sau khi HTTP status 200;
    `audio.SetMusicPlaying(false)` ở cleanup (mọi đường thoát đều tới cleanup
    do dùng `do { } while(false)`).

## 10. Ổn định stream + fix "đứt quãng" (08–22/09/2026)

- Pre-buffer ~1 giây (`kPreBufferBytes`) trước khi bắt đầu decode/phát;
  `kReadBufferBytes` 8KB → 24KB; stack task 8KB → 12KB.
- Giới hạn log `Decode error` (chỉ in vài lần đầu) tránh spam console.
- **Fix quan trọng nhất — tách mã lỗi trong vòng decode:**

  | Mã | Ý nghĩa | Xử lý đúng |
  |---|---|---|
  | `ESP_AUDIO_ERR_OK` | frame OK | `src += consumed` |
  | `ESP_AUDIO_ERR_BUFF_NOT_ENOUGH` (-8) | output buffer nhỏ | `resize` theo `needed_size`, **retry cùng input** (`continue`) |
  | `ESP_AUDIO_ERR_DATA_LACK` (-3) | chưa đủ 1 frame (cuối buffer) | **`break`** giữ leftover, đọc thêm HTTP — **KHÔNG skip byte** |
  | `ESP_AUDIO_ERR_FAIL` (-1) / `HEADER_PARSE` (-4) | frame hỏng | skip 1 byte để dò lại frame header |

  - Lỗi cũ: xử lý MỌI lỗi giống nhau bằng "skip 1 byte". Khi gặp `DATA_LACK`
    (bình thường ở cuối mỗi buffer) việc skip byte làm con trỏ lệch khỏi ranh
    giới frame MP3 → mất sync → `ESP_MP3_DEC: Failed to decode mp3 frame,
    error:12` spam liên tục + cascade `Decode error -1` → **nhạc giật liên tục,
    không ra tiếng**. Đã sửa theo đúng reference `audio_decoder_test.c` của
    Espressif (chỉ `continue` khi BUFF_NOT_ENOUGH, break khi cần thêm dữ liệu).
  - Thêm chống deadlock: nếu buffer đầy mà vẫn không tiêu thụ → resync 1 byte.

## 11. Sự cố MẠNG thường gặp (không phải lỗi code)

| Triệu chứng log | Nguyên nhân | Cách xử lý |
|---|---|---|
| `esp-tls: select() timeout` / `transport_base: Failed to open a new connection: 32774` / `MusicPlayer: Failed to open connection` | Laptop ở **5GHz** (`Xiaomi_58C8_5G`) còn robot ở **2.4GHz** (`Xiaomi_58C8`) — router chập chờn khi truyền TCP giữa 2 băng (ping được nhưng TCP handshake timeout) | Đưa **laptop về 2.4GHz** cùng SSID robot; hoặc tắt Band Steering / AP Isolation trên router |
| `MQTT: Received audio packet with wrong sequence` | WiFi yếu (RSSI -83..-97 dBm) mất gói UDP | Đưa robot gần router; giảm thiết bị 2.4GHz xung quanh |
| Server không truy cập được từ robot (dù laptop OK) | Windows Firewall chặn inbound | Tắt Private profile hoặc tạo rule "Music Server" TCP 8619 Inbound Allow |
| Chỉ `LISTENING` mà robot timeout | Band isolation / AP isolation | Kiểm tra `netstat -an | findstr 8619`, `Test-NetConnection -Port 8619`, `ping <IP robot>` |

- Kiểm tra nhanh trên laptop:
  `netstat -an | findstr 8619` (phải thấy `0.0.0.0:8619 LISTENING`),
  `curl.exe --noproxy "*" http://127.0.0.1:8619/health` → `ok`,
  `netsh wlan show interfaces` (xem Band của laptop).

## 13. Tối ưu theo gợi ý Gemini (22/09/2026)

Sếp đưa gợi ý từ Gemini; em đánh giá và chỉ áp dụng phần đúng:

| Gợi ý | Kết luận | Ghi chú |
|---|---|---|
| `asyncio.create_task` pre-resolve trong `get_song_url` | ❌ Sai kiến trúc | MCP tool chạy ở main thread (stdio), FastAPI ở thread riêng → không có event loop; yt-dlp là blocking. **Đã làm lại bằng `threading.Thread`** (`_preresolve_bg`) |
| ffmpeg `-ar 24000 -ac 1` | ✅ Áp dụng | Codec board xuất 24000 Hz mono → firmware **bỏ được resample** |
| `-fflags nobuffer -bufsize 32k` | ⏭️ Bỏ qua | `nobuffer` áp cho input demuxer, có thể gây hại |
| `timeout_ms = 15000` | ✅ Áp dụng | `music_player.cc:103` |
| `SetMusicPlaying(true)` sớm | ✅ Áp dụng | Chuyển lên đầu task (trước handshake) |
| Pre-buffer 16KB | ✅ Áp dụng | Đã bị mất ở lần rewrite trước; thêm lại |
| Null-check `SetStatus` | ✅ Đã có sẵn | Chỉ hạ `ESP_LOGW` → `ESP_LOGD` cho 2 log label-null |

**Bổ sung của em (Gemini thiếu):** **retry kết nối 3 lần** (cách 700ms) trong
`StreamTask` — đây mới là fix trực tiếp cho lỗi "văng socket" mà sếp gặp
(`Failed to open connection` → `Music task exit` ngay).

### File đã sửa
- `main/audio/music_player.cc`: timeout 15s, retry 3×, pre-buffer 16KB,
  `SetMusicPlaying(true)` ở đầu task.
- `tools/zing-music-mcp/server.py`: ffmpeg `-ar 24000`, `_preresolve_bg()`
  (threading) + gọi trong `get_song_url`.
- `main/display/lvgl_display/lvgl_display.cc`: 2 log label-null → `ESP_LOGD`.

### ⚠️ Lưu ý quan trọng
- **Vẫn phải đổi laptop sang WiFi 2.4GHz** (cùng băng robot). Lỗi hiện tại
  đo được: laptop 5GHz ↔ robot 2.4GHz → ping **50% loss, 650ms** → không code
  nào chữa được, phải đổi băng.
- Cần **restart run.bat** để server nạp `-ar 24000` + pre-resolve.

## 14. Sửa giật tiếng vòng 2 — WiFi buffers + jitter buffer (22/09/2026)

### 15.1 Chẩn đoán
- Log sau bản trước: decode OK (24000 Hz 1ch, không error), kết nối OK,
  pre-buffer 24576 bytes OK — nhưng vẫn giật.
- Soi chuỗi audio: `NoAudioCodec::Write` dùng `i2s_channel_write(portMAX_DELAY)`
  (blocking, pacing I2S đúng) → đường loa chuẩn. Giật = **queue cạn (underrun)**.
- **Đo tốc độ server** (curl 20s): **240 KB/s ≈ 1.9 Mbps** — nhanh hơn realtime
  (16KB/s @128k) **15 lần** → server/YouTube KHÔNG throttle. Bottleneck ở ESP32.

### 15.2 Nguyên nhân chính: WiFi RX buffer quá thấp (gợi ý Gemini #2 — đúng)
- `sdkconfig.defaults.esp32s3`: `STATIC_RX=3`, `DYNAMIC_RX=6`, `RX_BA_WIN=3`
  — cực thấp (IDF mặc định 10/32/6). Khi nhạc burst dữ liệu → tràn RX buffer
  → mất gói → retransmit → jitter. Log boot xác nhận: `static rx buffer num: 3`,
  `dynamic rx buffer num: 6`, `rx ba win: 3`.
- **Sửa** (file nguồn, build áp dụng khi regenerate sdkconfig):
  `STATIC_RX=10`, `DYNAMIC_RX=24`, `RX_BA_WIN=6`.
  Chi phí RAM nội bộ ~11KB static + tối đa ~29KB dynamic (PSRAM 8MB dư).
- ⚠️ Power save (gợi ý Gemini #1): **đã có sẵn** — `WifiStation::SetPowerSaveLevel`
  (`78__esp-wifi-connect/wifi_station.cc:309`) set `WIFI_PS_NONE` khi
  connecting/listening. Không cần sửa.
- ⚠️ Tắt thu âm khi phát nhạc (gợi ý Gemini #3): **từ chối** — sẽ mất lệnh
  "dừng nhạc" bằng giọng; upstream chỉ ~30kbps, không phải thủ phạm.

### 15.3 Firmware: tăng jitter buffer
- `kReadBufferBytes` 24KB → **64KB** (PSRAM, vì > `SPIRAM_MALLOC_ALWAYSINTERNAL=2048`).
- `kPreBufferBytes` 16KB → **48KB** (~4 giây @96kbps).
- `PushPcmToPlaybackQueue(pcm, max_queue_depth=8)` — thêm param; music gọi với
  **20 chunks (~1.7s)**. TTS giữ mặc định 8 (latency không đổi).
  An toàn: `ResetDecoder()` đã clear `audio_playback_queue_` khi Stop().

### 15.4 Server: bitrate 128k → **96k**
- Giảm 25% băng thông cần thiết trên 2.4GHz; server dư băng thông (đo 1.9Mbps).
- Tổng jitter tolerance sau sửa: queue 1.7s + inbuf ~4s ≈ **5.7 giây**.

### 14.5 Việc còn lại
- [ ] Sếp build + flash; restart `run.bat`; **đưa robot lại gần router**
      (RSSI boot -87..-95 dBm — vẫn yếu).
- [ ] Nếu vẫn giật: đo lại speedDownload qua WiFi thật (curl từ laptop qua IP
      LAN đã qua router), cân nhắc 64k.

### 14.6 Vòng 3: vẫn giật sau khi đủ buffer → thêm chẩn đoán (22/09/2026)
- Log sau bản trước: pre-buffer đủ 65536 B, decode 24000Hz 1ch sạch, kết nối OK,
  RSSI tốt (robot gần router), WiFi buffer đã tăng — **mà vẫn giật**.
- Đã loại: decode lỗi, kết nối fail, sai sample rate, server throttle (đo
  localhost 240KB/s = 15× realtime), thiếu buffer (5.4s + 1.7s), power save.
- Nghi vấn còn lại: (a) decode FAIL skip byte âm thầm (log bị cap 5 lần nên
  không thấy); (b) queue loa bị cạn (underrun) do music task không kịp feed;
  (c) nhiễu 2.4GHz thật sự.
- **Đã thêm chẩn đoán:**
  1. `AudioOutputTask`: đếm **underrun** — khi task thức dậy mà queue rỗng
     (DMA cạn → loa im) → log `Speaker queue underrun #N (starved of PCM)`.
  2. `music_player.cc`: đếm **TỔNG** decode error (log throttle 1 giây/lần,
     không còn cap 5) + log cuối: `Stream finished, ... , N decode errors`.
  3. Stats 5 giây/lần trong music task: `stats: buffered X B, errs N, RSSI Y dBm`
     (qua `esp_wifi_sta_get_ap_info`).
- **Cách đọc kết quả test:**
  - Nhiều `underrun #N` → music task không kịp feed → xem `stats: buffered`
    (nếu về 0 = mạng chậm; nếu vẫn cao = CPU/thứ tự task).
  - `errs` tăng nhanh (hàng trăm) → stream MP3 hỏng → xem lại server ffmpeg.
  - `RSSI` < -70 → vẫn là sóng yếu, phải cải thiện vị trí robot.

## 15. Trạng thái & Việc còn lại (cập nhật 22/09/2026)

### 14.6 Thành tựu từ Gemini (tu dong hoan thanh music streaming) — 22/09/2026 chieu

Sau khi em fix percent-ll-d → percent-lu, them retry/reconnect, sua LVGL warning, them -re cho ffmpeg — **Gemini da tu dong hoan thanh he thong nhac streaming end-to-end**. Ket qua: nhac da phan thanh cong tu YouTube qua server Cloud Stream Proxy.

#### 1. AudioService — them API cho music (tieng Viet duoc encode UTF-8 trong file)

- GetOutputSampleRate(): tra sample rate cua codec (24000 neu khong biet), dung boi MusicPlayer de resample.
- SetMusicPlaying(bool): mute TTS khi nhac phat, tranh xen tieng robot.
- PushPcmToPlaybackQueue(pcm, max_queue_depth): push PCM truc tiep vao playback queue voi jitter buffer depth (music dung 20, TTS dung 8).
- Refactor code style (format define, constructor, lambda).

#### 2. CompactWifiBoard — them 3 MCP tools cho music

- self.music.play(url, name): phat nhac tu stream URL.
- self.music.stop(): dung nhac dang phat.
- self.music.get_state(): tra JSON {playing: true/false}.
- Them includes: music_player.h, assets/lang_config.h, led/single_led.h.

#### 3. Server nhac Cloud Stream Proxy (tools/zing-music-mcp/)

- Files moi: server.py (FastAPI + ffmpeg pipe), mcp_pipe.py (MCP stdio bridge), requirements.txt, run.bat, Dockerfile, render.yaml, README.md.
- Tinh nang:
  - search_song(keyword): tim bai trÃªn YouTube qua yt-dlp, tra 5 ket qua.
  - get_song_url(title): tra NGAY status ready + stream_url, khong can poll.
  - /stream/<id>.mp3: StreamingResponse + ffmpeg pipe (-re -b:a 128k), 0 file tam.
- Deploy: Render.com (docker) hoac HuggingFace Spaces.
- Yeu cau: laptop phai bat, WiFi 2.4GHz cung bang voi robot.

#### 4. MusicPlayer (main/audio/music_player.cc + .h) — FILE MOI

- File moi duoc tao, chua tat nguon commit.
- Class MusicPlayer voi Play(url), Stop(), IsPlaying().
- Stream MP3 qua HTTP(S) → decode bang esp_audio_dec (KHOng phi esp_audio_simple_dec) → resample ve 24kHz mono → loa qua AudioService::PushPcmToPlaybackQueue.
- Luu y: day la file em tao ra (khong phai Gemini), nhung Gemini da viet AudioService API tuong thich.

#### 5. Luu y quan trong

- Firmware phai build + flash ban co 3 tool self.music.* moi.
- Server nhac phai dang chay (run.bat hoac deploy Render).
- Tren web xiaozhi.me se thay 5 tool: search_song, get_song_url (tu server) + self.music.play/stop/get_state (tu firmware).
- Neu web chi hien 2 tool: reset MCP endpoint + flash firmware moi nhat.

### 14.7 Những gì đã sửa hôm nay (22/09/2026 chiều)

#### 1. Fix lỗi format log %lld → %lu (music_player.cc)
- **Vấn đề**: ESP-IDF không hỗ trợ %lld (long long), khiến log 'Stream finished, ~%lld k samples written, %d decode errors' bị lệch argument → log ra số giả 1070485864 (không phải số decode error thật).
- **Nguyên nhân root cause**: Con số 1070485864 thực ra là lỗi mạng (TCP abort / socket not connected), không phải decode MP3 hỏng. Firmware đang đếm decode errors kể cả khi gặp lỗi transport, không phải lỗi decode MP3.
- **Sửa**: Đổi %lld → %lu, argument (long long)(pcm_written / 1000) → (unsigned long)(pcm_written / 1000).

#### 2. Thêm tự động reconnect khi stream nhạc bị đứt giữa chừng (music_player.cc)
- **Vấn đề**: Khi TCP connection bị đứt giữa chừng (software caused connection abort / socket not connected), music task exit ngay, không retry → nhạc dừng giữa bài.
- **Root cause**: LogE cho thấy cả HTTP lẫn MQTT đều bị đứt → sự cố mạng WiFi/router, không phải code decode.
- **Sửa**: 
  - Chuyển do { ... } while (false); thành or (int stream_attempt = 1; stream_attempt <= 3; ++stream_attempt) loop.
  - Reset state (inbuf, src_rate, src_channels, buffered, eos, pending) mỗi lần reconnect.
  - Khi HTTP read error / decoder failed → set will_retry = true, cleanup client, continue (reconnect).
  - Chỉ log 'Stream finished' khi stream thành công (!will_retry).
  - Sau 3 lần thất bại mới exit (tránh infinite loop khi server thật sự chết).
- **Tại sao reconnect hữu ích**: Server/laptop-side có thể bị kill khi idle 15 phút (Render free tier) hoặc router/NAT timeout → reconnect tự động phục hồi mà không cần user intervention.

#### 3. Sửa LVGL warning (lvgl_display.cc)
- **Vấn đề**: Có 6 calls lv_label_set_text không có null check rõ ràng → có thể crash nếu label chưa được tạo.
- **Sửa**: Đảm bảo tất cả calls đều có null check trước đó:
  - status_label_: early return if nullptr (trong SetStatus).
  - 
otification_label_: early return if nullptr (trong ShowNotification).
  - mute_label_: inline if (mute_label_ != nullptr) trước khi gọi.
  - attery_label_: condition check (battery_label_ != nullptr && ...) trước khi gọi.
  - 
etwork_label_: condition check (network_label_ != nullptr && ...) trước khi gọi.
- **Kết quả**: Không còn redundant checks, đảm bảo safety.

- [x] **Gemini hoàn thành tự động hệ thống nhạc streaming end-to-end** (22/09/2026 chiều):
  - AudioService thêm API: GetOutputSampleRate(), SetMusicPlaying(bool), PushPcmToPlaybackQueue(pcm, max_queue_depth).
  - CompactWifiBoard thêm 3 MCP tools: self.music.play/stop/get_state.
  - Server nhạc Cloud Stream Proxy (tools/zing-music-mcp/): search_song, get_song_url, /stream/{id}.mp3 ffmpeg pipe.
  - MusicPlayer (file mới): stream MP3 qua HTTP -> decode esp_audio_dec -> PCM vào AudioService.
  - Kết quả: nhạc phát thành công từ YouTube.

### ĐÃ XONG
- [x] Server nhạc Cloud Stream Proxy (RAM-only, ffmpeg pipe, deploy-ready).
- [x] Cầu nối Điểm cuối MCP (`mcp_pipe.py` + `run.bat`) — đã "Đã kết nối".
- [x] Firmware `MusicPlayer` + 3 tool `self.music.play/stop/get_state`.
- [x] Decoder MP3 chạy (`esp_audio_dec`), nhạc đã phát ra loa.
- [x] Mute TTS khi nhạc phát.
- [x] Fix vòng decode (DATA_LACK vs FAIL).
- [x] Server xuất 24000 Hz mono (khớp codec) + pre-resolve nền.
- [x] Retry kết nối 3 lần, timeout 15s, pre-buffer 48KB, jitter buffer 1.7s.
- [x] Server bitrate 96k.
- [x] WiFi RX buffers 3/6/3 → 10/24/6 (fix giật chính).
- [x] **Fix lỗi format log `%lld` → `%lu`** trong `music_player.cc` (ESP-IDF không hỗ trợ `%lld`, gây log ra số giả 1070485864 decode errors — thực chất là lỗi mạng chứ không phải decode hỏng).
- [x] **Thêm khả năng tự động reconnect khi stream nhạc bị đứt giữa chừng** (`music_player.cc`): vòng `for (int stream_attempt = 1; stream_attempt <= 3; ++stream_attempt)` retry khi HTTP read error / TCP abort / decoder failed; reset state (inbuf, src_rate, src_channels, buffered, eos, pending) mỗi lần reconnect; cleanup client trước khi continue; chỉ log "Stream finished" khi stream thành công.
- [x] **Sửa LVGL warning**: đảm bảo tất cả 6 calls `lv_label_set_text` trong `lvgl_display.cc` đều có null check trước đó (early return hoặc inline if) — tránh undefined behavior khi label chưa được tạo.
- [x] **Sửa lỗi giật tiếng triệt để & lỗi đứt stream (22/09/2026)**:
  - **Nguyên nhân gốc 1 (server.py dính `-re`)**: Cờ `-re` ép ffmpeg chạy đúng 1.0x (12 KB/s). ESP32 phát cũng 1.0x (12 KB/s). Khi buffer ban đầu cạn thì buffer bị ghim ở mức 116 byte (stats báo `buffered 116 B`), không thể bù đắp độ trễ WiFi → thiếu frame MP3 liên tục (`DATA_LACK`) làm queue rỗng → nhạc giật giật. Đồng thời, YouTube CDN phát hiện stream bị đọc nhỏ giọt 12 KB/s nên tự động ngắt kết nối sau ~40-90s gây lỗi `Software caused connection abort`.
  - **Nguyên nhân gốc 2 (firmware decode cạn buffer)**: Vòng lặp decode cũ giải mã hết toàn bộ `inbuf` về 0 rồi mới đọc HTTP một lần (chỉ lấy được 4KB rồi lại giải mã về 0). Khiến `inbuf` không bao giờ tích lũy được bộ đệm trong lúc phát.
  - **Đã sửa trong `server.py`**: Bỏ hoàn toàn cờ `-re` để ffmpeg burst tối đa (>900 KB/s) nạp đầy buffer; thêm các cờ tự động kết nối lại cho ffmpeg: `-reconnect 1 -reconnect_at_eof 1 -reconnect_streamed 1 -reconnect_delay_max 5` chống rớt mạng từ YouTube CDN; chuyển stderr ffmpeg ra `sys.stderr` để theo dõi.
  - **Đã sửa trong `music_player.cc`**: Tăng `http_cfg.buffer_size` lên 8192; đan xen giải mã và đọc HTTP (mỗi chu kỳ giải mã đủ 1 chunk PCM 2048 samples đưa vào queue loa thì tạm dừng giải mã để quay lại đọc HTTP) → `inbuf` luôn được giữ đầy 50–64 KB (~5 giây nhạc) và queue loa luôn đầy 20 chunks (~1.7 giây). Tổng buffer ổn định liên tục ~6.7 giây chống giật tuyệt đối!

### CÒN LẠI
- [ ] **Restart `run.bat`** trên PC (để nạp `server.py` mới đã bỏ `-re` và thêm reconnect).
- [ ] Sếp build lại firmware và flash vào robot (`python scripts/build.py bread-compact-wifi --name bread-compact-wifi-128x64` rồi nạp).
- [ ] Test lại: Ra lệnh "Phát bài Mùa hè 26" và quan sát log:
  - Log `stats: buffered` sẽ duy trì ở mức cao (~50.000 - 64.000 B thay vì 116 B).
  - Không còn hiện tượng giật giật hay rớt socket `Software caused connection abort`.

### 15. Nâng chất lượng âm thanh nhạc (23/09/2026)

**Vấn đề**: Nhạc phát ra loa nghe rè/méo, kém rõ rệt so với bài hát gốc (trong khi giọng TTS nghe bình thường).

**Nguyên nhân & cách sửa — server `tools/zing-music-mcp/server.py`:**

1. **Bitrate MP3 quá thấp**: 96 kbps ở 24 kHz mono = 4 bit/mẫu (MP3 ở 24 kHz là MPEG-2 LSF, codec phải làm việc ở chế độ "tiết kiệm" → artifact rè/swishy khi nhạc nhiều nhạc cụ). → **128 kbps** (5.33 bit/mẫu). Băng thông chỉ tăng 12 → 16 KB/s.
2. **Chất lượng nguồn (generation loss)**: yt-dlp trước đây lọc `bestaudio[abr<=128]` nên lấy bản 128k của YouTube (thường là Opus/AAC 128k) rồi encode lại sang MP3 → mất chất lượng 2 lần. → `bestaudio[abr<=192]/bestaudio[abr<=128]/bestaudio/best` để ưu tiên bản nguồn tốt hơn, giảm nhiễu lượng tử khi encode lại.
3. **Sub-bass làm méo loa nhỏ**: loa robot không tái tạo được dưới 80 Hz; năng lượng bass sâu chỉ làm màng loa rung mạnh → méo cả dải giữa (nghe "rè" rõ nhất ở đoạn có trống/bass). → `highpass=f=80`.
4. **Clip sau giải mã**: PCM đỉnh 0 dBFS khi nhân với software volume trong `NoAudioCodec::Write` (`pow(volume/100, 2)`) và amp I2S dễ vượt biên → clip cứng nghe như rè. → `alimiter=limit=0.891` giữ đỉnh -1 dBFS.
5. **Sample rate mismatch**: server fix cứng `-ar 24000` nên board nào có `AUDIO_OUTPUT_SAMPLE_RATE` khác (vd 16 kHz) sẽ bị ESP32 resample bằng `esp_ae_rate_cvt` (complexity 2, perf_type SPEED) — chất lượng thấp hơn resample ở server. → Sample rate / channels / bitrate / filter giờ là **biến môi trường**: `AUDIO_SAMPLE_RATE`, `AUDIO_CHANNELS`, `AUDIO_BITRATE`, `AUDIO_FILTERS` (mặc định đã khớp `bread-compact-wifi`: 24000 Hz, mono).
6. **Log cấu hình**: khi khởi động server in `[music] audio: 24000 Hz x1, 128k mp3, filters='...'` để biết ngay cấu hình đang chạy.

**Firmware `main/audio/music_player.cc`**: pre-buffer cứng 48 KB → `kReadBufferBytes` (64 KB ≈ 4 s @128 kbps) để giữ nguyên cửa sổ chống giật khi băng thông tăng 33%.

**Kiểm chứng đã chạy**:
- Bơm thật qua endpoint `/stream/{id}.mp3` (uvicorn + ffmpeg): file nhận được là `24000 Hz, mono, 128 kb/s` MP3.
- `ffmpeg volumedetect`: `highpass=f=80` giảm tone 40 Hz **12.3 dB** (bỏ sub-bass loa không phát được) nhưng giữ nguyên tone 440 Hz; `alimiter` giữ đỉnh đúng **-1.0 dBFS** kể cả khi boost +24 dB, còn không limiter thì chạm 0.0 dBFS (clip).
- Test env override: `AUDIO_SAMPLE_RATE=16000 AUDIO_CHANNELS=2 AUDIO_BITRATE=64k AUDIO_FILTERS=""` → lệnh ffmpeg đổi đúng (`-ar 16000 -ac 2 -b:a 64k`, không còn `-af`).

**Cần làm**: restart `run.bat` (hoặc redeploy Render) để nạp `server.py` mới; muốn sạch hơn nữa đặt `AUDIO_BITRATE=160k`, muốn nhiều bass hơn đặt `AUDIO_FILTERS=highpass=f=50,alimiter=limit=0.891:level=disabled` hoặc `AUDIO_FILTERS=""`.


