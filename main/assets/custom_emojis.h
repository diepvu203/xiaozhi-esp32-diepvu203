#ifndef CUSTOM_EMOJIS_H
#define CUSTOM_EMOJIS_H
#include "lvgl.h"
#ifdef __cplusplus
extern "C" {
#endif

const lv_image_dsc_t* GetCustomEmojiImage(const char* emotion);

extern const lv_image_dsc_t emoji_alien;
extern const lv_image_dsc_t emoji_bieucam_macdinh;
extern const lv_image_dsc_t emoji_confident;
extern const lv_image_dsc_t emoji_confused;
extern const lv_image_dsc_t emoji_cool;
extern const lv_image_dsc_t emoji_crying;
extern const lv_image_dsc_t emoji_delicious;
extern const lv_image_dsc_t emoji_embarrassed;
extern const lv_image_dsc_t emoji_funny;
extern const lv_image_dsc_t emoji_happy;
extern const lv_image_dsc_t emoji_kissy;
extern const lv_image_dsc_t emoji_laughing;
extern const lv_image_dsc_t emoji_loving;
extern const lv_image_dsc_t emoji_neutral;
extern const lv_image_dsc_t emoji_relaxed;
extern const lv_image_dsc_t emoji_robot;
extern const lv_image_dsc_t emoji_shocked;
extern const lv_image_dsc_t emoji_silly;
extern const lv_image_dsc_t emoji_thinking;
extern const lv_image_dsc_t emoji_winking;

#ifdef __cplusplus
}
#endif
#endif