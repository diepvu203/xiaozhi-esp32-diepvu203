#ifndef __MOTOR_CONTROLLER_H__
#define __MOTOR_CONTROLLER_H__

#include "mcp_server.h"

#include <esp_timer.h>
#include <esp_log.h>
#include <driver/gpio.h>
#include <functional>
#include <atomic>
#include <utility>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

// Điều khiển 2 động cơ DC (N20) qua module L298N.
// Mỗi động cơ dùng 2 chân IN (IN1/IN2, IN3/IN4).
// ENA/ENB của L298N được nối mức cao cố định (tốc độ không đổi).
//
// Thêm lệnh di chuyển mới:
//   1. Thêm giá trị vào enum MoveAction
//   2. Thêm case tương ứng trong ApplyAction()
//   3. Cập nhật mô tả tool "self.motor.move" bên dưới
class MotorController {
public:
    enum class MoveAction {
        kStop,
        kForward,
        kBackward,
        kTurnLeft,
        kTurnRight,
        kForwardUntilStop,   // Chạly tới iên tục đến khi nhận "stop" (tối đa kMaxContinuousMs)
        kBackwardUntilStop,  // Chạy lùi liên tục đến khi nhận "stop" (tối đa kMaxContinuousMs)
    };

    // Giới hạn an toàn cho lệnh chạy liên tục (forward_until_stop / backward_until_stop)
    static constexpr uint32_t kMaxContinuousMs = 10000;

    // Ngưỡng chống rơi: khoảng cách (mm) xuống sàn. Đọc > kNoFloorMm (hoặc
    // kErrorMm do lỗi) = mất sàn -> chặn lệnh tiến. mm = 0 (sát bàn) vẫn an toàn.
    // 30mm: nhạy hơn để bảo vệ tốt hơn (robot chỉ cần nghiêng nhẹ/nhô mép là dừng).
    static constexpr uint16_t kNoFloorMm = 30;
    // Giá trị trả về khi cảm biến không đọc được
    static constexpr uint16_t kErrorMm = 8191;
    // Chu kỳ kiểm tra sàn khi motor đang chạy tiến (chỉ đo khi đang chạy)
    static constexpr uint32_t kMonitorPollMs = 30;
    // Thời gian lùi lại khi phát hiện mất sàn giữa chừng
    static constexpr uint32_t kBackOffMs = 200;
    // Giới hạn PHIÊN chạy: LLM hay gửi nhiều lệnh forward nối tiếp (mỗi lệnh
    // tự auto-stop 1s) khiến robot chạy dài. Gộp các lệnh cách nhau < kRunGapMs
    // thành 1 phiên, tối đa kMaxRunSessionMs rồi từ chối lệnh mới.
    // LƯU Ý: kRunGapMs phải LỚN HƠN round-trip của 1 lệnh MCP (LLM suy nghĩ +
    // network + chạy 1s + trả kết quả, thực tế ~1-3s) — nếu nhỏ hơn thì mỗi
    // lệnh bị tính là phiên mới và giới hạn không bao giờ kích hoạt.
    static constexpr uint32_t kMaxRunSessionMs = 3000;
    static constexpr uint32_t kRunGapMs = 5000;
    // Cooldown lắc lư: wake word thường bắn nhiều sự kiện (Idle + abort
    // Speaking) nên trước đây robot lắc 2 lần. Sau khi lắc, bỏ qua hết trong
    // khoảng thời gian này.
    static constexpr uint64_t kWiggleCooldownMs = 10000;

