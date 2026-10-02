# Chỉnh Chất Lượng Âm Thanh Nhạc (Speaker EQ / DSP)

> **Nơi cấu hình**: `tools/zing-music-mcp/server.py` — `_SPEAKER_EQ`, `AUDIO_PRESETS`,
> `AUDIO_SAMPLE_RATE`, `AUDIO_BITRATE`, `AUDIO_FILTERS`.
> **Loa**: loa nhỏ ~3W trên robot (bread-compact-wifi), codec output 24000 Hz mono.

## 1. Chuỗi EQ hiện hành (`_SPEAKER_EQ`, v2 — 29/09/2026)

```python
_SPEAKER_EQ = (
    "highpass=f=70:p=2,"
    "equalizer=f=50:t=q:w=0.6:g=-5,"
    "equalizer=f=200:t=q:w=1.0:g=4,"
    "equalizer=f=400:t=q:w=1.1:g=3,"
    "equalizer=f=800:t=q:w=1.2:g=-1,"
    "equalizer=f=3200:t=q:w=1.3:g=6,"
    "equalizer=f=6000:t=q:w=1.2:g=3,"
    "treble=g=3.5:f=10000:w=0.7"
)
_SPEAKER_LIMIT = "alimiter=limit=0.841:level=disabled"  # -1.5 dBFS
```

**Đáp ứng tần số đo được (sine từng tần → MP3 160k → decode)** so với bản gốc,
trên chính bài DANHKA:

| Hz | v0 (gốc) | v1 (sai) | **v2 (nay)** |
|---|---|---|---|
| 50 | -12.5 | -18.5 | **-12.0** (giữ chống "u") |
| 80 | -5.0 | -13.2 | **-5.3** |
| 120 | -0.3 | -8.1 | **-0.9** |
| 200 | +4.1 | -2.3 | **+3.4** (trả lại thân giọng) |
| 300 | +5.2 | +1.0 | **+3.6** |
| 3200 | +2.0 | +3.9 | **+6.1** |
| 6000 | -0.1 | +0.0 | **+3.4** |
| 10000 | +0.3 | +2.2 | **+1.5** |

**Băng thông KHÔNG đổi**: MP3 40 s ra 801 644 B ở cả v0 và v2 (chênh 0 B).

## 2. Preset có sẵn

Đổi bằng `AUDIO_PRESET=<tên>` rồi restart server:

| Preset | Đặc điểm |
|--------|----------|
| `speaker` (mặc định) | Bù trừ loa nhỏ: cắt dải <100 Hz loa không dựng nổi, chuyển động lượng sang 250-500 Hz; giảm 800 Hz bớt "hộp", nhấn 3.2 kHz cho rõ tiếng |
| `flat` | Chỉ cắt sub-bass + chống clip, gần như nguyên bản |
| `warm` | Nhiều bass hơn (nhấn 200 Hz +6 dB) |
| `bright` | Nhiều treble hơn (nhấn 3.5 kHz +4 dB, 10 kHz +3 dB) |
| `loud` | `loudnorm` (to nhỏ đều giữa các bài) + EQ `speaker` |
| `none` | Không DSP (thô nhất, dễ rè nhất vì bass sâu làm loa rung) |

`AUDIO_FILTERS` (chuỗi `-af` tuỳ ý) **đè** preset nếu được set. Đặt `AUDIO_FILTERS=""`
để tắt hoàn toàn DSP.

**A/B nhanh**: `AUDIO_PRESET=warm` không cần deploy lại; `GET /health` trả
`audio.preset` + `audio.presets_available` để biết preset đang chạy.

## 3. Bitrate & sample rate — sự thật đo được

`AUDIO_BITRATE` mặc định **160k** (trước là 96k → 128k → 160k):
- Ở 24 kHz MP3 là **MPEG-2 LSF** nên `libmp3lame` **KẸP TRẦN ở 160 kbps**
  (xin 192k cũng chỉ ra 160k) — 160k là mức cao nhất có thể
- ⚠️ **Bitrate KHÔNG mở rộng được dải tần**: đo thực tế 96/128/160k đều bị cắt như
  nhau từ ~11 kHz trở lên, vì trần đó do **sample rate 24 kHz (Nyquist 12 kHz)**,
  không do bitrate
- Tăng 128k → 160k chỉ giảm artifact (méo lượng tử, pre-echo), không làm nhạc "sáng"
  hơn. Băng thông 16 → 20 KB/s

### Vì sao sample rate/channels/bitrate/filters thành env var
Trước đây server fix cứng `-ar 24000` → board nào có `AUDIO_OUTPUT_SAMPLE_RATE` khác
(vd 16 kHz) sẽ bị ESP32 resample bằng `esp_ae_rate_cvt` (complexity 2, perf_type SPEED)
— chất lượng thấp hơn resample ở server.

Nay: `AUDIO_SAMPLE_RATE`, `AUDIO_CHANNELS`, `AUDIO_BITRATE`, `AUDIO_FILTERS`
(mặc định khớp `bread-compact-wifi`: 24000 Hz, mono).

**Test env override đã chạy**:
```sh
AUDIO_SAMPLE_RATE=16000 AUDIO_CHANNELS=2 AUDIO_BITRATE=64k AUDIO_FILTERS=""
```
→ lệnh ffmpeg đổi đúng (`-ar 16000 -ac 2 -b:a 64k`, không còn `-af`).
## 4. Lịch sử 3 vòng chỉnh EQ (bài học quan trọng)

### Vòng 1 — "nghe mỏng" → boost trầm → bass vỡ

