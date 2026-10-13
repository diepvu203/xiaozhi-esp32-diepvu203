# bread-compact-wifi-lcd: LCD board port notes

This document records the robot-feature port and build configuration for the
ESP32-S3 `bread-compact-wifi-lcd` variant. It supplements
[`robot-movement.md`](./robot-movement.md), which primarily documents the OLED
`bread-compact-wifi` board. The two boards have different pin assignments; do
not copy pin values between them.

## Board identity and display

- Board directory and firmware identity: `bread-compact-wifi-lcd`.
- Target: ESP32-S3.
- `main/boards/bread-compact-wifi-lcd/config.json` selects
  `CONFIG_LCD_ST7789_240X240=y` for this build variant.
- LCD initialization and SPI/display pins remain owned by
  `compact_wifi_board_lcd.cc` and this board's `config.h`.
- LCD selection does not change the board directory or board identity.

## Ported functions

- L298N / two N20 motor support via the shared `MotorController`.
- VL53L0X ToF distance reader used by the motor's forward/cliff safety checks.
- Wake-word feedback calls `MotorController::Wiggle()` through
  `Board::OnWakeWordDetected()`.
- MCP music tools: `self.music.play` (URL), `self.music.stop`, and
  `self.music.get_state`.
- BOOT click enters Wi-Fi setup while starting. Otherwise it toggles chat; if
  music is playing, it stops music and returns the application to listening.
- The common lamp controller is still initialized on `LAMP_GPIO` (GPIO 18),
  retaining the board's lamp MCP tools.

## LCD-board pin assignments

| Function | GPIO | Source |
|---|---:|---|
| L298N left IN1 / IN2 | 1 / 2 | `MOTOR_L_IN1`, `MOTOR_L_IN2` |
| L298N right IN1 / IN2 | 3 / 8 | `MOTOR_R_IN1`, `MOTOR_R_IN2` |
| VL53L0X SDA / SCL | 17 / 39 | `TOF_I2C_SDA`, `TOF_I2C_SCL` |
| VL53L0X XSHUT | 10 | `TOF_XSHUT` |
| Lamp output | 18 | `LAMP_GPIO` |
| BOOT button | 0 | `BOOT_BUTTON_GPIO` |

These are the LCD board values. The OLED `bread-compact-wifi` board uses a
different ToF SDA and right-motor pin assignment; see its own `config.h`.
Confirm all wiring against the actual hardware before powering the motors.

## VL53L0X build integration

The VL53L0X component is present under `components/vl53l0x/`. Its source files
are added to the `main` component by `main/CMakeLists.txt` for both
`CONFIG_BOARD_TYPE_BREAD_COMPACT_WIFI` and
`CONFIG_BOARD_TYPE_BREAD_COMPACT_WIFI_LCD`. The same condition applies
`USE_I2C_2V8=1`. This makes the driver available in the minimal project build
without changing the LCD board source selection.

If initialization reports the sensor unavailable, the firmware logs a warning
and the forward cliff guard is disabled; treat this as an unsafe condition for
robot operation until wiring and sensor startup have been verified.

## Build and validation status

Canonical build command:

```powershell
python scripts/build.py bread-compact-wifi-lcd --name bread-compact-wifi-lcd
```

At the time this note was written, source/configuration changes were made, but
the build had **not** been verified: `idf.py` and `ninja` were unavailable in
the environment. A successful host-side diff/config check is not a firmware
compile or hardware test. After setting up ESP-IDF, perform a clean build if
the existing build cache has a different target, then flash and verify:

1. ST7789 LCD dimensions, orientation, colors, and backlight.
2. VL53L0X initialization and distance readings.
3. Forward motion is blocked at a detected table edge; reverse/stop still work.
4. Wake-word wiggle, BOOT behavior, and music play/stop/listening behavior.
5. Lamp MCP control, if this feature is required by the deployment.
6. 2x INMP441 mic recognition and AEC echo suppression during music playback.

Motor and cliff-protection behavior must be tested physically with wheels
raised first, then in a controlled environment. Compile success alone does not
validate pin wiring or safety behavior.

