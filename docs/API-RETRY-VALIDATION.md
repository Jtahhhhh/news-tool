# Chuẩn hóa API và retry — nghiệm thu 23/09/2026

## Triển khai

Adapter chung tách dựng request, gửi HTTP một lần và parse response. Gemini dùng `responseMimeType: application/json` cùng `responseJsonSchema`; schema nghiệp vụ và prompt 1.0 không đổi. Công cụ lab cũng bỏ retry HTTP riêng.

Job mới chốt nguồn ngay lúc xếp hàng, prompt, schema, timeout, output limit, policy và payload cho từng route được cho phép. Retry dùng payload đã lưu, chỉ thay thông tin xác thực khi cơ chế chọn credential cho phép. Đổi model/prompt/schema cần yêu cầu mới; job cũ không tự nhận cấu hình mở rộng. Idempotency nội bộ không bảo đảm nhà cung cấp chỉ tính phí một lần.

Mặc định tối đa **6 lượt gửi**, backoff **15, 30, 60, 120, 120 giây** có jitter trong giới hạn 120 giây; cửa sổ retry **900 giây** từ lần đặt trước đầu tiên. Retry-After hỗ trợ số giây/ngày giờ HTTP và không bị cắt ngắn; nếu vượt cửa sổ thì dừng, không gửi sớm. Giới hạn token/USD có thể dừng trước lượt thứ 6. Job giữ policy đã chụp; migration chỉ cập nhật policy cho job mới, không nâng trần job cũ.

Mặc định **1 request đồng thời/provider/nhóm quota/model**, kiểm tra và đặt trước dưới cùng khóa transaction. Key cùng project phải cùng nhóm. Unknown outcome/hủy sau gửi giữ slot tới hết lease ban đầu. Circuit chỉ cho một probe; 503 giữ cùng key. Fallback vẫn mặc định tắt.

Mỗi attempt lưu ID, UTC, endpoint/model, latency, HTTP status, request ID nếu có, hash payload/prompt/schema/snapshot/config, mã lỗi provider, field violations và header chẩn đoán allowlist. Không lưu Authorization/request headers. Lỗi HTTP, rỗng, bị cắt, từ chối, parse và validation được phân biệt. Raw output đã che secret lưu trong PostgreSQL, chỉ mở trong trang review/job local; không đưa vào API danh sách attempt. Hệ thống local chưa có RBAC; raw output/snapshot phải được bảo vệ cùng database và backup.

## Kiểm thử tự động

- **77/77 test ứng dụng pass**, PostgreSQL riêng `news_tool_test`.
- **20/20 test lab pass**, gồm toàn bộ schema/parser trước đây; cập nhật kỳ vọng đúng payload Gemini và một lần gửi HTTP.
- **36/36 test quản lý LLM/truy vết pass lại** sau bổ sung giữ key khi chờ và tương thích job cũ.
- Test mock **503 × 5 → 200 ở lần 6** đi qua HTTP MockTransport: đúng 6 request, cùng payload hash/snapshot/prompt/schema, bản nháp `needs_review`.
- Có test hết số lượt/cửa sổ thời gian, Retry-After dài hơn cửa sổ/ngày HTTP, quota, invalid key, timeout, circuit, đồng thời qua hai transaction, slot unknown outcome, lịch retry qua kết nối DB mới và phục hồi tiến trình worker trong suite hiện có.
- Test response/API/UI không lộ key; lỗi không có usage ghi “chưa xác định chi phí”.

## Smoke API thật — tách biệt với mock

Chạy đúng **một job Gemini**, model `gemini-3.8-flash`, nguồn giả lập thư viện, target 15 giây, output limit 8192, fallback tắt; database riêng `news_tool_smoke_test`. Không gọi DeepSeek trong đợt này. Công cụ `tests/live_request_smoke.py` dùng idempotency cố định để tránh tạo thêm job khi chạy lại.

| Attempt | HTTP | Latency | Kết quả |
|---|---|---|---|
| 1 | 503 | 3,956 giây | UNAVAILABLE, chờ khoảng 16 giây |
| 2 | 503 | 2,846 giây | UNAVAILABLE, chờ khoảng 31,5 giây |
| 3 | 200 | 8,541 giây | Schema hợp lệ, draft, needs_review |

Ba lần giữ cùng credential, model và tất cả hash. Tổng thời gian job khoảng **63,7 giây**. API không cung cấp request ID. Lần thành công trả usage: **493 input + 449 output + 1.445 thinking = 2.387 total tokens**. Hai lỗi 503 không có usage: **không kết luận chi phí bằng 0**, chưa đối chiếu hóa đơn/dashboard.

Báo cáo máy đọc: [results/request-trace-live-smoke.json](results/request-trace-live-smoke.json). Raw output và lịch sử vẫn trong database smoke. Kết quả này xác nhận cấu hình gọi được API, **không chứng minh** schema gây ra/hết gây ra 503 hoặc tỷ lệ thành công dài hạn. Không chạy A/B vì không có lỗi schema tái hiện; không dùng một thành công để kết luận nguyên nhân quá tải.

## Đối chiếu nội dung và review

Tiêu đề, hook, caption, lời đọc, chữ trên màn hình và hai quote đều khớp dữ kiện nguồn: phòng đọc 120 chỗ, phục vụ các ngày trong tuần. Không tự thêm ngày đăng hay “hôm nay”.

**Một lỗi nội dung của output gốc:** `scenes[1].visual_brief` ghi lịch “thứ Hai đến thứ Sáu”, trong khi nguồn chỉ ghi “các ngày trong tuần”. Đã sửa thủ công bằng màn hình review thành hình minh họa bạn đọc, không gán lịch cụ thể; lưu **bản biên tập mới**, giữ nguyên bản model gốc và lịch sử. Bản sửa vẫn **needs_review**, không tự duyệt. Đây là bằng chứng validator cấu trúc không đủ để bảo đảm mọi nội dung đúng.

Nguồn là fixture giả lập nên cả hai bản không dùng để xuất bản. Tổng 15 giây là số model đề xuất, chưa đo bằng audio/TTS. Chưa chấm benchmark văn phong hay triển khai video.

## Vận hành

Migration `0004` đã bổ sung dữ liệu truy vết; backup trước triển khai: `results/before-request-trace.dump`. Compose web/worker dùng chung image, chỉ cổng web mở ở loopback; database giữ named volume. Có giao diện review smoke riêng tại `http://localhost:8002/scripts/1` (không có worker tự chạy ở database này).

Docker Desktop ban đầu lỗi hai socket tạm. Đã lưu lại thư mục socket dưới tên `*-recovery-*` trong LocalAppData rồi khởi động lại; không reset Docker hoặc xóa volume.

Xem [hướng dẫn vận hành LLM](README-LLM-CONTROL.md). Tham khảo hợp đồng API: [Gemini GenerateContent](https://ai.google.dev/api/generate-content), [DeepSeek error codes](https://api-docs.deepseek.com/quick_start/error_codes/).
