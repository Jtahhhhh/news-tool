# Quản lý credential, quota và fallback

Bản cập nhật API/retry mới: [nghiệm thu 0004, smoke thật và giới hạn](API-RETRY-VALIDATION.md). Mặc định một request đồng thời cho mỗi nhóm quota/model; raw output và hash cấu hình xem tại chi tiết job. Giới hạn số lượt hiển thị theo policy đã chụp của từng job, không áp dụng ngược trần mới cho job cũ.

Trang vận hành: http://localhost:8000/llm. Chạy `docker compose up --build -d` để khởi động; migration 0004 chạy trước web, worker chờ web healthy. Dữ liệu quản lý nằm trong PostgreSQL named volume như dữ liệu tin.

## Thêm nhiều key mà không đưa secret vào database/UI

1. Copy `.env.credentials.example` thành `.env.credentials`, nhập giá trị **trên máy chủ**. Ví dụ tên biến `LLM_KEY_GEMINI_A`, `LLM_KEY_GEMINI_B`; không nhập key vào chat, form UI hoặc Git. File chỉ được cấp cho worker.
2. Tạo lại worker sau khi thay biến môi trường: `docker compose up -d --force-recreate worker`.
3. Trong **Cấu hình LLM → Thêm tham chiếu credential**, nhập `env:LLM_KEY_GEMINI_A`, provider, project/account, model được phép, quota group và mức ưu tiên.
4. Key cùng project phải cùng quota group. Các key khác project nhưng thực tế dùng chung tài khoản/quota cũng phải đặt chung group theo dashboard nhà cung cấp. Hệ thống không tự suy ra project từ chuỗi key.
5. Worker cập nhật đuôi đã che và trạng thái đọc secret trong khoảng 30 giây. `ready` chỉ có nghĩa worker đọc được secret, **chưa xác nhận quyền/quota**. **Thử kết nối** dùng dữ liệu giả lập, tối đa một lần gửi, không fallback, phải đánh dấu cảnh báo có thể tốn quota/chi phí.

Tham chiếu mặc định `env:GEMINI_API_KEY` và `env:DEEPSEEK_API_KEY` tiếp tục dùng key ở `script-api-lab/.env` hoặc `.env`; `.env.credentials` được áp dụng sau cùng ở worker. Giá trị rỗng cũng ghi đè giá trị trước đó. Web không đọc các env_file này: chỉ nhận danh sách cấu hình không chứa LLM secret qua Compose.

Docker secrets: xem `compose.secrets.example.yaml`. Tạo secret file trong `secrets/`, mount **chỉ worker** tại `/run/secrets/llm_ten`, nhập tham chiếu `secret:llm_ten` trong UI. Tên file phải có tiền tố `llm_`; không nhận đường dẫn tùy ý/symlink. Key và file secrets bị loại khỏi build context và Git. Không đưa override/file secret lên kho công khai.

Database chỉ giữ tham chiếu và metadata, cùng đuôi tối đa 4 ký tự; không giữ bản mã hoặc bản rõ của key. Metadata credential đã có lượt gửi không được đổi provider/ref/group để tránh làm mất hiệu lực bộ đếm; có thể bổ sung project, sửa tên/ưu tiên/model hoặc bật/tắt. Nếu cần thay key, đổi giá trị secret tại cùng ref và tạo lại worker. Log/output được che các key đã resolve và biến key được allowlist.

Đây là công cụ local, cổng mặc định chỉ bind `127.0.0.1`; trang quản trị chưa có RBAC/tài khoản riêng. Không mở công khai trước khi thêm xác thực/phân quyền phù hợp.

## Chọn key và đặt trước quota

- Lọc credential đang bật, có secret, đúng provider/model. Số ưu tiên nhỏ hơn trước; cùng mức chọn credential ít được dùng gần đây nhất (round-robin).
- `quota_group + provider + model` dùng chung một bộ đếm. Billing hết số dư còn chặn nhóm cho mọi model (`model=*`). Thêm key cùng nhóm không tăng số lượt cho phép.
- Khóa transaction PostgreSQL `721003` tuần tự hóa việc chọn key, đặt trước quota và chuyển trạng thái circuit. Network chạy ngoài transaction. Nhiều worker không lấy trùng lượt cuối hoặc cùng thực hiện probe phục hồi.
- `observed_calls` là **tổng số lượt đặt trước để gửi của hệ thống từ migration này**, gồm lượt lỗi/timeout và trường hợp crash ngay trước network. Không phải usage/quota thật; không bao gồm ứng dụng khác, lịch sử trước migration hoặc request gọi trực tiếp ngoài hàng đợi.
- `configured_limit` mặc định trống, không tự gán 20/ngày. Nếu đặt `window_seconds`, đây là cửa sổ giới hạn **nội bộ** tính từ mốc lưu trong DB; không đại diện thời điểm reset của provider. Không đặt cửa sổ thì chạm giới hạn sẽ chờ xử lý thủ công.
- Quota ngày được xác nhận qua cấu trúc `google.rpc.QuotaFailure` có quota ID theo ngày; không đoán từ mọi HTTP 429. Khi chưa có thông tin reset đáng tin cậy, nhóm bị chặn vô thời hạn. Thời gian cửa sổ nội bộ không tự gỡ cờ hết quota thực.
- Sau khi đối chiếu dashboard, **Mở lại nhóm** bắt đầu cửa sổ nội bộ mới, giữ nguyên `observed_calls`. Job waiting_quota được đánh thức để kiểm tra lại mọi giới hạn; không bỏ qua circuit, budget hay key đã tắt. Nếu nhiều scope đang chặn (ví dụ model cụ thể và `*`), cần kiểm tra/mở từng scope thích hợp.

## Lỗi và trạng thái job