    // auto_stop_ms: thời gian chạy tối đa mỗi lệnh di chuyển thông thường trước khi tự dừng.
    // Đặt 0 để chạy liên tục cho đến khi nhận lệnh "stop".
    MotorController(gpio_num_t left_in1, gpio_num_t left_in2,
                    gpio_num_t right_in1, gpio_num_t right_in2,
                    uint32_t auto_stop_ms = 1000) :
        left_in1_(left_in1), left_in2_(left_in2),
        right_in1_(right_in1), right_in2_(right_in2),
        auto_stop_ms_(auto_stop_ms) {
        if (left_in1_ == GPIO_NUM_NC || left_in2_ == GPIO_NUM_NC ||
            right_in1_ == GPIO_NUM_NC || right_in2_ == GPIO_NUM_NC) {
            return;
        }

        uint64_t pin_mask = (1ULL << left_in1_) | (1ULL << left_in2_) |
                            (1ULL << right_in1_) | (1ULL << right_in2_);
        gpio_config_t config = {
            .pin_bit_mask = pin_mask,
            .mode = GPIO_MODE_OUTPUT,
            .pull_up_en = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        ESP_ERROR_CHECK(gpio_config(&config));
        Stop();

        esp_timer_create_args_t timer_args = {
            .callback = [](void* arg) {
                static_cast<MotorController*>(arg)->Stop();
            },
            .arg = this,
            .dispatch_method = ESP_TIMER_TASK,
            .name = "motor_auto_stop",
            .skip_unhandled_events = true,
        };
        ESP_ERROR_CHECK(esp_timer_create(&timer_args, &auto_stop_timer_));

        RegisterMcpTools();
    }

    // Đăng ký hàm đọc khoảng cách xuống sàn (mm) của cảm biến ToF.
    // Task giám sát sẽ đo cảm biến NHƯNG chỉ khi motor đang chạy tiến; khi robot
    // đứng yên hoặc lùi/xoay thì không đo (tiết kiệm, không cần thiết).
    // Ngoài ra, khi nhận lệnh di chuyển sẽ đo 1 lần trước khi chạy.
    // Trả về kErrorMm (8191) khi không đọc được / không có sàn.
    void SetDistanceReader(std::function<uint16_t()> read_mm) {
        distance_reader_ = std::move(read_mm);
        // Chạy task giám sát sàn. Task NÀY chỉ đo khi motor đang chạy tiến
        // (moving_forward_ == true), nên khi robot đứng yên thì không đo gì cả.
        if (monitor_task_ == nullptr) {
            xTaskCreate([](void* arg) {
                static_cast<MotorController*>(arg)->CliffMonitorLoop();
            }, "motor_floor_monitor", 3072, this, 5, &monitor_task_);
        }
    }

    // Phản hồi lắc lư khi nghe thấy wake word. Chạy trong task one-shot riêng
    // để không block main task (không cản luồng mở audio channel). Giữ
    // moving_forward_ = false nên không đụng cơ chế chống rơi / auto-stop.
    // Đăng ký callback thông báo sự kiện (vd: phát hiện mép bàn giữa chừng)
    // để board chủ động gửi text lên AI qua Application::SendRobotAlert().
    void SetWakeNotifier(std::function<void(const std::string&)> cb) {
        wake_notifier_ = std::move(cb);
    }

    void Wiggle() {
        int64_t now = esp_timer_get_time();
        if (wiggling_.exchange(true)) {
            return;  // đang lắc, bỏ qua
        }
        if (last_wiggle_us_ != 0 && now - last_wiggle_us_ < (int64_t)kWiggleCooldownMs * 1000) {
            wiggling_ = false;  // trong cooldown: bỏ qua các sự kiện wake word phụ
            return;
        }
        last_wiggle_us_ = now;
        xTaskCreate([](void* arg) {
            auto* self = static_cast<MotorController*>(arg);
            constexpr uint32_t kStepMs = 50;  // lắc nhanh: 50ms mỗi hướng
            for (int i = 0; i < 1; ++i) {     // chỉ 1 nhịp trái-phải
                self->SetMotor(self->left_in1_, self->left_in2_, -1);
                self->SetMotor(self->right_in1_, self->right_in2_, 1);
                vTaskDelay(pdMS_TO_TICKS(kStepMs));
                self->SetMotor(self->left_in1_, self->left_in2_, 1);
                self->SetMotor(self->right_in1_, self->right_in2_, -1);
                vTaskDelay(pdMS_TO_TICKS(kStepMs));
            }
            self->SetMotor(self->left_in1_, self->left_in2_, 0);
            self->SetMotor(self->right_in1_, self->right_in2_, 0);
            self->wiggling_ = false;
            vTaskDelete(nullptr);
        }, "motor_wiggle", 2048, this, 5, nullptr);
    }

private:
    gpio_num_t left_in1_;
    gpio_num_t left_in2_;
    gpio_num_t right_in1_;
    gpio_num_t right_in2_;
    uint32_t auto_stop_ms_;
    esp_timer_handle_t auto_stop_timer_ = nullptr;

    // Reader khoảng cách xuống sàn (mm)
    std::function<uint16_t()> distance_reader_;
    // Task giám sát sàn (chỉ đo khi moving_forward_ = true)
    TaskHandle_t monitor_task_ = nullptr;
    std::atomic<bool> moving_forward_{false};
    // Cờ chống chồng 2 lần lắc lư (wake word phát hiện liên tiếp)
    std::atomic<bool> wiggling_{false};
    // Thời điểm lệnh di chuyển gần nhất / bắt đầu phiên chạy (microseconds)
    int64_t last_command_us_ = 0;
    int64_t run_start_us_ = 0;
    // Thời điểm lắc lư gần nhất (microseconds), dùng cho cooldown
    int64_t last_wiggle_us_ = 0;
    // Callback thông báo sự kiện cho board (gửi text lên AI)
    std::function<void(const std::string&)> wake_notifier_;
    // Chỉ thông báo mép bàn 1 lần cho đến khi có lệnh tiến thành công kế tiếp
    std::atomic<bool> cliff_notified_{false};

    void SetMotor(gpio_num_t in1, gpio_num_t in2, int direction) {
        // direction: 1 = tiến, -1 = lùi, 0 = dừng
        gpio_set_level(in1, direction > 0 ? 1 : 0);
        gpio_set_level(in2, direction < 0 ? 1 : 0);
    }

    void CancelAutoStop() {
        if (auto_stop_timer_ != nullptr) {
            esp_timer_stop(auto_stop_timer_);
        }
    }

    void ScheduleAutoStop() {
        if (auto_stop_ms_ > 0 && auto_stop_timer_ != nullptr) {
            esp_timer_start_once(auto_stop_timer_, auto_stop_ms_ * 1000);
        }
    }

    void Stop() {
        SetMotor(left_in1_, left_in2_, 0);
        SetMotor(right_in1_, right_in2_, 0);
        moving_forward_ = false;
        CancelAutoStop();
    }

    void Forward() {
        SetMotor(left_in1_, left_in2_, 1);
        SetMotor(right_in1_, right_in2_, 1);
        moving_forward_ = true;
        cliff_notified_ = false;  // cho phép thông báo lại nếu lại gặp mép bàn
        ScheduleAutoStop();
    }

    void Backward() {
        SetMotor(left_in1_, left_in2_, -1);
        SetMotor(right_in1_, right_in2_, -1);
        moving_forward_ = false;
        ScheduleAutoStop();
    }

    void TurnLeft() {
        SetMotor(left_in1_, left_in2_, -1);
        SetMotor(right_in1_, right_in2_, 1);
        moving_forward_ = false;
        ScheduleAutoStop();
    }

    void TurnRight() {
        SetMotor(left_in1_, left_in2_, 1);
        SetMotor(right_in1_, right_in2_, -1);
        moving_forward_ = false;
        ScheduleAutoStop();
    }

    // Chạy liên tục: auto-stop sau kMaxContinuousMs nếu không nhận được "stop"
    void ForwardUntilStop() {
        SetMotor(left_in1_, left_in2_, 1);
        SetMotor(right_in1_, right_in2_, 1);
        moving_forward_ = true;
        if (auto_stop_timer_ != nullptr) {
            esp_timer_start_once(auto_stop_timer_, kMaxContinuousMs * 1000);
        }
    }

    void BackwardUntilStop() {
        SetMotor(left_in1_, left_in2_, -1);
        SetMotor(right_in1_, right_in2_, -1);
        moving_forward_ = false;
        if (auto_stop_timer_ != nullptr) {
            esp_timer_start_once(auto_stop_timer_, kMaxContinuousMs * 1000);
        }
    }

    // Giám sát sàn trong lúc motor đang chạy TIẾN. Task này chỉ đo cảm biến khi
    // moving_forward_ = true; khi đứng yên / lùi / xoay thì không đo gì.
    // Nếu mất sàn giữa chừng -> dừng ngay + lùi một đoạn để không rơi khỏi mép.
    void CliffMonitorLoop() {
        while (true) {
            vTaskDelay(pdMS_TO_TICKS(kMonitorPollMs));
            if (!moving_forward_.load() || !distance_reader_) {
                continue;
            }
            uint16_t mm = distance_reader_();
            if (mm > kNoFloorMm) {
                // Debounce: 1 mẫu vượt ngưỡng có thể là rung/nghiêng khi chạy
                // (đã gặp thực tế: giữa bàn đọc 55mm trong khi trước đó 0mm).
                // Cần 2 mẫu liên tiếp mới xác nhận mất sàn.
                vTaskDelay(pdMS_TO_TICKS(kMonitorPollMs));
                uint16_t mm2 = distance_reader_();
                if (mm2 <= kNoFloorMm) {
                    continue;  // mẫu sau ổn -> báo động giả, bỏ qua
                }
                ESP_LOGW("MotorCtrl", "Cliff during move: %d/%d mm, stopping", mm, mm2);
                Stop();  // moving_forward_ = false -> task ngừng đo tiếp
                // Lùi lại một đoạn để rời xa mép bàn, tránh rơi
                SetMotor(left_in1_, left_in2_, -1);
                SetMotor(right_in1_, right_in2_, -1);
                vTaskDelay(pdMS_TO_TICKS(kBackOffMs));
                Stop();
                // Chủ động báo cho AI (test kênh text tùy ý qua
                // SendWakeWordDetected). Chỉ gửi 1 lần đến lệnh tiến kế tiếp.
                if (!cliff_notified_.exchange(true) && wake_notifier_) {
                    wake_notifier_("Hi LyLy, robot vừa phát hiện mép bàn nên đã dừng lại!");
                }
            }
        }
    }

    // Đo khoảng cách (mm) xuống sàn ngay trước khi chạy. Trả về kErrorMm nếu
    // không có reader hoặc cảm biến không đọc được.
    uint16_t MeasureDistanceBeforeMove() {
        if (distance_reader_) {
            return distance_reader_();
        }
        return kErrorMm;
    }

    void ApplyAction(MoveAction action) {
        switch (action) {
            case MoveAction::kForward:
                Forward();
                break;
            case MoveAction::kBackward:
                Backward();
                break;
            case MoveAction::kTurnLeft:
                TurnLeft();
                break;
            case MoveAction::kTurnRight:
                TurnRight();
                break;
            case MoveAction::kForwardUntilStop:
                ForwardUntilStop();
                break;
            case MoveAction::kBackwardUntilStop:
                BackwardUntilStop();
                break;
            case MoveAction::kStop:
            default:
                Stop();
                break;
        }
    }

    static bool ParseAction(const std::string& name, MoveAction& action) {
        if (name == "forward") {
            action = MoveAction::kForward;
        } else if (name == "backward") {
            action = MoveAction::kBackward;
        } else if (name == "left") {
            action = MoveAction::kTurnLeft;
        } else if (name == "right") {
            action = MoveAction::kTurnRight;
        } else if (name == "forward_until_stop") {
            action = MoveAction::kForwardUntilStop;
        } else if (name == "backward_until_stop") {
            action = MoveAction::kBackwardUntilStop;
        } else if (name == "stop") {
            action = MoveAction::kStop;
        } else {
            return false;
        }
        return true;
    }

    void RegisterMcpTools() {
        auto& mcp_server = McpServer::GetInstance();

        PropertyList properties;
        // Required string property: forward / backward / left / right / stop
        properties.AddProperty(Property("action", kPropertyTypeString));
        mcp_server.AddTool(
            "self.motor.move",
            "Drive the robot wheels. Valid actions: 'forward' (move forward briefly), "
            "'backward' (move backward briefly), 'left' (rotate left in place), "
            "'right' (rotate right in place), "
            "'forward_until_stop' (keep moving forward until told to stop), "
            "'backward_until_stop' (keep moving backward until told to stop), "
            "'stop' (stop both motors). Use 'stop' to end a *_until_stop action",
            properties,
            [this](const PropertyList& properties) -> ReturnValue {
                std::string action_name = properties["action"].value<std::string>();
                MoveAction action;
                if (!ParseAction(action_name, action)) {
                    throw std::runtime_error("Unknown action: " + action_name +
                        ". Valid actions: forward, backward, left, right, "
                        "forward_until_stop, backward_until_stop, stop");
                }
                // Chống chạy dài: LLM đôi khi gửi nhiều lệnh di chuyển nối tiếp
                // (mỗi lệnh tự auto-stop 1s) làm robot chạy rất lâu. Gộp các
                // lệnh cách nhau < kRunGapMs thành 1 phiên, quá kMaxRunSessionMs
                // thì từ chối để robot nghỉ. Lệnh stop reset phiên.
                if (action != MoveAction::kStop) {
                    int64_t now = esp_timer_get_time();
                    if (last_command_us_ != 0 &&
                        now - last_command_us_ > (int64_t)kRunGapMs * 1000) {
                        run_start_us_ = now;  // nghỉ đủ lâu -> phiên chạy mới
                    }
                    if (run_start_us_ == 0) {
                        run_start_us_ = now;
                    }
                    if (now - run_start_us_ > (int64_t)kMaxRunSessionMs * 1000) {
                        throw std::runtime_error(
                            "Robot has been moving continuously for too long. "
                            "It needs to rest. Wait a moment before asking it to move again");
                    }
                    last_command_us_ = now;
                } else {
                    run_start_us_ = 0;
                    last_command_us_ = 0;
                }
                // Đo khoảng cách 1 lần trước bất kỳ lệnh di chuyển nào (không đo liên tục).
                if (action != MoveAction::kStop && distance_reader_) {
                    uint16_t mm = MeasureDistanceBeforeMove();
                    ESP_LOGI("MotorCtrl", "ToF distance before move: %d mm", mm);
                    // Chặn các lệnh tiến khi mất sàn, để LLM đọc lỗi và cảnh báo.
                    bool forward = (action == MoveAction::kForward ||
                                    action == MoveAction::kForwardUntilStop);
                    if (forward && mm > kNoFloorMm) {
                        throw std::runtime_error(
                            "Cannot move forward: table edge detected (cliff). "
                            "Please do not go forward. Try backward, left or right instead");
                    }
                } else if (!distance_reader_) {
                    ESP_LOGW("Motion", "No floor sensor; cliff check skipped");
                }
                ApplyAction(action);
                return "{\"action\": \"" + action_name + "\", \"status\": \"ok\"}";
            });
    }
};

#endif // __MOTOR_CONTROLLER_H__