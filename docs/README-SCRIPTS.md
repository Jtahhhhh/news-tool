# Tạo và duyệt kịch bản

**Cập nhật migration 0003:** cơ chế credential/quota/fallback và trạng thái `unknown_outcome` mới được mô tả tại [README-LLM-CONTROL.md](README-LLM-CONTROL.md). Các mô tả retry/fallback 0002 dưới đây là thiết kế ban đầu; ưu tiên hướng dẫn 0003 khi có khác biệt.

## Khởi động và cấu hình

Chạy tại thư mục `news-tool`:

```powershell
docker compose up --build -d
```

Mở http://localhost:8000/scripts. Chọn một nhóm ở **Tin đã chọn**, bấm **Viết kịch bản**, chọn provider/giọng điệu/thời lượng rồi gửi. Crawl và chấm điểm không tự tạo yêu cầu LLM.

Migration `0002` chạy tự động trước web; worker đợi web và PostgreSQL healthy. Bốn bảng mới độc lập: `script_jobs`, `script_versions`, `script_source_snapshots`, `script_reviews`. `/scripts/{id}` dùng ID nhóm sự kiện làm định danh chuỗi phiên bản. PostgreSQL named volume giữ mọi dữ liệu khi tạo lại container. Không dùng `docker compose down -v` nếu cần giữ dữ liệu.

