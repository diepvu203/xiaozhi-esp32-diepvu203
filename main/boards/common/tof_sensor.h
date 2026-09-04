#ifndef __TOF_SENSOR_H__
#define __TOF_SENSOR_H__

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <driver/i2c_master.h>
#include <driver/gpio.h>
#include <esp_log.h>

#include "vl53l0x.h"

// Wrapper cho cam bien ToF VL53L0X dung component chuan (components/vl53l0x,
// copy tu du an test cua sep). Dung de phat hien mat san (cliff detection):
// cam bien huong xuong san, neu khoang cach do lon hon nguong (hoac loi do)
// thi coi nhu mat san.
class TofSensor {
public:
    // Khoang cach (mm) toi mat ban: neu do lon hon nguong nay = khong thay san
    // (mat canh bao: > 30mm la coi nhu sap roi, dung ngay). Chi nho: mm = 0
    // (sensor rat sat ban) van la co san.
    static constexpr uint16_t kNoFloorMm = 30;
    // Gia tri tra ve khi loi do / het range
    static constexpr uint16_t kErrorMm = 8191;

    TofSensor(gpio_num_t sda, gpio_num_t scl, gpio_num_t xshut,
              i2c_port_t port = I2C_NUM_1) : xshut_(xshut) {
        if (sda == GPIO_NUM_NC || scl == GPIO_NUM_NC || xshut == GPIO_NUM_NC) {
            return;
        }

        // XSHUT: keo thap truoc khi init de dam bao sensor o dia chi mac dinh
        gpio_config_t io_conf = {
            .pin_bit_mask = (1ULL << xshut_),
            .mode = GPIO_MODE_OUTPUT,
            .pull_up_en = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        ESP_ERROR_CHECK(gpio_config(&io_conf));
        gpio_set_level(xshut_, 0);
        vTaskDelay(pdMS_TO_TICKS(10));
        gpio_set_level(xshut_, 1);
        vTaskDelay(pdMS_TO_TICKS(10));

        i2c_master_bus_config_t bus_config = {
            .i2c_port = port,
            .sda_io_num = sda,
            .scl_io_num = scl,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&bus_config, &bus_));

        esp_err_t err = vl53l0x_create(&sensor_, bus_);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "vl53l0x_create failed: %s", esp_err_to_name(err));
            return;
        }
        err = vl53l0x_init(sensor_);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "vl53l0x_init failed: %s", esp_err_to_name(err));
            sensor_ = nullptr;
            return;
        }

        // Bat buoc chay calibration truoc khi do: neu khong, rang range status
        // se khac 0 (valid = false) va ReadDistanceMm() luon tra ve kErrorMm.
        // Trinh tu theo example single_ranging cua component.
        vl53l0x_ref_spad_calibration_t spad = {};
        if (vl53l0x_perform_ref_spad_management(sensor_, &spad) == ESP_OK) {
            vl53l0x_set_reference_spads(sensor_, &spad);
            ESP_LOGI(TAG, "Ref SPAD: count=%u aperture=%d", (unsigned)spad.count, (int)spad.is_aperture);
        } else {
            ESP_LOGW(TAG, "Ref SPAD management failed; apply default SPAD fallback");
            spad.count = 3;
            spad.is_aperture = true;
            vl53l0x_set_reference_spads(sensor_, &spad);
        }
        vl53l0x_ref_calibration_t ref_cal = {};
        if (vl53l0x_perform_ref_calibration(sensor_, &ref_cal) != ESP_OK) {
            ESP_LOGW(TAG, "Ref calibration failed; continuing anyway");
        }
        // Khong co du lieu offset/xtalk cua rieng modem -> dung gia tri 0/off
        vl53l0x_set_offset_calibration(sensor_, 0);
        vl53l0x_set_xtalk_compensation_enable(sensor_, false);
        vl53l0x_set_profile(sensor_, VL53L0X_PROFILE_DEFAULT);

        initialized_ = true;
        ESP_LOGI(TAG, "Initialized");

        // Do thu mot lan de xac nhan cam bien hoat dong that su
        uint16_t test_mm = ReadDistanceMm();
        ESP_LOGI(TAG, "Self-test reading: %d mm", test_mm);
    }

    bool IsReady() const { return initialized_; }

    // Doc khoang cach (mm). Tra ve kErrorMm neu loi do / het range.
    uint16_t ReadDistanceMm() {
        if (!initialized_) {
            return kErrorMm;
        }
        vl53l0x_data_t data;
        esp_err_t err = vl53l0x_single_measure(sensor_, &data);
        if (err != ESP_OK || !data.valid) {
            return kErrorMm;
        }
        return data.distance_mm;
    }

    // true = van con san duoi chan robot
    bool HasFloor() {
        return ReadDistanceMm() <= kNoFloorMm;
    }

private:
    static constexpr const char* TAG = "TofSensor";

    i2c_master_bus_handle_t bus_ = nullptr;
    vl53l0x_handle_t sensor_ = nullptr;
    gpio_num_t xshut_;
    bool initialized_ = false;
};

#endif // __TOF_SENSOR_H__