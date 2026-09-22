#include "music_player.h"

#include <cstring>
#include <memory>
#include <vector>

#include <esp_crt_bundle.h>
#include <esp_http_client.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <esp_wifi.h>

#include "application.h"
#include "audio_service.h"

#include "esp_ae_rate_cvt.h"
#include "esp_audio_dec.h"
#include "esp_audio_dec_default.h"

#define TAG "MusicPlayer"

// Same macro audio_service.cc uses (rate converter config).
#define RATE_CVT_CFG(_src_rate, _dest_rate, _channel)                                        \
    (esp_ae_rate_cvt_cfg_t) {                                                                \
        .src_rate = (uint32_t)(_src_rate), .dest_rate = (uint32_t)(_dest_rate),              \
        .channel = (uint8_t)(_channel), .bits_per_sample = ESP_AUDIO_BIT16, .complexity = 2, \
        .perf_type = ESP_AE_RATE_CVT_PERF_TYPE_SPEED,                                        \
    }

namespace {

// Output chunk pushed to the playback queue: ~85 ms @ 24 kHz.
constexpr size_t kChunkSamples = 2048;
// MP3 max frame: 1152 samples * 2 channels * 16 bits (x2 headroom).
constexpr size_t kDecoderOutBytes = 1152 * 2 * sizeof(int16_t) * 2;
// HTTP read buffer (input to the MP3 decoder). Sized to hold the pre-buffer.
// 64KB > 2KB → allocated in PSRAM (CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=2048).
constexpr size_t kReadBufferBytes = 64 * 1024;
constexpr int kTaskStackBytes = 12288;
constexpr int kTaskPriority = 4;

}  // namespace

MusicPlayer& MusicPlayer::GetInstance() {
    static MusicPlayer instance;
    return instance;
}

bool MusicPlayer::Play(const std::string& url) {
    if (running_.load()) {
        ESP_LOGW(TAG, "Already playing, ignore new request");
        return false;
    }
    if (url.empty()) {
        ESP_LOGW(TAG, "Empty url");
        return false;
    }
    stop_requested_.store(false);
    running_.store(true);

    auto* arg = new std::string(url);
    BaseType_t ok = xTaskCreate(
        [](void* p) {
            std::unique_ptr<std::string> url(static_cast<std::string*>(p));
            MusicPlayer::GetInstance().StreamTask(*url);
            vTaskDelete(nullptr);
        },
        "music", kTaskStackBytes, arg, kTaskPriority, &task_handle_);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create music task");
        running_.store(false);
        delete arg;
        return false;
    }
    ESP_LOGI(TAG, "Playing: %s", url.c_str());
    return true;
}

void MusicPlayer::Stop() {
    if (!running_.load()) {
        return;
    }
    ESP_LOGI(TAG, "Stop requested");
    stop_requested_.store(true);
    // Wait for the task to exit (it owns the decoder/client buffers).
    int waited_ms = 0;
    while (running_.load() && waited_ms < 5000) {
        vTaskDelay(pdMS_TO_TICKS(20));
        waited_ms += 20;
    }
    // Flush any PCM chunks still queued so the speaker goes silent and the
    // buffers are freed immediately.
    Application::GetInstance().GetAudioService().ResetDecoder();
}

void MusicPlayer::FinishTask() { running_.store(false); }

