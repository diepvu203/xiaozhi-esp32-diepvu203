# MCP Server Nhạc Việt cho Xe Robot XiaoZhi — Cloud Stream Proxy

Server MCP tìm nhạc trên **SoundCloud + YouTube** (qua `yt-dlp`) và phục vụ
**file MP3 đã tải sẵn qua HTTP** (FastAPI `FileResponse` / `StreamingResponse`),
gộp cùng MCP server (`search_song`, `get_song_url`) trong **một process, một
cổng** (`/mcp` streamable-http + `/stream/<id>.mp3` + `/health`).
**Preload xuống đĩa**: `get_song_url` tải + transcode nền vào cache
(`<tmp>/zing-music/<id>.mp3`, tối đa 20 bài) — robot mở URL là nhận file có
`Content-Length`, không còn pipe sống bị đứt giữa chừng (nguyên nhân hat dut
trước đây). Robot chỉ nhận 1 URL stream duy nhất và tự stream qua WiFi.

## Tool cung cấp

| Tool | Input | Output |
|------|-------|--------|
| `search_song` | `keyword` (tên bài / ca sĩ) | Tối đa 5 kết quả: `title`, `uploader`, `duration_sec`, `source`, `youtube_url` (xếp theo độ khớp tên, ưu tiên SoundCloud) |
| `get_song_url` | `title` | Trả NGAY `{"status":"ready","stream_url":"<PUBLIC_BASE>/stream/<id>.mp3"}` — không cần poll/chờ |
| `search_radio` | `keyword` (tên / quốc gia / chủ đề) | Tối đa 8 trạm: `name`, `country`, `tags`, `bitrate`, `codec`, `hls`, `url` (mọi nguồn MP3/AAC/HLS đều nhận — server transcode) |
| `get_radio_url` | `name` (tên trạm) | Trả NGAY `{"status":"ready","stream_url":"<PUBLIC_BASE>/radio/<id>.mp3"}` — radio là stream liên tục. **Lưu ý: lúc phát nhạc/radio thì micro bị tắt theo thiết kế firmware** (`music_playing_` trong `audio_service.cc` tắt codec input để wake-word không cắt ngang) — không gọi tiếp bằng giọng được; muốn đổi bài/dừng thì **nhấn nút wakeup** trên robot (board gọi `MusicPlayer::Stop()`) rồi nói tiếp |
| `/radio/<id>.mp3` | route FastAPI | Stream MP3 sống (`StreamingResponse` vô hạn): primer MP3 im ≤2.5s → audio radio transcode về 24 kHz mono (qua DSP `AUDIO_FILTERS`); **luôn 200, bỏ qua `Range`** (radio không có byte offset để resume) |

Cơ chế: `get_song_url` bắt đầu **preload nền** ngay (resolve googlevideo +
tải chunk 1 MB có resume + transcode MP3 CBR 160k — xem `AUDIO_BITRATE` —
ghi vào cache đĩa). Khi robot (hoặc VLC) mở `/stream/<id>.mp3`: file có sẵn
→ trả ngay `FileResponse` (`Content-Length`); chưa xong → trả `200` ngay và
gửi **silence primer** (MP3 im cùng sample-rate/channels/bitrate, 8 KB, pace ~real-time)
để kết nối không chết (firmware pre-buffer 64 KB, timeout đọc 15 s), rồi
chuyển sang toàn bộ file. Tải lỗi / quá 240 s → fallback pipe `ffmpeg`
trực tiếp (hành vi cũ, giảm cấp).

## Cấu hình (biến môi trường)

