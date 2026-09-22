#ifndef MUSIC_PLAYER_H
#define MUSIC_PLAYER_H

#include <atomic>
#include <string>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

// Streams an MP3 over HTTP(S) (Cloud Stream Proxy `/stream/<id>.mp3`),
// decodes it on the fly with `esp_audio_simple_dec` and pushes mono PCM
// (resampled to the codec output sample rate) into the AudioService
// playback queue, so it reaches the I2S DAC through the normal
// `AudioOutputTask` path.
//
// Triggered by the `self.music.play` MCP tool, stopped via
// `self.music.stop` (or by calling Stop() directly).
//
// Memory policy: all buffers live inside the streaming task and are freed
// when the task exits; Stop() flushes the playback queue so the speaker
// goes quiet immediately.
class MusicPlayer {
public:
    static MusicPlayer& GetInstance();

    // Start streaming `url` in a background FreeRTOS task.
    // Returns false if already playing.
    bool Play(const std::string& url);

    // Ask the task to stop, wait for it to exit and flush the playback
    // queue (frees queued PCM buffers, avoids leak/late sound).
    void Stop();

    bool IsPlaying() const { return running_.load(); }

private:
    MusicPlayer() = default;
    ~MusicPlayer() = default;
    MusicPlayer(const MusicPlayer&) = delete;
    MusicPlayer& operator=(const MusicPlayer&) = delete;

    void StreamTask(std::string url);
    void FinishTask();  // task-side cleanup (decoder, resampler, client)

    std::atomic<bool> running_{false};
    std::atomic<bool> stop_requested_{false};
    TaskHandle_t task_handle_ = nullptr;
};

#endif  // MUSIC_PLAYER_H