void MusicPlayer::StreamTask(std::string url) {
    AudioService& audio = Application::GetInstance().GetAudioService();
    const int out_rate = audio.GetOutputSampleRate();

    // Shared across reconnect attempts (freed once at the end).
    esp_audio_dec_handle_t decoder = nullptr;
    esp_ae_rate_cvt_handle_t resampler = nullptr;
    std::vector<int16_t> dec_pcm(kDecoderOutBytes / sizeof(int16_t));
    std::vector<int16_t> mono_pcm;
    std::vector<int16_t> resampled;
    std::vector<int16_t> pending;
    int src_rate = 0;
    int src_channels = 0;
    size_t buffered = 0;
    int decode_err_count = 0;
    int64_t last_err_log_us = 0;
    int64_t last_stats_us = esp_timer_get_time();
    int64_t pcm_written = 0;

    // Mute TTS immediately so it does not compete for the radio while we are
    // connecting/reconnecting to the music server.
    audio.SetMusicPlaying(true);

    esp_http_client_handle_t client = nullptr;

    // (Re)stream loop: if the TCP/HTTP link drops mid-song we reconnect and
    // restart the song instead of dying silently. Three attempts keep us from
    // looping forever when the server is really gone.
    for (int stream_attempt = 1; stream_attempt <= 3 && !stop_requested_.load(); ++stream_attempt) {
        if (stream_attempt > 1) {
            ESP_LOGW(TAG, "Stream interrupted, reconnecting (attempt %d/3)", stream_attempt);
            vTaskDelay(pdMS_TO_TICKS(1000));
        }

        // --- Per-stream state (reset on every reconnect) ---
        esp_http_client_config_t http_cfg = {};
        http_cfg.url = url.c_str();
        http_cfg.buffer_size = 8192;
        http_cfg.timeout_ms = 15000;  // weak WiFi needs more time (was 5000)
        http_cfg.keep_alive_enable = true;
        http_cfg.crt_bundle_attach = esp_crt_bundle_attach;

        bool will_retry = false;

        client = esp_http_client_init(&http_cfg);
        if (client == nullptr) {
            ESP_LOGE(TAG, "esp_http_client_init failed");
            break;
        }

        // Retry the connection a few times: on weak WiFi a single TCP/HTTP
        // timeout should not throw away the whole song.
        bool connected = false;
        for (int attempt = 1; attempt <= 3 && !stop_requested_.load(); ++attempt) {
            if (esp_http_client_open(client, 0) == ESP_OK) {
                connected = true;
                break;
            }
            ESP_LOGW(TAG, "Failed to open connection (attempt %d/3)", attempt);
            vTaskDelay(pdMS_TO_TICKS(700));
        }
        if (!connected) {
            ESP_LOGE(TAG, "Failed to open connection after retries");
            will_retry = true;
        } else {
            esp_http_client_fetch_headers(client);
            int status = esp_http_client_get_status_code(client);
            if (status != 200) {
                ESP_LOGE(TAG, "HTTP status %d", status);
                will_retry = true;
            }
        }

        if (will_retry) {
            esp_http_client_close(client);
            esp_http_client_cleanup(client);
            client = nullptr;
            continue;
        }

        ESP_LOGI(TAG, "Streaming (output %d Hz)", out_rate);

        // --- Per-stream decode state (fresh for each reconnect) ---
        std::vector<uint8_t> inbuf(kReadBufferBytes);
        src_rate = 0;
        src_channels = 0;
        buffered = 0;
        pending.clear();
        bool eos = false;

        esp_audio_dec_register_default();
        esp_audio_dec_cfg_t dec_cfg = {};
        dec_cfg.type = ESP_AUDIO_TYPE_MP3;
        if (esp_audio_dec_open(&dec_cfg, &decoder) != ESP_AUDIO_ERR_OK) {
            ESP_LOGE(TAG, "Failed to open MP3 decoder");
            esp_audio_dec_unregister_default();
            decoder = nullptr;
            will_retry = true;
        }

        if (!will_retry) {
            // Pre-buffer ~48 KB (~4 s @96 kbps) before decoding/playing so a
            // laggy WiFi link does not cause stutter.
            constexpr size_t kPreBufferBytes = 48 * 1024;
            while (!stop_requested_.load() && !eos && buffered < kPreBufferBytes) {
                int plen =
                    esp_http_client_read(client, reinterpret_cast<char*>(inbuf.data()) + buffered,
                                         inbuf.size() - buffered);
                if (plen < 0) {
                    ESP_LOGE(TAG, "HTTP read error during pre-buffer");
                    will_retry = true;
                    break;
                }
                if (plen == 0) {
                    if (esp_http_client_is_complete_data_received(client)) {
                        eos = true;
                    } else {
                        vTaskDelay(pdMS_TO_TICKS(5));
                    }
                } else {
                    buffered += plen;
                }
            }
        }

        if (will_retry) {
            esp_audio_dec_close(decoder);
            esp_audio_dec_unregister_default();
            decoder = nullptr;
            esp_http_client_close(client);
            esp_http_client_cleanup(client);
            client = nullptr;
            continue;
        }

        ESP_LOGI(TAG, "Pre-buffered %u bytes", (unsigned)buffered);

        while (!stop_requested_.load() && !eos) {
            // Periodic diagnostics: RSSI + buffering state + decoder health.
            int64_t now_us = esp_timer_get_time();
            if (now_us - last_stats_us > 5000000) {
                last_stats_us = now_us;
                wifi_ap_record_t ap = {};
                if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
                    ESP_LOGI(TAG, "stats: buffered %u B, errs %d, RSSI %d dBm", (unsigned)buffered,
                             decode_err_count, ap.rssi);
                } else {
                    ESP_LOGI(TAG, "stats: buffered %u B, errs %d", (unsigned)buffered,
                             decode_err_count);
                }
            }

            // Read data if we have room.
            if (buffered < inbuf.size()) {
                int len =
                    esp_http_client_read(client, reinterpret_cast<char*>(inbuf.data()) + buffered,
                                         inbuf.size() - buffered);
                if (len < 0) {
                    ESP_LOGE(TAG, "HTTP read error");
                    will_retry = true;
                    break;
                }
                if (len == 0) {
                    if (esp_http_client_is_complete_data_received(client)) {
                        eos = true;
                    } else {
                        vTaskDelay(pdMS_TO_TICKS(10));
                    }
                } else {
                    buffered += len;
                    ESP_LOGD(TAG, "http read %d, buffered %u", len, (unsigned)buffered);
                }
            }
            if (buffered == 0) {
                if (eos)
                    break;
                continue;
            }

            // Decode everything we currently have. The decoder consumes what it
            // can; on a generic error we resync by dropping one byte (MP3
            // decoders scan forward to the next frame header on their own).
            uint8_t* src = inbuf.data();
            uint32_t remaining = static_cast<uint32_t>(buffered);
            bool chunk_pushed = false;
            while (!stop_requested_.load() && remaining > 0) {
                esp_audio_dec_in_raw_t raw = {
                    .buffer = src,
                    .len = remaining,
                    .consumed = 0,
                    .frame_recover = ESP_AUDIO_DEC_RECOVERY_NONE,
                };
                esp_audio_dec_out_frame_t frame = {
                    .buffer = reinterpret_cast<uint8_t*>(dec_pcm.data()),
                    .len = static_cast<uint32_t>(dec_pcm.size() * sizeof(int16_t)),
                    .needed_size = 0,
                    .decoded_size = 0,
                };
                esp_audio_err_t ret = esp_audio_dec_process(decoder, &raw, &frame);
                if (ret == ESP_AUDIO_ERR_BUFF_NOT_ENOUGH) {
                    // Grow output and retry the SAME input (reference behaviour).
                    dec_pcm.resize(frame.needed_size / sizeof(int16_t) + 128);
                    continue;
                }
                if (ret == ESP_AUDIO_ERR_DATA_LACK) {
                    // Not enough bytes for a full frame yet: keep the leftover
                    // and read more from the network. Do NOT skip bytes here,
                    // that would break MP3 frame alignment.
                    break;
                }
                if (ret != ESP_AUDIO_ERR_OK) {
                    // Genuine corrupt frame (FAIL/HEADER_PARSE): skip one byte
                    // so the decoder can scan forward to the next frame header.
                    // Log at most once per second but ALWAYS count the total.
                    decode_err_count++;
                    int64_t now_us = esp_timer_get_time();
                    if (now_us - last_err_log_us > 1000000) {
                        ESP_LOGW(TAG, "Decode error %d (total %d, resync 1 byte)", ret,
                                 decode_err_count);
                        last_err_log_us = now_us;
                    }
                    src += 1;
                    remaining -= 1;
                    continue;
                }
                uint32_t consumed = raw.consumed;
                if (frame.decoded_size > 0) {
                    // Update stream format on first decoded frame.
                    if (src_rate == 0) {
                        esp_audio_dec_info_t info = {};
                        esp_audio_dec_get_info(decoder, &info);
                        src_rate = static_cast<int>(info.sample_rate);
                        src_channels = info.channel;
                        ESP_LOGI(TAG, "MP3 stream: %d Hz, %d ch", src_rate, src_channels);
                        if (src_rate != out_rate) {
                            esp_ae_rate_cvt_cfg_t rate_cfg = RATE_CVT_CFG(src_rate, out_rate, 1);
                            if (esp_ae_rate_cvt_open(&rate_cfg, &resampler) != ESP_OK) {
                                ESP_LOGE(TAG, "Failed to open resampler");
                                resampler = nullptr;
                            }
                        }
                    }
                    // Downmix to mono if needed.
                    // IMPORTANT: dec_pcm is pre-allocated to kDecoderOutBytes (4608
                    // samples) for headroom, but only the first `samples` elements
                    // are valid output for this frame. Always copy the valid slice
                    // into mono_pcm so pending.insert gets the correct length.
                    const int16_t* pcm = dec_pcm.data();
                    size_t samples = frame.decoded_size / sizeof(int16_t);
                    if (src_channels == 2) {
                        mono_pcm.resize(samples / 2);
                        for (size_t i = 0; i < mono_pcm.size(); ++i) {
                            mono_pcm[i] = static_cast<int16_t>(
                                (static_cast<int32_t>(pcm[2 * i]) + pcm[2 * i + 1]) / 2);
                        }
                    } else {
                        // Stream is already mono; slice only the decoded portion.
                        mono_pcm.assign(dec_pcm.begin(), dec_pcm.begin() + samples);
                    }

                    // Resample to the codec output rate if needed.
                    std::vector<int16_t>* out = &mono_pcm;
                    if (resampler != nullptr) {
                        uint32_t target = 0;
                        esp_ae_rate_cvt_get_max_out_sample_num(resampler, mono_pcm.size(),
                                                               &target);
                        resampled.resize(target);
                        uint32_t actual = target;
                        esp_ae_rate_cvt_process(resampler, (esp_ae_sample_t)mono_pcm.data(),
                                                mono_pcm.size(), (esp_ae_sample_t)resampled.data(),
                                                &actual);
                        resampled.resize(actual);
                        out = &resampled;
                    }

                    // Accumulate and push in fixed-size chunks so the playback
                    // queue stays small but never starves.
                    pending.insert(pending.end(), out->begin(), out->end());
                    while (pending.size() >= kChunkSamples && !stop_requested_.load()) {
                        std::vector<int16_t> chunk(pending.begin(),
                                                   pending.begin() + kChunkSamples);
                        pending.erase(pending.begin(), pending.begin() + kChunkSamples);
                        // Deep jitter buffer (20 chunks approx 1.7 s) so
                        // WiFi bursts do not underrun the speaker queue.
                        if (!audio.PushPcmToPlaybackQueue(std::move(chunk), 20)) {
                            ESP_LOGW(TAG, "Playback queue busy, drop chunk");
                        } else {
                            pcm_written += kChunkSamples;
                        }
                        chunk_pushed = true;
                    }
                }
                if (consumed > 0) {
                    src += consumed;
                    remaining -= consumed;
                } else {
                    break;  // need more input data
                }

                // If we produced and pushed a chunk to the playback queue, pause
                // decoding for now so the outer loop can read more HTTP data and
                // keep inbuf full (~48-64 KB) instead of draining it to 0.
                // (When eos is true, we keep draining to the end of the song).
                if (chunk_pushed && !eos) {
                    break;
                }
            }

            // Compact the leftover to the front of the input buffer.
            if (remaining > 0 && src != inbuf.data()) {
                memmove(inbuf.data(), src, remaining);
            }
            buffered = remaining;

            // If we could not consume and the buffer is full, force a forward
            // resync so a stuck decoder makes progress instead of looping.
            if (buffered == inbuf.size() && buffered > 0 && !eos) {
                ESP_LOGW(TAG, "Stuck, resync 1 byte");
                memmove(inbuf.data(), inbuf.data() + 1, buffered - 1);
                buffered -= 1;
            }
        }  // Flush the tail chunk (< kChunkSamples) so short songs end cleanly.
        if (!stop_requested_.load() && !pending.empty()) {
            audio.PushPcmToPlaybackQueue(std::move(pending));
            pending.clear();
        }
        if (!will_retry) {
            ESP_LOGI(TAG, "Stream finished, ~%lu k samples written, %d decode errors",
                     (unsigned long)(pcm_written / 1000), decode_err_count);
            break;  // song finished successfully, exit retry loop
        }
        // will_retry == true: stream failed mid-way, continue to reconnect.
        continue;
    }

    // --- Cleanup: free everything the task allocated ---
    if (resampler != nullptr) {
        esp_ae_rate_cvt_close(resampler);
        resampler = nullptr;
    }
    if (decoder != nullptr) {
        esp_audio_dec_close(decoder);
        esp_audio_dec_unregister_default();
        decoder = nullptr;
    }
    if (client != nullptr) {
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        client = nullptr;
    }
    ESP_LOGI(TAG, "Music task exit (buffers freed)");
    audio.SetMusicPlaying(false);  // unmute TTS now that music has stopped
    FinishTask();
}
