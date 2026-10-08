#include "screen_log.h"

#include <esp_log.h>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <cstdio>
#include <cstring>

ScreenLogStore& ScreenLogStore::GetInstance() {
    static ScreenLogStore instance;
    return instance;
}

ScreenLogStore::ScreenLogStore() {
    mutex_ = xSemaphoreCreateMutex();
    lines_.reserve(64);
}

void ScreenLogStore::Configure(size_t max_lines, size_t max_chars_per_line) {
    if (xSemaphoreTake(mutex_, portMAX_DELAY) == pdTRUE) {
        max_lines_ = max_lines;
        max_chars_per_line_ = max_chars_per_line;
        while (lines_.size() > max_lines_) {
            lines_.erase(lines_.begin());
        }
        xSemaphoreGive(mutex_);
    }
}

void ScreenLogStore::Install() {
    if (installed_) {
        return;
    }
    original_vprintf_ = esp_log_set_vprintf(LogHook);
    installed_ = true;
}

int ScreenLogStore::LogHook(const char* format, va_list args) {
    auto& self = GetInstance();

    // ISR-safe: never touch mutex/heap from ISR, just passthrough to serial.
    // This also avoids FreeRTOS assert (xSemaphoreTake from ISR) which can
    // hang boot if any driver logs from interrupt context.
    if (xPortInIsrContext()) {
        if (self.original_vprintf_ != nullptr) {
            return self.original_vprintf_(format, args);
        }
        return 0;
    }

    // Format into a small stack buffer first (no heap, no ESP_LOG here).
    char tmp[256];
    va_list copy;
    va_copy(copy, args);
    int n = vsnprintf(tmp, sizeof(tmp), format, copy);
    va_end(copy);
    if (n > 0) {
        size_t len = strnlen(tmp, sizeof(tmp));
        // Keep room for newline handling inside PushRaw.
        self.PushRaw(tmp, len);
    }

    // Serial passthrough keeps USB log working.
    if (self.original_vprintf_ != nullptr) {
        return self.original_vprintf_(format, args);
    }
    return n;
}

void ScreenLogStore::PushRaw(const char* data, size_t len) {
    if (data == nullptr || len == 0) {
        return;
    }
    // Never block audio/log tasks: drop when busy.
    if (xSemaphoreTake(mutex_, 0) != pdTRUE) {
        return;
    }

    for (size_t i = 0; i < len; ++i) {
        char c = data[i];
        if (c == '\r') {
            continue;
        }
        if (c == '\n') {
            if (!line_frag_.empty()) {
                if (line_frag_.length() > max_chars_per_line_) {
                    line_frag_.resize(max_chars_per_line_);
                }
                lines_.push_back(line_frag_);
                line_frag_.clear();
                while (lines_.size() > max_lines_) {
                    lines_.erase(lines_.begin());
                }
                has_update_ = true;
            }
        } else {
            line_frag_ += c;
            // Flush very long lines without waiting for newline.
            if (line_frag_.length() >= max_chars_per_line_ + 40) {
                std::string cut = line_frag_.substr(0, max_chars_per_line_);
                lines_.push_back(cut);
                line_frag_.clear();
                while (lines_.size() > max_lines_) {
                    lines_.erase(lines_.begin());
                }
                has_update_ = true;
            }
            // Strip ANSI color escapes: drop ESC [ ... m sequences.
            // Kept simple: if frag contains ESC, truncate it out.
            size_t esc = line_frag_.find('\x1b');
            if (esc != std::string::npos) {
                // Remove ESC and following "[..m" if complete, else keep waiting.
                size_t m = line_frag_.find('m', esc);
                if (m != std::string::npos) {
                    line_frag_.erase(esc, m - esc + 1);
                } else if (line_frag_.length() - esc > 16) {
                    line_frag_.erase(esc);
                }
            }
        }
    }

    xSemaphoreGive(mutex_);
}

std::string ScreenLogStore::GetText() const {
    std::string out;
    if (xSemaphoreTake(mutex_, portMAX_DELAY) != pdTRUE) {
        return out;
    }
    size_t total = 0;
    for (auto& l : lines_) {
        total += l.length() + 1;
    }
    total += line_frag_.length();
    out.reserve(total + 1);
    for (auto& l : lines_) {
        out += l;
        out += '\n';
    }
    if (!line_frag_.empty()) {
        out += line_frag_;
    }
    xSemaphoreGive(mutex_);
    return out;
}

size_t ScreenLogStore::LineCount() const {
    size_t n = 0;
    if (xSemaphoreTake(mutex_, portMAX_DELAY) == pdTRUE) {
        n = lines_.size();
        xSemaphoreGive(mutex_);
    }
    return n;
}