| Biến | Ý nghĩa |
|---|---|
| `PORT` | Cổng HTTP. Mặc định 8080 (Render tự set) |
| `MCP_TRANSPORT` | `stdio` — chạy qua `mcp_pipe.py` kết nối `MCP_ENDPOINT` (run.bat và Render đều dùng). `streamable-http` — serve `POST /mcp` trực tiếp (mặc định trong server.py nếu không set) |
| `PUBLIC_BASE` | Base URL công khai, vd `https://xiaozhi-music.onrender.com`. Bỏ trống khi chạy laptop → tự dùng `http://<IP-LAN>:<PORT>` |
| `MCP_ENDPOINT` | URL "Điểm cuối MCP" từ xiaozhi.me (cho `mcp_pipe.py`) |
| `AUDIO_SAMPLE_RATE` | Rate gửi xuống robot. Mặc định `24000` — phải khớp `AUDIO_OUTPUT_SAMPLE_RATE` của board để ESP32 không resample thêm |
| `AUDIO_CHANNELS` | Số kênh. Mặc định `1` (loa robot là mono) |
| `AUDIO_PRESET` | Bộ lọc DSP bù trừ loa. Mặc định `speaker`. Xem bảng dưới |
| `AUDIO_BITRATE` | Bitrate MP3. Mặc định `160k` — mức cao nhất libmp3lame cho phép ở 24 kHz (MPEG-2 LSF) |
| `AUDIO_FILTERS` | Chuỗi `ffmpeg -af` tuỳ ý, **đè** preset khi được set. `""` = tắt DSP |
| `MUSIC_CACHE_DIR` | Thư mục cache bài hát đã tải. Mặc định `<tmp>/zing-music` |
| `MUSIC_CACHE_MAX_FILES` | Số bài giữ trong cache (mỗi bài ~1–12 MB). Mặc định `20` |
| `YTDLP_COOKIES_B64` | base64 của `cookies.txt` (Netscape) cho yt-dlp — phương án khi PO token chưa đủ. Xem mục bot-check |
| `YTDLP_PROXY` | Proxy cho yt-dlp + bước tải source (nên **sticky/residential**, không xoay IP liên tục) |
| `YTDLP_PLAYER_CLIENTS` | Ladder player client: tier cách nhau `\|`, client trong tier cách nhau `,`, tier rỗng = client mặc định của yt-dlp. Mặc định `\|tv,web_embedded,tv_downgraded\|mweb,tv_simply,web\|android_vr,android,ios` (tier đầu để rỗng = yt-dlp tự chọn client) |
| `YTDLP_DEBUG` | `1` = in cả message `[debug]` của yt-dlp (mặc định chỉ info/warn/err) |
| `YTDLP_FORMAT_PROBE` | `0` = tắt bước thử tải 64 KB từ direct URL trước khi coi resolve thành công (mặc định `1`). Giữ bật để robot không nghe silence primer chỉ vì URL 403 |
| `YTDLP_SOURCES` | Thứ tự nguồn nhạc, cách nhau `,`. Mặc định `soundcloud,youtube` — **SoundCloud là nguồn chính** |
| `YTDLP_RESOLVE_CANDIDATES` | Số ứng viên mỗi nguồn thử thêm khi ứng viên đầu hỏng (mặc định `2`) |
| `YTDLP_SOURCE_COOLDOWN` | Giây bỏ qua nguồn vừa gặp bot-check (mặc định `900`). `0` = tắt |

### Nguồn nhạc: SoundCloud (chính) + YouTube (dự phòng) — tự động chuyển

`YTDLP_SOURCES` mặc định `soundcloud,youtube`. **SoundCloud là nguồn chính**
vì nó không dùng cơ chế nào của YouTube: không cookies, không PO token,
không bị bot-check theo IP (đã verify end-to-end: search → resolve → tải →
MP3 4.5 MB trong 13s, hoàn toàn không auth). YouTube đứng sau làm dự phòng
cho bài chỉ có trên đó và là nguồn cần cookies/POT khi IP bị gắn cờ.

Lý do đổi thứ tự: cookies/proxy/POT chỉ giữ được vài bài rồi hỏng (Google
revoke session khi IP đổi), nên phụ thuộc vào chúng là rủi ro dịch vụ. Với
SoundCloud làm nguồn chính, YouTube bị chặn hoàn toàn vẫn hát được.

