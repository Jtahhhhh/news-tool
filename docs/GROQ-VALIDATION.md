# Kết quả kiểm thử Groq — 2026-09-30

## Môi trường và kết quả

- Image riêng: `news-tool:groq-audit`, build thành công từ source hiện tại.
- PostgreSQL 17 riêng: database `news_tool_groq_test`; không dùng database vận hành.
- Chạy toàn bộ test từ image, không mount `.env`, `.env.credentials` hoặc key thật.
- **165 passed, 93 warnings, 111.44 giây**. Warnings là cảnh báo deprecation Alembic/Starlette/AnyIO đã có; không có test thất bại.
- `docker compose config --quiet` thành công, gồm service `llm-worker` mới.
- Migration đến `0007` và `/health` được kiểm tra trong bộ integration tests.

## Các hành vi đã xác minh

| Phạm vi | Cách kiểm tra |
|---|---|
| Groq adapter | HTTP mock, endpoint/payload, Bearer key giả, parse response, request ID và usage. |
| Structured output | `json_schema`, `strict=true`, mọi object cấm field ngoài schema và đủ required fields. |
| Contract | Thiếu field, JSON hỏng, scene ID sai, nguồn không tồn tại, duration quá dài bị chặn; duration ngắn hơn được chấp nhận. |
| Grounding học bổng | Fixture chỉ có 91 ngành, ba nhóm ngành, khoảng 3,7–8,4 triệu đồng/tháng và Bộ GD&ĐT; reject số liệu/đơn vị mới và các suy diễn đã nêu trong yêu cầu. |
| Repair | Lần đầu lỗi chưa tạo bản nháp; một lần sửa cùng provider; vẫn lỗi thì kết thúc, không fallback. |
| Retry/fallback | 429/503 sau 3 lần mới chuyển Gemini; các mã 500/502/504 được phân loại retryable; 400/401/403 không retry. Tôn trọng Retry-After và tổng ngân sách. |
| Tách provider | Groq khởi tạo khi module Gemini bị chặn import; regression Gemini/DeepSeek vẫn qua. |
| Nguồn | Clean HTML, chặn redirect tới địa chỉ không được phép, RSS fallback khi timeout, đóng băng nguồn trước lần gọi. |
| Concurrency | Giới hạn chung qua DB và giới hạn nhóm quota, kể cả reservation chưa rõ kết quả. |
| Pipeline | Groq HTTP mock → draft → trang review → sửa → duyệt → HTTP TTS mock trả WAV hợp lệ → preview audio → FFmpeg thật → preview video → duyệt video và checksum. |
| Regression | Thu thập tin, scripts, quota, worker recovery, video và publishing cùng chạy trong suite. |

Test pipeline dùng audio tổng hợp giả lập, không đánh giá phát âm VieNeu thật. Không gọi Groq/Gemini thật, không chứng nhận quota/quyền model của tài khoản người dùng. Tính đúng ngữ nghĩa của mọi bài báo không thể được chứng minh bằng bộ lọc grounding này; vẫn cần biên tập duyệt nguồn.

## Phạm vi bàn giao

Đã cập nhật source và cấu hình mẫu, không sửa key thật, không deploy lên container đang chạy. Các thay đổi video/TikTok từ công việc trước vẫn được giữ nguyên ngoài các commit Groq; bổ sung kiểm tra lại schema/grounding ngay trước khi tạo job video cho script thật nằm trong file video service đang có ở workspace.

Các commit Groq hiện dựa trên workspace đã có migration video `0005` và TikTok `0006`. Khi chuyển sang máy khác, cần mang theo đầy đủ các phần đó; chỉ checkout các commit Groq trong repo chưa có tiền đề sẽ không đủ để chạy toàn bộ pipeline.

Hướng dẫn vận hành và thêm key: [HUONG-DAN-GROQ.md](HUONG-DAN-GROQ.md).