## Dual Microphone Array & Hardware AEC (2x INMP441)

To support wake-word barge-in during loud music or TTS playback, the board is configured with a dual INMP441 microphone setup using I2S Simplex Stereo:

| Micro | Chân L/R | Kênh I2S | Vai trò trong ESP-SR AFE |
|---|---|---|---|
| **Mic 1** | Nối **GND** | Left (`M`) | Thu giọng nói người dùng |
| **Mic 2** | Nối **3.3V** | Right (`R`) | Đặt gần loa thu tín hiệu tham chiếu khử vọng (AEC) |

- **I2S Simplex**: Dùng chung các chân I2S input `SCK = GPIO 5`, `WS = GPIO 4`, `DIN = GPIO 6`.
- **Cấu hình Codec ([`config.h`](file:///d:/xiaozhi/xiaozhi-esp32-diepvu203/main/boards/bread-compact-wifi-lcd/config.h))**:
  ```c
  #define AUDIO_INPUT_REFERENCE    true
  #define AUDIO_INPUT_CHANNELS     2
  ```
- **Xử lý tín hiệu**:
  - `NoAudioCodecSimplex`: Mở I2S Stereo (`I2S_SLOT_MODE_STEREO`, `I2S_STD_SLOT_BOTH`). Độ nhạy mic sử dụng bit shift cố định `>> 12` (không dùng AutoGain động để tránh hụt giọng nói).
  - `AfeAudioEngine`: Nhận chuỗi kênh `"MR"` và khởi tạo `afe_config->aec_init = true` với chế độ `AEC_MODE_SR_LOW_COST`, NLP `AEC_NLP_LEVEL_AGGR`, và VAD bật.
  - `AudioService`: Khi phát nhạc (`music_playing_ = true`), micro **vẫn được giữ bật** nếu `codec_->input_reference()` hoạt động, cho phép robot nhận diện wake-word ngắt nhạc bằng giọng nói thay vì phải bấm nút BOOT vật lý.

## Tối ưu bộ nhớ PSRAM cho Audio & Mạng

- **Static Task Allocation trên PSRAM**: Task xử lý âm thanh `audio_afe` và task phát nhạc `music` cấp phát stack trên PSRAM (`MALLOC_CAP_SPIRAM`), tránh cạn kiệt Internal SRAM khi bật AEC và giải mã MP3 đồng thời.
- **Tự động ngắt TTS**: Khi `MusicPlayer::StreamTask` bắt đầu mở luồng nhạc, hệ thống tự động gọi `AbortSpeaking()` nếu robot đang đọc thông báo dở dang, giải phóng queue âm thanh ngay lập tức.
- **Config Flags (`config.json`)**:
  - `CONFIG_USE_DEVICE_AEC=y`
  - `CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP=y`
  - `CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=256`
  - `CONFIG_SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY=y`

## Files changed / relevant

- `main/boards/bread-compact-wifi-lcd/config.json` — selects ST7789 240x240 and audio/PSRAM configs.
- `main/boards/bread-compact-wifi-lcd/config.h` — LCD-board motor, ToF, and 2-mic AEC settings.
- `main/boards/bread-compact-wifi-lcd/compact_wifi_board_lcd.cc` — MCP tools,
  ToF hookup, BOOT/music handling, and wake-word motor feedback.
- `main/audio/codecs/no_audio_codec.cc` & `.h` — Stereo I2S simplex capture for dual-mic input.
- `main/audio/engines/afe_audio_engine.cc` & `.h` — Dual-channel `"MR"` AEC pipeline with PSRAM stack.
- `main/audio/audio_service.cc` — Conditional mic keep-alive during music streaming when AEC is enabled.
- `main/audio/music_player.cc` — PSRAM stack for music stream task and auto abort speaking on play.
- `main/CMakeLists.txt` — VL53L0X sources and compile definition for both
  compact Wi-Fi variants.
- `main/boards/common/motor_controller.h` and `tof_sensor.h` — shared motor
  and sensor implementations.