- Nguồn vừa gặp bot-check bị bỏ qua `YTDLP_SOURCE_COOLDOWN` giây (mặc định
  15 phút) → request sau không mất thêm 10–20s chờ một nguồn đang chết.
- Mỗi nguồn thử tối đa `YTDLP_RESOLVE_CANDIDATES` video, xếp hạng theo độ
  khớp tên (bỏ dấu) — tránh trúng video cover/1 phút rồi bỏ cả bài.
- SoundCloud ưu tiên format **progressive** (`http_mp3`) thay vì HLS: đi
  `_fetch_chunked` + `_transcode_local` (nhanh, resume được) thay vì
  `_download_ffmpeg` trên m3u8 (đo thật: m3u8 mất >60s và phải reconnect,
  progressive xong trong 13s).
- Xem nguồn đang bật / đang cooldown: `GET /health` → `sources`.

#### Vì sao không dùng Zing MP3?

Đã thử thật, ba rào chặn độc lập:

1. **Search API trả `-403`** — `{"err":-403,"msg":"You don't have permission"}`.
   apiKey hard-code trong extractor đã bị Zing thu hồi, Zing không công bố key
   mới. Không search được thì không tìm được bài theo tên.
2. **Bài VIP** — resolve bài thật ra
   `The song is only for VIP accounts`; phần lớn nhạc Vpop là VIP.
3. **Geo-restricted VN** — `_GEO_COUNTRIES=['VN']` trong extractor, cần
   cookies để qua; Render đặt ở US nên thêm một tầng chặn nữa.

Chỉ dùng được khi biết sẵn URL Zing + bài free + có cookies VN — không
phù hợp làm nguồn nhạc tự động cho robot.

Server in ra dòng `[music] audio: 24000 Hz x1, 160k mp3, filters='...'` khi khởi động để bạn biết cấu hình đang chạy.

### Log chẩn đoán (đọc khi có sự cố)

- `[ytdlp:warn] ...` — warning của yt-dlp được bơm thẳng ra log (trước đây bị
  `no_warnings=True` ẩn mất). Đây là chỗ thấy client nào bị bot-check, client
  nào thiếu PO token, client nào bị bỏ qua.
- `[music] client tier '<list>' bị chặn: ...` — tier đó fail ở mức client
  (bot-check / thiếu token) → server tự nhảy sang tier kế tiếp
  (`YTDLP_PLAYER_CLIENTS`).
- `[music] resolve OK qua tier '<list>' (tier đã fail: ...)` — tier thắng.
- `[music] resolved '<title>' (client=..., formats_client=[...],
  format_id=..., abr=...)` — client Innertube thật sự sinh ra format đang dùng.
- `[music] preload OK '<title>' (N B, Xs = resolve A + tải B (...) +
  transcode C, cycle n, client=...)` — **chia thời gian theo công đoạn**:
  dùng số này để biết nút thắt nằm ở mạng (resolve/tải) hay CPU (transcode)
  thay vì tối ưu nhầm chỗ.
- `[music] format probe OK (206, 4096 B, 0.3s)` / `format probe HTTP 403` —
  bước thử tải 64 KB từ direct URL sau khi resolve. URL không tải được thì
  tier đó bị bỏ (`tier '...' cho URL khong tai duoc -> thu tier ke tiep`) và
  thử tier client kế tiếp — nhờ vậy robot không rơi vào cảnh chỉ nghe
  silence primer rồi im.
- `[music] search youtube=3, soundcloud=0` — số kết quả mỗi nguồn (giá trị
  chữ `cooldown` = nguồn đang bị bỏ qua tạm).
- `[music] nguon 'youtube' that bai sau 12s` → `[music] resolve OK
  nguon='soundcloud' sau 4s (2/2 nguon)` — YouTube hỏng, đã tự sang nguồn
  sau. Nếu thấy `đang cooldown` ở request sau thì nguồn đó vừa bị chặn.
- `[music] soundcloud OK '<title>' (format_id=http_mp3_1_0)` — nguồn dự phòng
  thành công.
