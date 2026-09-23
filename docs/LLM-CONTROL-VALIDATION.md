# Nghiệm thu bộ quản lý LLM — 23/09/2026

## Kết quả đã kiểm chứng

- Docker Compose build thành công; migration PostgreSQL `0003_llm_control` đã áp dụng. Bản sao trước migration: `results/before-llm-control.dump`.
- Toàn bộ suite: **70/70 test pass**. Sau thay đổi đánh thức job chờ quota, chạy lại **29/29 test quản lý LLM pass**.
- Mock bao phủ chọn key/round-robin, quota nhóm, hai worker tranh phần quota cuối, phân loại 429/403/503, backoff/Retry-After, circuit và một probe, fallback, giới hạn tổng 4 lượt, ngân sách, timeout, hủy/thử lại và che secret.
- Web không nhận giá trị API key. Database lưu tham chiếu; credential hiển thị đuôi đã che. Test kiểm tra không lộ secret trong response/error/UI.
- UI tích hợp dùng PostgreSQL riêng `news_tool_ui_test`, credential tổng hợp và worker mock, không gọi API thật: job 3 gặp Gemini 503, chờ khoảng 5 giây rồi DeepSeek thành công. Hai attempt được ghi; bản nháp 4 giữ snapshot 1, ghi provider thực tế DeepSeek và trạng thái `needs_review`.
- Đặt giới hạn nội bộ bằng số lượt đã dùng cho cả hai nhóm mock: job 4 vào `waiting_quota`, `next_attempt_at = NULL`, chưa gửi request. Restart cả web/worker mock rồi kiểm tra: trạng thái chờ, lịch sử attempt và bản 2 đã duyệt vẫn còn; không phát sinh lượt gọi.
- Prompt/schema dùng cho tạo kịch bản không thay đổi. Kết quả thành công tiếp tục phải qua review.

## Smoke API thật và phần chưa đạt

Kết quả chi tiết: [results/llm-control-live-smoke.json](results/llm-control-live-smoke.json).

- Gemini, model `gemini-3.8-flash`: connection-test job 3 gửi đúng **1 request**, nhận **HTTP 503**, ghi `service_error`, kết thúc thất bại theo giới hạn một lượt của thử kết nối. Không gửi tiếp hoặc fallback trong smoke.
- DeepSeek: thiếu secret nên **không gửi request**.
- Dashboard Google AI Studio yêu cầu đăng nhập. Chưa đối chiếu được quota/usage thực tế; lỗi 503 không có usage nên không thể kết luận chi phí bằng 0.
- Vì vậy **chưa xác nhận credential/model hoạt động thành công với API thật**, chưa nghiệm thu đối chiếu dashboard. Cần cung cấp secret DeepSeek tại máy chủ, đăng nhập dashboard và thử kết nối lại với số lượt nhỏ khi Gemini khả dụng.

## Vận hành và giới hạn

Ứng dụng chính giữ fallback **tắt**; fallback chỉ bật trên dữ liệu mock. Bật fallback đồng nghĩa cho phép provider tiếp theo nhận cùng snapshot nguồn. Cấu hình provider được phép, thứ tự và ngân sách trước khi bật.

Số lượt quan sát chỉ tính request được bộ quản lý mới đặt trước, không đại diện quota tài khoản và không bao gồm công cụ bên ngoài/historical request. Không có mặc định 20 lượt/ngày. Quota không biết thời điểm reset sẽ chờ xác nhận vận hành. Chi phí đặt trước là ước tính cấu hình, không thay thế hóa đơn nhà cung cấp; giới hạn tiền mặc định chưa bật, giới hạn lượt/token vẫn áp dụng.

Không tự gửi lại timeout hoặc request đang gửi khi worker bị ngắt: `unknown_outcome` cần người vận hành cân nhắc trước khi thử lại. Migration không hỗ trợ downgrade phá lịch sử; phục hồi bằng backup khi cần.

Xem [hướng dẫn quản lý LLM](README-LLM-CONTROL.md) để thêm env/Docker secret, quản lý quota nhóm, thử kết nối và phục hồi job.
