#ifndef CUSTOM_GIFS_H
#define CUSTOM_GIFS_H
#include "lvgl.h"
#ifdef __cplusplus
extern "C" {
#endif

const lv_image_dsc_t* GetCustomGifImage(const char* emotion);

extern const lv_image_dsc_t gif_angry;
extern const lv_image_dsc_t gif_confident;
extern const lv_image_dsc_t gif_confused;
extern const lv_image_dsc_t gif_cool;
extern const lv_image_dsc_t gif_crying;
extern const lv_image_dsc_t gif_delicious;
extern const lv_image_dsc_t gif_embarrassed;
extern const lv_image_dsc_t gif_funny;
extern const lv_image_dsc_t gif_happy;
extern const lv_image_dsc_t gif_kissy;
extern const lv_image_dsc_t gif_laughing;
extern const lv_image_dsc_t gif_loving;
extern const lv_image_dsc_t gif_neutral;
extern const lv_image_dsc_t gif_relaxed;
extern const lv_image_dsc_t gif_sad;
extern const lv_image_dsc_t gif_shocked;
extern const lv_image_dsc_t gif_silly;
extern const lv_image_dsc_t gif_sleepy;
extern const lv_image_dsc_t gif_thinking;
extern const lv_image_dsc_t gif_winking;

#ifdef __cplusplus
}
#endif
#endif