- Chạy `python verify_resolve_ladder.py` để test lại ladder + route
  (offline, không cần mạng).

### Chất lượng âm thanh

Thứ tự ảnh hưởng thực tế: **âm sắc (EQ) > méo/clip > bitrate**. Trần cứng: 24 kHz → Nyquist 12 kHz, và loa nhỏ trên board không tái tạo được sub-bass.

**Chi phí CPU của filter ≈ 0** (đo trên laptop dev, 120 s audio, chuỗi đầy đủ
decode AAC → EQ + alimiter → MP3 24 kHz mono): **0,55 s có filter** vs
**0,86 s không filter**; encode 5 phút ở 96k/128k/160k đều ~2,3 s. Nút thắt
thời gian nằm ở mạng (resolve/tải) và CPU của instance, **không phải ở filter**
— đừng xoá EQ để "tăng tốc", nó chỉ đổi âm sắc.

**Preset (`AUDIO_PRESET`)** — đổi preset rồi restart là nghe khác ngay, nên cứ thử A/B:

| Preset | Đặc điểm | Đáp tuyến đo được (24 kHz mono, chỉ EQ chứ không limiter/codec) |
|---|---|---|
| `speaker` (mặc định) | Bù trừ loa nhỏ: cắt mạnh dải loa không dựng nổi, dồn sang 250–500 Hz (loa thực sự phát được) → ấm mà **không vỡ bass**; giảm 800 Hz bớt "hộp", nhấn 3.2 kHz cho rõ tiếng | 50 Hz **−12.0** · 80 Hz **−4.6** · 100 Hz −1.8 · 200 Hz **+4.5** · 250 Hz **+5.7** · 400 Hz **+4.2** · 800 Hz −1.2 · 3.2 kHz **+2.4** · 10 kHz +0.8 |
| `flat` | Gần như nguyên bản, chỉ cắt sub-bass + chống clip | 90 Hz −3 · còn lại ~0 |
| `warm` | Bass nhiều hơn (nhấn 200 Hz +6 dB) | 200 Hz **+5.8** |
| `bright` | Treble nhiều hơn (3.5 kHz +4, 10 kHz +3) | 3.5 kHz **+3.5** · 10 kHz **+1.7** |
| `loud` | To/nhỏ đều giữa các bài (`loudnorm`) + EQ `speaker` | như `speaker`, mức to được chuẩn hoá |
| `none` | Không DSP. Thô nhất và **dễ rè nhất** (bass sâu làm màng loa rung) | 0 dB toàn dải |

> **Vì sao `speaker` cắt 30–100 Hz mạnh thế.** Loa 3W không dựng nổi dải đó;
> giữ lại chỉ biến thành hành động côn loa (excursion) → méo, nghe "vỡ/rè", và
> bất kỳ mức boost nào ở 60–200 Hz cũng *tăng* hiệu ứng đó. Đo trên bài thật,
> phần năng lượng trong 30–120 Hz giảm từ **14.8% → 10.9%** toàn bài, còn dải
> 150 Hz–8 kHz giữ nguyên. Bản cũ (nhấn +2.5 dB @110 Hz, +3.5 dB @200 Hz) vì
> thế nghe ấm với bài nhạc thường nhưng vỡ với remix bass mạnh. Nếu nghe bài nào
> bị "mỏng", hãy A/B `AUDIO_PRESET=warm`

Preset nào cũng kết thúc bằng `alimiter=limit=0.841` (−1.5 dBFS) để PCM sau khi giải mã không bị clip khi nhân software volume trong firmware.

**Bitrate (`AUDIO_BITRATE`)** — 128k → 160k. Đo lại trên tín hiệu nhạc tổng hợp 20 s @24 kHz (SNR so với PCM gốc):

| Cấu hình | Bitrate thực | SNR |
|---|---|---|
| CBR 128k | 129k | 18.5 dB |
| **CBR 160k** | **161k** | **18.6 dB** |
| VBR `-q:a 0` (V0) | 73k | 15.6 dB |
| VBR `-q:a 2` (V2) | 55k | 13.3 dB |
| CBR 160k + `-cutoff 11000` | 161k | 16.9 dB (tệ hơn) |

