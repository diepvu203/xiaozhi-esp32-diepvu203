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

- **Lắc lư phản hồi wake word**: khi được hô wake word, robot lắc trái/phải
  ~2 nhịp (~0.6s) như phản hồi thân thiện. Cơ chế: hook
  `Board::OnWakeWordDetected()` (virtual no-op trong `board.h`, được gọi từ
  `Application::HandleWakeWordDetectedEvent()`), board override → `MotorController::Wiggle()`
  chạy trong task one-shot riêng (không block main task, không cản audio),
  giữ `moving_forward_ = false` nên không đụng chống rơi/auto-stop.

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
  `HasFloor()` trả về true nếu khoảng cách ≤ **70mm** (`kNoFloorMm`).
- `MotorController::SetDistanceReader(callback)`: đăng ký hàm đọc khoảng cách
  ToF (mm). Mỗi lệnh di chuyển sẽ đo 1 lần trước khi chạy (`MeasureDistanceBeforeMove()`).
- **Task `motor_floor_monitor`**: đo cảm biến **chỉ khi motor đang chạy tiến**
  (`moving_forward_ == true`, poll ~30ms). Khi robot đứng yên / lùi / xoay thì
  không đo — đáp ứng yêu cầu "không đo liên tục khi không cần".
- Với lệnh tiến (`forward`): đo trước, nếu khoảng cách
  > `kNoFloorMm = 70` (hoặc 8191 = lỗi) thì **chặn** (không chạy) và trả lỗi
  cho LLM để AI nói cảnh báo. Lùi/xoay: đo (log) nhưng vẫn chạy.
- **Trong lúc chạy tiến**, nếu `motor_floor_monitor` phát hiện mất sàn
  (khoảng cách > 70mm) giữa chừng → tự dừng ngay + lùi `kBackOffMs = 200` để
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
  đọc > 70 mm — `kNoFloorMm = 70` — hoặc 8191).
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
- **Chốt ngưỡng chống rơi = 70mm**: `kNoFloorMm = 70`. Sensor đọc > 70 mm (hoặc
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

### 6.4 Cách xác nhận nhanh
- Quay tay động cơ (không cấp điện) rồi nói lệnh → nghe được bình thường =
  chắc chắn do nhiễu điện, không phải firmware.
- Cấp nguồn motor từ nguồn riêng → hết chứng tỏ do sụt áp/nhiễu nguồn.
- [ ] Test trên phần cứng (giọng nói: "đi tới", "đi lùi", "xoay trái", "xoay phải", "dừng lại")
- [ ] Sau này: thêm lệnh phức tạp (đi khoảng cách, PWM tốc độ, v.v.)