Các biến trong `.env.example`: `LLM_PROVIDER`, `GEMINI_MODEL`, `DEEPSEEK_MODEL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_OUTPUT_TOKENS`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`. Không đưa key vào Git hoặc nội dung yêu cầu. Compose ghi đè hai biến key thành rỗng ở **web**; chỉ **worker** nhận key. Worker đọc `script-api-lab/.env` nếu có, sau đó `.env` của ứng dụng ghi đè. Giá trị key rỗng trong `.env` cũng ghi đè key ở lab; muốn dùng cấu hình lab hiện có thì bỏ dòng key tương ứng khỏi `.env` ứng dụng. Không cần sao chép key.

`LLM_ALLOW_FAKE=false` mặc định. Chỉ bật ở môi trường thử nghiệm riêng. Fake được gắn cảnh báo và không qua cổng chuyển sang dựng video, kể cả được duyệt để kiểm thử UI. Test tự động không gọi API thật.

## Nguồn, phiên bản và duyệt

- Worker chụp tối đa 5 bài theo ID; dữ liệu hiện có là nội dung/tóm tắt đã thu thập, không mặc nhiên là toàn bài. Kiểm tra tối thiểu: một nguồn có ít nhất 80 ký tự, 12 đơn vị cách nhau bởi khoảng trắng, và không chỉ có tiêu đề. Đây không phải chứng nhận đủ dữ kiện; model và người duyệt vẫn phải kiểm tra.
- Prompt và schema phiên bản **1.0** giữ nguyên từ lab. Hash prompt/schema được lưu cùng bản tạo. Nhận xét biên tập là trường riêng trong job.
- Bản hợp lệ có `outcome=draft`, `status=needs_review`. `insufficient_evidence` và `validation_error` được lưu riêng, không được duyệt. Output lỗi, thông tin HTTP, request ID, usage và thời gian được giữ để chẩn đoán.
- Bản sửa/tạo lại luôn là phiên bản mới. Tạo lại mặc định dùng snapshot cũ; chọn **Cập nhật snapshot** nếu muốn dùng nguồn hiện tại. Đổi thời lượng/giọng điệu tạo snapshot yêu cầu mới với cùng nguồn.
- Sửa hook đồng bộ lời đọc cảnh đầu. Lưu và duyệt đều chạy lại validator. Quote phải nguyên văn, nguồn/claim phải tồn tại, ID cảnh liên tục, tổng giây dự kiến lệch mục tiêu không quá 3 giây.
- Hai tab: API yêu cầu ID bản nền/ID bản duyệt. Bản cũ trả HTTP 409; UI yêu cầu tải lại. Khi đang sinh phiên bản mới, lưu/duyệt cũng bị chặn để tránh quyết định trên nội dung sắp lỗi thời.
- HTML/script từ nguồn và output chỉ hiển thị dạng văn bản. Dẫn chứng bên phải không thay đổi theo lần crawl sau.
- Validator không chứng nhận khẳng định đúng, quote thực sự hỗ trợ claim, văn phong tốt hoặc thời lượng đọc đúng. Người duyệt phải kiểm tra tất cả nội dung. Tên người duyệt là tên tự khai trong công cụ local; chưa có hệ thống tài khoản xác thực.

Module dựng video sau này phải lấy bản qua `app.services.script_service.renderable_version(session, event_id)`: chỉ bản **mới nhất**, **approved**, hợp lệ, không phải fake, và không có job đang tạo bản mới mới được trả về. Module này chưa tạo video hay gọi TTS.

## Retry và phục hồi

Mỗi lượt worker chỉ gửi một request. HTTP 429/500/502/503/504 được thử tối đa **4 lần gửi**. Các lần chờ cơ sở 5/15/45 giây cộng jitter 0–2 giây; ưu tiên `Retry-After`, giới hạn tối đa một ngày. Job chờ ở `retry_wait` với `next_attempt_at`; worker tiếp tục xử lý việc khác, không ngủ để chờ API.

401/403/400 và lỗi cấu hình không tự retry. Timeout/lỗi mạng hoặc worker chết sau khi đã đánh dấu gửi: `failed`, `error_kind=unknown_outcome`, vì request trước có thể đã được xử lý và tính phí. Người dùng kiểm tra dashboard rồi chủ động gửi yêu cầu mới nếu muốn. Không tự chuyển provider.

Job đang chạy nhưng chưa gửi được phục hồi khi hết lease. Lease bằng timeout đã chụp trong job cộng 30 giây; process con bị dừng sau timeout cộng 20 giây. Owner cũ không được ghi kết quả sau khi mất lease. Worker luân phiên ưu tiên hàng đợi crawl và kịch bản để tránh bỏ đói một loại công việc.

## API

Các POST nhận JSON và header `x-csrf-token` lấy từ meta `csrf-token` của trang cùng cookie hiện tại. GET HTML mặc định; thêm `?format=json` hoặc `Accept: application/json` để lấy dữ liệu.

| Endpoint | Dữ liệu chính |
|---|---|
| `POST /script-jobs` | `event_id`, `provider`, `tone`, `target_seconds`, `idempotency_key`, `feedback` tùy chọn |
| `GET /script-jobs/{id}` | trạng thái, attempts, lịch retry, lỗi, logs, ID phiên bản |
| `GET /scripts` | tìm `q`, lọc `status`, `provider`, phân trang `page` |
| `GET /scripts/{event_id}` | phiên bản, snapshot, lịch sử job/duyệt; `version_id`, `compare_id` để xem/so sánh |
| `POST /scripts/{event_id}/versions` | `base_version_id`, `data` theo Output schema |
| `POST /scripts/{event_id}/regenerate` | như tạo mới, thêm `base_version_id`, `refresh_sources=false` mặc định |
| `POST /scripts/{event_id}/reviews` | `version_id`, `decision=approved/rejected`, `reviewer`, `comment` |

Idempotency key dùng lại với cùng payload trả về cùng job; dùng với payload khác trả 409. PostgreSQL còn chặn hai job đang hoạt động cho cùng sự kiện dù key khác nhau.

## Kiểm thử

Database test phải riêng và tên kết thúc `_test`. Thay thông tin kết nối nếu đã đổi cấu hình mặc định:

```powershell
docker compose exec -T db createdb -U news_tool news_tool_test
docker compose run --rm --no-deps -e TEST_DATABASE_URL=postgresql+psycopg://news_tool:change-this-local-password@db:5432/news_tool_test web pytest -q -p no:cacheprovider
docker compose exec -T web alembic check
```

Lệnh `createdb` chỉ cần chạy lần đầu. Test dùng TRUNCATE trên database test; không chạy với database thật.

Live evaluation dùng adapter mới nhưng chạy tách biệt, không sửa dữ liệu biên tập. Tại PowerShell trong `news-tool`, smoke một request:

```powershell
docker compose run --rm --no-deps -v "${PWD}/script-api-lab/fixtures:/fixtures:ro" -v "${PWD}/results:/results" worker python -m app.llm.evaluate --provider gemini --input /fixtures/normal.json --out /results/llm-integration
```

Thay `gemini` bằng `deepseek` để smoke provider đó. Sau khi provider qua smoke, benchmark 5 tình huống × 3:

```powershell
docker compose run --rm --no-deps -v "${PWD}/script-api-lab/fixtures:/fixtures:ro" -v "${PWD}/results:/results" worker python -m app.llm.evaluate --provider gemini --fixtures /fixtures --repeat 3 --out /results/llm-integration
```

CLI evaluation không tự retry; mỗi run có attempts/retries/live_call/usage/error và output. Gặp lỗi dịch vụ/tài khoản sẽ dừng batch. Retry durable của ứng dụng được kiểm thử riêng. Không dùng structural pass làm điểm chất lượng. Báo cáo trạng thái nghiệm thu ở `SCRIPT-VALIDATION.md`.