→ Giữ **CBR 160k**, không dùng VBR (VBR ở container này tụt xuống 55–73 kbps), không set `-cutoff`. 160k là **trần** của libmp3lame ở 24 kHz: xin `192k` cũng chỉ ra 160k. Băng thông 20 KB/s (robot pre-buffer 4 s ≈ 80 KB).

**Board 16 kHz** (ESP32-S3 SuperMini...) → `AUDIO_SAMPLE_RATE=16000` + `AUDIO_BITRATE=64k`.

### Ví dụ chỉnh

| Muốn gì | Cách làm |
|---|---|
| Sáng/thoáng hơn | `AUDIO_PRESET=bright` |
| Bass nhiều hơn | `AUDIO_PRESET=warm` |
| Nghe thử bản "nguyên bản" | `AUDIO_PRESET=flat` |
| Tự chỉnh EQ | `AUDIO_FILTERS=highpass=f=90,equalizer=f=250:t=q:w=1:g=5,alimiter=limit=0.841:level=disabled` |
| Tắt hết DSP | `AUDIO_PRESET=none` hoặc `AUDIO_FILTERS=` |

## Cài đặt (laptop dev, 1 lần)

```powershell
pip install -r requirements.txt
```

## Chạy trên laptop (dev)

Double-click `run.bat` (đã dán MCP_ENDPOINT vào trong; `run.bat` tự set
`MCP_TRANSPORT=stdio`) hoặc:

```powershell
cd d:\xiaozhi\xiaozhi-esp32-diepvu203\tools\zing-music-mcp
$env:MCP_ENDPOINT = "<URL Điểm cuối MCP>"
$env:MCP_TRANSPORT = "stdio"   # MCP stdio qua mcp_pipe; HTTP stream chạy nền
python mcp_pipe.py server.py
```

- Web xiaozhi.me hiện **Đã kết nối** + 2 tool là OK.
- Windows Firewall lần đầu hỏi **Allow** cho Python port 8080 (Private).

## Deploy lên Render.com (server 24/7, robot chạy WiFi nào cũng hát được)

Server gộp MCP + stream vào **một process trên Render**: `mcp_pipe.py` kết
nối OUT tới `MCP_ENDPOINT` (wss api.xiaozhi.me) để đăng ký 2 tool nhạc 24/7;
`server.py` chạy stdio, thread nền phục vụ `/health` + `/stream/<id>.mp3`.

1. Push cả repo lên GitHub. Có 2 cách:
   - **Blueprint (khuyến nghị):** Render → New → Blueprint → chọn repo
     (dùng `render.yaml` ở **gốc repo**, trỏ `./Dockerfile` gốc).
   - **Web Service:** New Web Service → Docker → dùng `Dockerfile` ở gốc repo
     (COPY `tools/zing-music-mcp/*`).
2. Environment variables (dashboard Render):
   - `MCP_ENDPOINT` = link Điểm cuối MCP `wss://api.xiaozhi.me/mcp/?token=...`
     lấy từ xiaozhi.me — **BẮT BUỘC** (token xoay vòng thì cập nhật lại rồi restart).
   - `PUBLIC_BASE` = `https://<tên-service>.onrender.com` — **BẮT BUỘC** (không
     set thì stream URL trả IP nội bộ container, robot không tải được nhạc).
   - `MCP_TRANSPORT` = `stdio` (`render.yaml` đã set).
3. Deploy: log phải thấy `Successfully connected to WebSocket server`, vào
   xiaozhi.me thấy 2 tool quay lại, `GET /health` trả `{"ok":true}`.
4. Lưu ý:
   - Gói **free ngủ sau ~15 phút không traffic** → khi ngủ tool biến mất;
     chặn bằng cron ping `/health` mỗi 10 phút (cron-job.org) hoặc upgrade
     Starter (~$7/tháng).
   - YouTube chặn IP datacenter → xem mục **"YouTube chặn 'Sign in to confirm
     you're not a bot'"** bên dưới.

