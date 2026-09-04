# Firmware Knowledge Notes (TÀI LIỆU TÌM HIỂU - CHƯA KIỂM CHỨNG)

> **LƯU Ý QUAN TRỌNG:** Tài liệu này là kết quả **tìm hiểu/phân tích code** trong
> phiên làm việc, phục vụ tham khảo cho các yêu cầu sau này. Một số kết luận
> (đặc biệt phần liên quan server xiaozhi.me) **CHƯA được kiểm chứng thực tế**.
> Trước khi ứng dụng, cần test lại với thiết bị + server thật.

## 1. Kiến trúc hội thoại AI xiaozhi

- Luồng chuẩn: mic → audio stream → server (ASR) → LLM → TTS → speaker.
- Firmware KHÔNG chạy AI; mọi "trí tuệ" nằm ở server.
- Trạng thái thiết bị: Idle / Connecting / Listening / Speaking ...,
  chuyển qua `Application::SetDeviceState()` + state machine
  (xem `main/device_state_machine.*`, `docs/websocket.md`).

## 2. Hai kênh firmware → LLM (đã xác minh bằng code)

### 2.1. Kết quả MCP tool call (chắc chắn hoạt động)

- Handler tool chạy qua `McpServer::DoToolCall()` (main/mcp_server.cc dòng ~550)
  → `app.Schedule(...)` → chạy trên **main task**.
- `throw std::runtime_error("msg")` → `msg` trở thành **lỗi tool** mà LLM đọc
  được; `return "json/text"` → kết quả thành công. LLM diễn đạt lại thành lời nói.
- Ứng dụng thực tế: mép bàn → error text → AI nói "đang ở mép bàn, không đi được".
- **Timeout tool call:** firmware KHÔNG đặt deadline (ReplyResult chờ bao lâu
  cũng gửi được). Giới hạn thực tế:
  - Handler chạy trên MAIN TASK → không nên block > 2-3s (né task watchdog,
    ≥ 5s liên tục là nguy hiểm).
  - `vTaskDelay()` trong handler KHÔNG làm treo audio/wifi (task riêng), chỉ
    hoãn việc trong main task (display, state change, MCP call khác).
  - Poll loop `vTaskDelay(30ms) + đo cảm biến` = chờ + kiểm tra xen kẽ; delay
    thẳng 1 nhịp lớn thì KHÔNG kịp phát hiện sự kiện giữa chừng.
  - Server side: không xác nhận được con số từ repo; vài giây là an toàn.

### 2.2. `Protocol::SendWakeWordDetected(text)` — kênh text chủ động

- Code (`main/protocols/protocol.cc:67`): gửi gói
  `{"session_id":"...","type":"listen","state":"detect","text":"<wake_word>"}`.
- **Text của wake word được đưa vào LLM như lời người dùng** → AI "tự trả lời"
  khi hô "Hi LyLy" vì "Hi LyLy" chính là tin nhắn đầu tiên của hội thoại.
  (Ghi chú trong `docs/websocket.md` mục "Wake Word Detected" với ví dụ
  `"text": "Hi XiaoZhi"`.)
