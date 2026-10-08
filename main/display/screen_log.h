#ifndef SCREEN_LOG_H
#define SCREEN_LOG_H

#include <esp_log.h>

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include <atomic>
#include <cstdarg>
#include <string>
#include <vector>

// Thread-safe ring buffer for on-screen log view.
// Captures ESP_LOG output via esp_log_set_vprintf hook and keeps serial
// output working via passthrough to the original vprintf.
class ScreenLogStore {
public:
    static ScreenLogStore& GetInstance();

    // Install esp_log vprintf hook once. Safe to call multiple times.
    void Install();

    // Number of retained lines and max chars per line.
    void Configure(size_t max_lines, size_t max_chars_per_line);

    // Raw write from the log hook. Never calls ESP_LOG (no recursion).
    // Drops the line if the buffer is busy (audio paths must not block).
    void PushRaw(const char* data, size_t len);

    // Copy joined text (oldest first, '\n' separated). Main-task side.
    std::string GetText() const;

    bool HasUpdate() const { return has_update_; }
    void ClearUpdate() { has_update_ = false; }
    size_t LineCount() const;

private:
    ScreenLogStore();
    ScreenLogStore(const ScreenLogStore&) = delete;
    ScreenLogStore& operator=(const ScreenLogStore&) = delete;

    static int LogHook(const char* format, va_list args);

    mutable SemaphoreHandle_t mutex_ = nullptr;
    vprintf_like_t original_vprintf_ = nullptr;
    bool installed_ = false;

    std::vector<std::string> lines_;
    size_t max_lines_ = 50;
    size_t max_chars_per_line_ = 110;
    std::atomic<bool> has_update_{false};
    std::string line_frag_;
};

#endif