## YouTube chặn "Sign in to confirm you're not a bot"

IP datacenter của Render bị YouTube gắn cờ → bước `_resolve` (lấy direct URL)
thất bại, log thấy đúng câu lỗi này. `search_song` vẫn chạy (extract_flat
không bị check) nên robot "tìm được bài nhưng không phát được".

**Cách xử lý đầu tiên (mặc định, không cần làm gì):** server tự chuyển sang
**SoundCloud** — xem mục "Nguồn nhạc: YouTube + SoundCloud" ở trên. Chỉ khi
cả hai nguồn đều hỏng thì mới cần đọc tiếp phần dưới.

### Giải pháp lâu dài (đã tích hợp): PO token — không cần export cookies

**Tại sao cookies "chỉ hát được vài bài rồi chết":** Google revoke session
ngay khi cookies bị dùng từ IP **không cố định** (proxy xoay IP) hoặc IP
datacenter bị gắn cờ — không phải cookies "hết hạn theo tuổi". Export lại
liên tục chỉ là chữa triệu chứng.

Dockerfile đã tích hợp **bgutil PO-token provider** — cơ chế chính thức của
yt-dlp để vượt "Sign in to confirm you're not a bot" *không cần cookies*:

- `requirements.txt` cài plugin pip `bgutil-ytdlp-pot-provider==2.0.0`
  (yt-dlp tự discover provider `bgutil:http`, không cần cấu hình).
- `start.sh` boot server POT chạy nền của bgutil tại `127.0.0.1:4416`
  (dùng deno — đã có sẵn trong image), **trước** khi `mcp_pipe` chạy.
- Plugin tự gắn PO token vào request resolve; nếu server POT chết thì
  yt-dlp chỉ log warning và chạy như cũ (không crash service).

Kiểm tra sau deploy (Render log):

1. Boot: `[music] bgutil PO-token plugin 2.0.0 OK -> ...`,
   `[start] bgutil POT server ready (pid ...)` và
   `[music] bgutil POT server /ping khi boot: up`.
2. Thử hát **liên tiếp nhiều bài** — không còn lỗi
   "Sign in to confirm you're not a bot".
3. Khi POT đã hoạt động ổn định: **xóa `YTDLP_COOKIES_B64`** trên Render
   (Environment → Remove → Save) để khỏi phải export cookies mỗi ngày.
   Để lại cookies cũng được, nhưng không còn bắt buộc.

Nếu VẪN còn lỗi bot-check dù đã có POT → đọc trường `pot_srv=` trong log
`resolve give-up` (hoặc `GET /health` → `pot_server`) rồi xử lý theo:

- **`pot_srv=down`** — server POT không trả lời `/ping` lúc yt-dlp cần
  token (nghi deno bị OOM-kill trên instance 512MB): xem log `[start]`
  (WARNING hoặc ready muộn), restart service; lặp lại → tăng RAM instance.
- **`pot_srv=up`** — PO token đi được nhưng Google vẫn chặn. Server luôn bật
  warning của yt-dlp, nên **đọc các dòng `[ytdlp:warn]` phía trên** để biết
  client nào fail:
  1. **Xóa `YTDLP_COOKIES_B64`** rồi test — session cookies bị revoke có
     thể gây hard-block "Sign in" ngay cả khi đã có PO token.
  2. Thử nhóm client **không cần PO token**:
     `YTDLP_PLAYER_CLIENTS='|tv,web_embedded,tv_downgraded'`. Nhóm này không đi
     qua BotGuard nên loại được biến "PO token"; nếu **cả nhóm này cũng
     fail** thì IP datacenter đã bị gắn cờ → cần cookies mới hoặc proxy
     sticky/residential sạch.
  3. Vì sao ladder mặc định xếp như vậy (theo yt-dlp đang pin trong
     `requirements.txt`): `visionos`, `tv`, `web_embedded` **không cần** PO
     token; `mweb`, `tv_simply`, `web` cần token web (bgutil sinh được);
     `android_vr`, `android`, `ios` cần token Android/iOS mà bgutil **không**
     sinh được → xếp cuối. Tên client không tồn tại trong yt-dlp (ví dụ
     `tv_embedded`) chỉ bị bỏ qua kèm warning, không có tác dụng.

