#ifndef _BOARD_CONFIG_H_
#define _BOARD_CONFIG_H_

#include <driver/gpio.h>

#define AUDIO_INPUT_SAMPLE_RATE  16000
#define AUDIO_OUTPUT_SAMPLE_RATE 24000

// 如果使用 Duplex I2S 模式，请注释下面一行
#define AUDIO_I2S_METHOD_SIMPLEX

#ifdef AUDIO_I2S_METHOD_SIMPLEX

#define AUDIO_I2S_MIC_GPIO_WS   GPIO_NUM_4
#define AUDIO_I2S_MIC_GPIO_SCK  GPIO_NUM_5
#define AUDIO_I2S_MIC_GPIO_DIN  GPIO_NUM_6
#define AUDIO_I2S_SPK_GPIO_DOUT GPIO_NUM_7
#define AUDIO_I2S_SPK_GPIO_BCLK GPIO_NUM_15
#define AUDIO_I2S_SPK_GPIO_LRCK GPIO_NUM_16

#else

#define AUDIO_I2S_GPIO_WS GPIO_NUM_4
#define AUDIO_I2S_GPIO_BCLK GPIO_NUM_5
#define AUDIO_I2S_GPIO_DIN  GPIO_NUM_6
#define AUDIO_I2S_GPIO_DOUT GPIO_NUM_7

#endif


#define BUILTIN_LED_GPIO        GPIO_NUM_48
#define BOOT_BUTTON_GPIO        GPIO_NUM_0
#define TOUCH_BUTTON_GPIO       GPIO_NUM_47
// GPIO39/40 da chuyen sang dung cho cam bien ToF VL53L0X (bo 2 nut am luong)

#define DISPLAY_SDA_PIN GPIO_NUM_41
#define DISPLAY_SCL_PIN GPIO_NUM_42
#define DISPLAY_WIDTH   128

#if CONFIG_OLED_SSD1306_128X32
#define DISPLAY_HEIGHT  32
#elif CONFIG_OLED_SSD1306_128X64
#define DISPLAY_HEIGHT  64
#elif CONFIG_OLED_SH1106_128X64
#define DISPLAY_HEIGHT  64
#define SH1106
#else
#error "OLED display type is not selected"
#endif

#define DISPLAY_MIRROR_X true
#define DISPLAY_MIRROR_Y true


// A MCP Test: Control a lamp (LED connected to GPIO 11)
#define LAMP_GPIO GPIO_NUM_11

// Motor driver L298N (2x N20 wheel motors)
// ENA/ENB cua L298N noi muc cao co dinh (toc do khong doi)
#define MOTOR_L_IN1 GPIO_NUM_1   // Left motor IN1
#define MOTOR_L_IN2 GPIO_NUM_2   // Left motor IN2
#define MOTOR_R_IN1 GPIO_NUM_21  // Right motor IN3
#define MOTOR_R_IN2 GPIO_NUM_8   // Right motor IN4

// Cam bien ToF VL53L0X (chong roi khoi mat ban)
// LUU Y: KHONG dung GPIO35/36/37 - cac chan nay cua PSRAM octal tren ESP32-S3
#define TOF_I2C_SDA GPIO_NUM_40
#define TOF_I2C_SCL GPIO_NUM_39
#define TOF_XSHUT   GPIO_NUM_10

#endif // _BOARD_CONFIG_H_
