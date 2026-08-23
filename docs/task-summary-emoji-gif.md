# Tóm Tắt Công Việc: Emoji Biểu Cảm + GIF Động trên OLED 128x64

## 1. Tổng Quan

Dự án XiaoZhi ESP32 (firmware voice-assistant) tại `d:\xiaozhi\xiaozhi-esp32-diepvu203`.
Board: **bread-compact-wifi** (ESP32) dùng **OledDisplay** (SSD1306 128x64).
Màn hình OLED monochrome 1-bit, hiển thị emoji biểu cảm full màn hình 128x64.

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