Sửa bằng cookies (phương án dự phòng, khi PO token chưa dùng được):

1. **Export cookies** từ máy đang đăng nhập YouTube (chọn 1 cách):
   - Extension **"Get cookies.txt LOCALLY"** (Chrome/Edge/Firefox) → mở
     youtube.com → Export → được `cookies.txt` (định dạng Netscape).
   - Hoặc CLI (trên máy đã đăng nhập):
     ```powershell
     yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download https://www.youtube.com
     ```
2. **Encode base64** (PowerShell, thực hiện trong thư mục chứa cookies.txt):
   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\cookies.txt"))
   ```
3. **Self-check base64 trước khi paste vào Render** — byte đầu PHẢI là `35`
   (`0x23` = `'#'`). Nếu là `239` (`0xEF`) là **UTF-8 BOM** — chính BOM gây
   lỗi `does not look like a Netscape format cookies file` (Python đọc file
   ở text mode, BOM thành ký tự ẩn `U+FEFF` bám trước `# Netscape...` →
   regex magic của `http.cookiejar` không match → `LoadError`):
   ```powershell
   $b64 = '<dán base64 vào đây>'
   $d = [Convert]::FromBase64String($b64)
   $d[0]                              # 35 = OK; 239 = có BOM
   [Text.Encoding]::UTF8.GetString($d, 0, 30)   # ẨN BOM -> nhìn "đúng" nhưng chưa đủ!
   ```
   Nếu file có BOM thì strip trước khi encode lại:
   ```powershell
   $d = [IO.File]::ReadAllBytes("$PWD\cookies.txt")
   if ($d[0] -eq 0xEF -and $d[1] -eq 0xBB -and $d[2] -eq 0xBF) { $d = $d[3..($d.Length-1)] }
   [Convert]::ToBase64String($d)
   ```
   (Server cũng tự strip UTF-8 BOM lúc boot — self-check để chắc file không
   hỏng theo cách khác.)
4. Render dashboard → **Environment** → thêm biến `YTDLP_COOKIES_B64` =
   dán chuỗi base64 (biến secret, `sync:false` trong render.yaml — **KHÔNG
   commit nội dung cookies vào git**) → Save → service tự redeploy.
5. Kiểm tra: `GET /health` phải thấy `"cookies": true` — nghĩa là server
   đã decode **VÀ** validate qua Netscape header (file hỏng → server từ chối
   và trả `"cookies": false`, lý do chi tiết nằm trong log Render: dòng
   `YTDLP_COOKIES_B64 rejected: ...`). Rồi thử yêu cầu hát.
6. Cookies hết hạn / YouTube xoay session → export lại + cập nhật env
   (bỏ qua bước này nếu đã dùng PO token ở trên).

Phương án phụ: có proxy IP sạch thì set `YTDLP_PROXY=http://user:pass@host:port`
trên dashboard (không cần cookies).

Trên laptop (IP nhà) **không cần cookies** — resolve trực tiếp vẫn chạy.

**Lỗi "Requested format is not available":** Dockerfile đã cài **deno** (JS
runtime cho yt-dlp) — không có JS runtime thì YouTube **drop formats** ở một
số video và yt-dlp báo đúng lỗi này; `server.py` cũng có retry ladder
(`bestaudio` → `best` → default). Nếu vẫn gặp với 1 video cụ thể thì video đó
có thể bị giới hạn tuổi / không khả dụng — bảo robot thử bài khác.

## Cấu hình prompt trên xiaozhi.me (Vai trò)