**Triệu chứng ban đầu**: cao tần rõ, bass "vỡ", remix có kick dày thì "rè rè".

**Thủ phạm là chính cái sửa lần trước**: thấy nghe "mỏng" nên thêm `+2.5 dB @110 Hz`
và `+3.5 dB @200 Hz` — hai dải đó nằm đúng vùng loa nhỏ không dựng nổi. Boost ở đó
không tạo bass nghe được, nó chỉ biến thành **hành động côn loa (excursion) → méo**.

**Đo trên bài thật**:

| | 30-60 Hz | 60-120 Hz | % năng lượng toàn bài |
|---|---|---|---|
| Bản cũ | −26.2 | −23.7 | **14.8%** |
| Bản mới | −28.1 | −26.1 | **10.9%** |

Dải 150 Hz–8 kHz giữ nguyên (−20.2 → −19.9) → cắt đúng chỗ thừa.

**Đã loại trừ codec** (giả thuyết thứ hai) bằng đo trước/sau encode MP3 160k trên
chính bài thật: mọi dải chỉ mất **0.3–0.4 dB** → codec không làm hỏng bass.

**Bài học**: không có gain nào vừa giữ được cả bài thường lẫn remix. Đổi mục tiêu từ
*bù* sang **cân bằng** — đưa bass xuống thấp hơn dải trung 1.9 dB thay vì nhô lên 3.7 dB.

### Vòng 2 — bài DANHKA "ù ù, giọng trầm không rõ"

Bài "Bắt con bướm vàng" (full 330 s). Đo FFT 40 s đầu, 467 đoạn phổ:

| Dải | % công suất |
|---|---|
| 40-60 Hz | **61.8%** |
| 50-70 Hz | 47.9% |
| 0-120 Hz | **88.3%** |
| 2-8 kHz (giọng người) | **2.2%** |

Tiếng "ồm ồm" của giọng nam trầm nằm đúng ở 50-70 Hz. Bài ở vòng 1 chỉ có 14.8% dưới
120 Hz → **bài khác nhau về đặc tính, một chuỗi filter không thể chiều cả hai**.

**Nguyên nhân vì sao `highpass f=100` vẫn "ù"**: highpass cắt dưới 100 Hz ở
12 dB/octave nên 50-70 Hz chỉ giảm ~3.5 dB, còn 100-120 Hz gần như nguyên yên.
Đồng thời lượng công suất không lối đó bị limiter bóp lại, thay thế mọi thứ ở 2-8 kHz
(2.2%) nên giọng người bị chìm hẳn.

**Đã loại trừ 3 giả thuyết khác trước khi kết luận**: không phải codec (MP3 160k chỉ
mất 0.3 dB), không phải lỗi DC (DC = -1.2, sạch), không phải thiếu headroom.

### Vòng 3 — v1 sửa "ù" nhưng "nghe tù, mất cao"

Sửa `highpass=f=100` → `f=60:p=2` + 2 equalizer Q hẹp ở 45/80 Hz. Kết quả: bấm "ù ù"
đi, nhưng robot báo **"nghe tù, mất cao"**.

**Nguyên nhân — do đáp ứng tần số, KHÔNG phải do băng thông**:

| Hz | v0 | v1 | |
|---|---|---|---|
| 120 | -0.3 | **-8.1** | ← cắt oan |
| 200 | +4.1 | **-2.3** | ← cắt oan |
| 300 | +5.2 | +1.0 | |
| 3200 | +2.0 | +3.9 | |
| 10000 | +0.3 | +2.2 | |

v1 **không làm mất cao** — 800 Hz trở lên đều TĂNG. Cái sai là cắt nhầm **120-300 Hz**,
đúng vùng **thân giọng ca sĩ**. Loa nhỏ dùng dải 80-150 Hz làm "cầu nối" để mọi tần số
cao hơn cộng hưởng; cắt mất nó thì cả phần trên cũng mất rung.

### ⚠️ Bài học (2 lần sai liên tiếp)

> Cả vòng 1 và vòng 2 (lần 1) đều **đo bằng năng lượng theo dải** rồi kết luận, và cả
> hai lần đều dẫn tới chính sai.
>
> Đo phổ tần số chỉ nói **"có bao nhiêu năng lượng ở đó"**, không nói **"thành phần đó
> bị sửa đổi bao nhiêu"**. **Chỉnh EQ thì đáp ứng tần số mới là thước đo đúng.**

Cách đo đúng đã dùng: **sine từng tần** qua chuỗi → MP3 160k → decode (trên chính bài
thật), so dB với bản không DSP.

## 5. Còn lại / chưa kiểm chứng được

- Các con số là **phép đo trên PC**. Loa 3W thật sự méo ở 60-120 Hz hay không thì chỉ
  nghe mới biết.
- Nếu vẫn rè: thêm `lowshelf` cắt thẳng thay vì EQ lồng nhau.
- Nếu nghe vẫn "tù": nguyên nhân có khả năng nằm ở **bản nén 128k của SoundCloud đã mất
  dải cao trước khi tới server** — khi đó hướng sửa là **đổi nguồn**, không phải EQ.

**Đã sửa một lời giải thích SAI trong code**: comment cũ ghi "bản mới không vượt ngưỡng
nên limiter gần như không tác động". Đo thật thì sai — peak sau EQ đều chạm trần 0 dBFS ở
**cả hai** bản (nguồn SC 128k vốn đã nén chặt) → limiter vẫn bóp ~1.5 dB như cũ.