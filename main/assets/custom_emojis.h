#ifndef CUSTOM_EMOJIS_H
#define CUSTOM_EMOJIS_H
#include "lvgl.h"
#ifdef __cplusplus
extern "C" {
#endif

const lv_image_dsc_t* GetCustomEmojiImage(const char* emotion);

extern const lv_image_dsc_t emoji_angry;
extern const lv_image_dsc_t emoji_bieucam_macdinh;
extern const lv_image_dsc_t emoji_sad;
extern const lv_image_dsc_t emoji_sleepy;
extern const lv_image_dsc_t emoji_surprised;

#ifdef __cplusplus
}
#endif
#endif