```
Khi tôi xin phát nhạc hoặc tìm nhạc: nếu chưa rõ bài nào thì gọi
search_song để liệt kê và hỏi tôi chọn; khi đã chốt bài thì gọi
get_song_url (trả về ngay) rồi gọi self.music.play với stream_url.
Nếu lỗi thì báo tôi. Không tự phát nhạc khác khi tôi không yêu cầu.

Khi tôi xin mở radio/phát đài: gọi search_radio liệt kê trạm MP3,
chốt tên thì gọi get_radio_url rồi play stream_url.
LƯU Ý QUAN TRỌNG: lúc đang phát nhạc/radio thì micro của robot bị tắt
(theo thiết kế firmware để wake-word không cắt ngang) — không ra lệnh
bằng giọng được. Nếu tôi muốn đổi bài/dừng mà robot không nghe, hãy
hướng dẫn tôi NHẤN NÚT WAKEUP trên robot để dừng phát rồi nói tiếp.
```

## Kiến trúc & giới hạn

```
MODE 1 — Cloud (Render, MCP_TRANSPORT=stdio, 24/7, không cần laptop):

  MCP: xiaozhi.me ◀──wss── mcp_pipe.py (trên Render) ──stdio── server.py
  Robot ──GET /stream──▶ https://<tên-service>.onrender.com/stream/<id>.mp3
                         (thread nền: /health + /stream trên PORT Render)

MODE 2 — Local dev (run.bat, laptop mở):

  MCP: xiaozhi.me ◀──wss── mcp_pipe.py (laptop) ──stdio── server.py
  Robot ──GET /stream──▶ http://<IP-laptop>:8080/stream/<id>.mp3
```

- **Đã test**: `/health` OK; `/stream/<id>.mp3` trả MP3 hợp lệ — cache sẵn
  thì có `Content-Length` ngay; đang preload thì nhận silence primer rồi
  chuyển mượt sang file. Tải trước về đĩa, không còn stream pipe sống.
- **Đã test** (laptop dev, yt-dlp 2026.8.19): `/stream` trả primer sau ~0,1 s
  ngay cả khi preload chưa resolve xong (route không còn resolve đồng bộ →
  hết cảnh extract 2 lần); ladder client tự nhảy tier và log tier thắng /
  tier bị chặn; dòng `resolved (... client=..., formats_client=[...])` và
  `preload OK (... = resolve A + tải B + transcode C)` ra đúng số đo.
- Direct URL googlevideo bị khóa theo IP + hết hạn → luôn stream qua server
  (IP của server quyết định); không trả direct URL cho robot.

## Firmware (Giai đoạn 3 — ĐÃ LÀM, đã chạy được)

- `main/audio/music_player.*` trên ESP32: stream URL HTTP(S) → decode MP3
  bằng **`esp_audio_dec`** (KHÔNG phải `esp_audio_simple_dec` — simple decoder
  chỉ hỗ trợ WAV/M4A/TS/OGG, không có MP3) → resample về 24 kHz mono → loa qua
  `AudioService::PushPcmToPlaybackQueue`.
- MCP tool `self.music.play(url, name)` / `self.music.stop` /
  `self.music.get_state` đã đăng ký trong `compact_wifi_board.cc`.
- Khi nhạc phát, TTS downlink bị mute (`AudioService::SetMusicPlaying`) để
  không xen tiếng robot nói.
- Cần **build + nạp firmware** mới hát được; server nhạc phải đang chạy.
- Trên web xiaozhi.me sẽ thấy **5 tool**: `search_song`, `get_song_url`
  (từ server) + `self.music.play/stop/get_state` (từ firmware).
- ⚠️ Nếu web chỉ hiện 2 tool: reset Điểm cuối MCP + flash firmware mới nhất
  (firmware phải là bản có 3 tool `self.music.*`).
- ⚠️ Laptop nên để WiFi **2.4GHz cùng băng với robot** (5GHz↔2.4GHz làm TCP
  handshake chập chờn → `Failed to open connection`).