- → Firmware CÓ THỂ chủ động gửi text bất cứ lúc nào qua kênh này.
- ❌ **ĐÃ TEST THỰC TẾ (xong checklist #1): server xiaozhi.me TỪ CHỐI text tùy ý.**
  Gửi `SendWakeWordDetected("Hi LyLy, robot vừa phát hiện mép bàn...")` trong lúc
  hội thoại → server trả lỗi: *"Detect is only for wake words, do not send long
  texts"* (device hiện Alert ERROR + biểu cảm sad). Server chỉ chấp nhận text
  trùng wake word đã đăng ký ("Hi,Lily" / "Hi,莉莉").
  → Kênh này chỉ dùng được cho mục đích đánh thức bằng wake word thật.
  Muốn firmware chủ động gửi nội dung cho LLM: phải tự host server hoặc dùng
  kênh khác. (Code `Application::SendRobotAlert` + `MotorController::SetWakeNotifier`
  vẫn giữ, đã tắt gọi trong `compact_wifi_board.cc`, ghi chú lý do tại chỗ.)

## 3. Cơ chế wake word & vòng lifecycle "thức - ngủ"

- Idle: `EnableWakeWordDetection(true)` — luôn nghe wake word.
- Phát hiện → `Application::BeginWakeWordInvoke/ContinueWakeWordInvoke`
  (application.cc ~880) → mở audio channel → gửi audio wake word +
  `SendWakeWordDetected(text)` → Listening.
- Thời gian chờ im lặng rồi ngủ (AI nói "có thể bạn đang bận, tôi sẽ trò
  chuyện sau"):
  - **KHÔNG nằm trong firmware** (đã soát `application.cc`: `clock_ticks_`
    chỉ dùng cho status bar + debug heap, không có timer nghe).
  - Do **server** quyết định (~10 giây theo quan sát, chưa xác minh con số).
  - Câu "có thể bạn đang bận..." là TTS do server sinh ra, không có trong firmware.
  - Firmware chỉ có thể làm robot **ngủ sớm hơn** (tự StopListening/Idle sau
    N giây không có tiếng — dùng VAD `OnVadStateChange` của AudioService),
    KHÔNG kéo dài được.
  - Chỉnh được toàn quyền chỉ khi **tự host server** (dự án mã nguồn mở
    `xiaozhi-esp32-server` có config timeout này).

## 4. API firmware chủ động kích hoạt hội thoại (đã có sẵn)

> **Cập nhật đã kiểm chứng (code có thật):** thêm hook
> `Board::OnWakeWordDetected()` (virtual no-op, `main/boards/common/board.h`)
> được gọi từ `Application::HandleWakeWordDetectedEvent()` (application.cc
> ~832) — board có actuator có thể override để phản hồi vật lý khi nghe wake
> word (vd `MotorController::Wiggle()` của bread-compact-wifi).

| API | Ý nghĩa |
|---|---|
| `Application::ToggleChatState()` | Mở/kết thúc phiên chat (như bấm nút BOOT) |
| `Application::StartListening()` | Bắt đầu nghe mic (như giữ nút Touch) |
| `Protocol::SendWakeWordDetected(text)` | Giả lập wake word + gửi text lên LLM |
| `Application::Schedule()` | Bắt buộc bọc khi gọi từ task khác (callback có thể chạy ngoài main task) |

- Kết hợp truyền thống: expose trạng thái qua tool (vd `self.get_device_status`
  hoặc tool riêng) để LLM "tra" được lý do (cliff, pin...) sau khi được đánh thức.
- Anti-spam: khi kích hoạt AI từ cảm biến cần flag "chỉ báo cáo 1 lần" / cooldown.

## 5. Chiến lược đang dùng cho robot (mép bàn / ToF)

- Mỗi lệnh `move`: đo ToF 1 lần trước khi chạy; `forward` bị chặn nếu
  khoảng cách > `kNoFloorMm` (30mm) → error text → AI cảnh báo.
- Giữ tool call trong lúc động cơ chạy (~1s, auto-stop 1000ms) để AI kịp
  đọc kết quả tại đúng lượt.
- Sự kiện ngoài lượt tool call (rơi khi đứng yên, pin yếu...) → dùng kênh 2.2
  (cần test) hoặc `ToggleChatState` + tool trạng thái.

## 6. Việc cần làm khi ứng dụng (checklist)

1. ✅ ~~Test `SendWakeWordDetected` với text tùy ý trên server đang dùng~~
   — **XONG, KẾT LUẬN: server TỪ CHỐI** ("Detect is only for wake words, do
   not send long texts"). Xem chi tiết mục 2.2. Tính năng "robot tự bắt chuyện
   bằng text tùy ý" KHÔNG làm được trên server này → cần tự host server.
2. Xác nhận timeout tool call thực tế của server (gọi tool chờ ~2-3s xem có OK).
3. Nếu cần kéo dài/đổi câu chờ im lặng → cân nhắc tự host xiaozhi-esp32-server.
4. Anti-spam khi kích hoạt AI từ cảm biến (flag chỉ báo cáo 1 lần / cooldown).

## 7. Fact về server xiaozhi.me (quan sát từ trang quản lý)

- Tính cách/vai trò AI chỉnh bằng prompt trực tiếp trên xiaozhi.me — đổi không
  cần build lại firmware.
- Có thể hướng dẫn AI gọi tool theo yêu cầu qua system prompt.
- Giới hạn prompt ~2000 từ (quan sát; chưa xác minh con số chính thức).