| Lỗi | Hành vi |
|---|---|
| 500/502/503/504 | Retry exponential từ 15 giây, tối đa 120 giây + jitter trong giới hạn, giữ credential khi retry cùng provider; theo dõi circuit provider/model |
| 429 chưa xác nhận theo ngày | Chờ nhóm theo giới hạn tốc độ; không tắt key, không mặc định hết quota ngày |
| 429 quota ngày xác nhận / 402 billing | Chặn nhóm, không đoán reset; chọn nhóm độc lập khác hoặc fallback nếu cho phép, nếu hết lựa chọn thì waiting_quota |
| Key invalid xác nhận | Tắt credential; báo kiểm tra. 403 thông thường không đủ để tắt key |
| Thiếu quyền/model/request sai | Lỗi cấu hình, job failed; không tự lặp |
| Timeout/mạng/crash sau đánh dấu gửi | unknown_outcome, không tự gửi lại hoặc fallback; giữ phần đặt trước |
| Output bị cắt/từ chối/sai schema | Lưu chẩn đoán, không fallback để né kiểm tra nội dung |

HTTP `Retry-After` được tôn trọng, kể cả dài hơn 24 giờ. Khoảng chờ thực là tối đa giữa backoff+jitter và Retry-After. Nếu Retry-After vượt cửa sổ retry 15 phút, job kết thúc thất bại, không gửi sớm để né giới hạn. Worker ghi `next_attempt_at`, xử lý job khác thay vì giữ process ngủ chờ retry. Không có thời điểm thử lại thì `next_attempt_at=NULL` và không bị polling worker liên tục.

Circuit mặc định mở sau 3 lỗi server liên tiếp trong 60 giây; quản trị cấu hình được. Sau thời gian ngắt chỉ một request được làm probe (half_open). Thành công đóng circuit; probe lỗi lại tạm ngừng; probe chết được phục hồi theo lease. Thành công của request cũ đã gửi trước khi mở circuit không tự đóng circuit đang mở.

Job có `queued/running/retry_wait/waiting_quota/succeeded/failed/unknown_outcome`. Hủy trước gửi thành failed + cancelled; hủy sau gửi thành unknown_outcome vì không thu hồi được HTTP và phí. **Thử lại job** không xóa lịch sử/bộ đếm, không vượt 6 sends, vẫn qua quota/circuit. Với unknown_outcome phải chủ động xác nhận khả năng bị tính phí lặp. Hết 6 lần phải tạo yêu cầu mới rõ ràng.

## Fallback và ngân sách

Mặc định **tắt fallback**. Quản trị chọn thứ tự/model, provider được nhận dữ liệu và trigger `service_error/quota`. Khi người dùng chọn provider bắt đầu, chỉ đi về phía sau trong thứ tự; không quay vòng. 429 tốc độ không tự kích hoạt fallback quota ngày. Thử kết nối không fallback.

Job chụp policy lúc tạo. Tắt fallback hoặc thu hồi provider trong policy hiện tại có hiệu lực ở lần gửi sau; bật mới không tự mở rộng sự đồng ý của job cũ. Muốn áp dụng route/provider/model mới, tạo yêu cầu mới. Snapshot nguồn, prompt, schema và feedback giữ nguyên qua các lần fallback. Bản tạo lưu provider/model thực tế; `llm_attempts` giữ từng credential, model, lỗi HTTP, thời gian, usage, request ID và lý do chuyển.

Giới hạn tổng gửi mặc định 6 trên **tất cả provider**, không reset khi chuyển key/provider/retry thủ công. Token output đặt trước mặc định 49152/job, mỗi send trừ max output đã cấu hình; không hoàn phần đặt trước cho lỗi/timeout. Có thể bật ngân sách USD đặt trước: phải nhập mức đặt trước mỗi provider, không tự đoán giá. Mức này là giới hạn vận hành theo ước lượng quản trị, **không bảo đảm hóa đơn thực bằng hoặc nhỏ hơn ước lượng**; không thay thế đối chiếu giá, usage và Billing.

## Kiểm tra và vận hành

```powershell
docker compose config --quiet
docker compose build
docker compose up -d
docker compose exec -T web alembic check
docker compose run --rm --no-deps -e TEST_DATABASE_URL=postgresql+psycopg://news_tool:change-this-local-password@db:5432/news_tool_test web pytest -q -p no:cacheprovider
```

Database test phải tồn tại và tên kết thúc `_test`; test TRUNCATE database riêng. Thay thông tin kết nối nếu đã đổi mật khẩu/user. Provider mock dùng key giả, không gọi mạng; `tests/mock_llm_worker.py` chỉ chạy với database `_ui_test`.

Smoke thật ưu tiên nút **Thử kết nối**. Công cụ `python -m tests.live_control_smoke` (chạy trong worker, gọi web nội bộ) chọn tối đa một credential ready mỗi provider và tạo một connection job/provider. Đây là lệnh có gọi API thật, không nằm trong test tự động. Báo cáo `/tmp/llm-control-live-smoke.json`, có thể copy ra máy chủ. Không chạy nhiều lần chỉ để tiêu quota.

Migration không xóa bảng/phiên bản cũ. Job trước migration giữ log cũ; không bịa credential ID/quota cho lịch sử không có thông tin. Trước rollback phải sao lưu/export; downgrade tự động bị chặn để tránh xóa audit. Backup trước triển khai tại `results/before-llm-control.dump`.

Tài liệu phân loại tham khảo: [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [Gemini troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting), [DeepSeek error codes](https://api-docs.deepseek.com/quick_start/error_codes/). Quota Gemini thuộc project; DeepSeek 429 là giới hạn tốc độ/concurrency, 402 là thiếu số dư. Không suy ra quota từ số key.
