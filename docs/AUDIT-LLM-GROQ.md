# Audit module LLM trước khi bổ sung Groq — 30/09/2026

## Luồng hiện tại

`/selected` → `POST /script-jobs` → `script_service.enqueue_script` → snapshot tối đa 5 bài → prompt `app/llm/prompts/script_v1.txt` → request đóng băng theo route → worker gọi `LLMProvider.generate_script` → HTTP một lần → parse provider → `validate_output` → ScriptVersion → review → TTS/video.

Đã có abstraction ở `app/llm/base.py`: `build_request`, `parse_response`, `generate_script`. Gemini/DeepSeek dùng HTTPX, không có SDK đặc thù trong pipeline. Tuy nhiên `prepare_requests` tự chọn lớp provider và `send_http` đoán tên provider từ URL; `get_provider` import cả Gemini và DeepSeek. Cần registry lazy để thêm Groq mà không phụ thuộc import Gemini.

## Contract giữ nguyên

- Input: `schema_version`, `story_id`, `language`, `target_seconds` (15–90), `tone`, `sources[]` gồm `source_id/url/title/published_at/text`.
- Output: `schema_version/story_id/decision/reason/title/hook/caption/claims/scenes/warnings`.
- Claim: `claim_id/text/evidence[]` với `source_id/quote`; scene: `scene_id/seconds/narration/on_screen_text/visual_brief/claim_ids`.
- Không có public field `figures`, `citations` hoặc `target_duration_seconds`. Số liệu được kiểm tra từ text; trích dẫn dùng evidence; thời lượng dùng target_seconds và scenes.seconds. Không đổi tên hay thêm DTO riêng của Groq.
- `draft → needs_review → approved/rejected`; JSON/validation lỗi có outcome riêng và không được duyệt. Sửa/duyệt đều gọi validator.

## Phát hiện

1. Provider literal/check constraint/secret allowlist/UI chỉ nhận Gemini/DeepSeek; model regex không nhận dấu `/` trong `openai/gpt-oss-120b`.
2. Snapshot chỉ lấy RSS summary; chưa lấy toàn văn. Chuẩn bị nguồn/request đang ở enqueue, không nên đưa network fetch vào request web hoặc transaction dài.
3. Validator kiểm tra JSON, quote nguyên văn, ID và tổng giây, nhưng chưa phát hiện số liệu/suy diễn ngoài nguồn. Tổng giây bắt buộc sát target gây áp lực kéo dài nội dung thiếu dữ kiện.
4. Retry do `llm_control` quản lý trong PostgreSQL; HTTP adapter chỉ gửi một lần. Timeout/crash sau gửi là unknown_outcome; không được sửa thành tự retry mù quáng.
5. Policy mặc định 6 sends toàn job, fallback tắt, retry 15 giây, group concurrency 1. Chuyển provider ngay khi service_error nếu fallback bật; yêu cầu mới cần thử tối đa 3 lần tại provider trước khi fallback.
6. Concurrency hiện theo quota-group/model và số process worker; cần trần LLM toàn cục cùng worker LLM riêng.
7. Raw output và hash request được lưu để audit. Không cần log toàn article/prompt vào process log; dữ liệu nguồn vẫn phải lưu trong snapshot để người duyệt đối chiếu.
8. Policy đã lưu trong DB không tự đổi khi đổi env. Cần hướng dẫn rõ đồng bộ route Groq/fallback cho hệ thống cũ, không tự sửa quyền gửi dữ liệu của job đã tạo.

## Cấu hình/ngoại lệ hiện có

`LLM_PROVIDER`, `GEMINI_MODEL`, `DEEPSEEK_MODEL`, các API key, timeout/output token, fake mode. Key đọc ở worker qua `env:*` hoặc `secret:llm_*`, web không nhận LLM key. `ProviderFailure` mang HTTP status, retry_after, request_id, kind, uncertain, usage, latency; quota/circuit/reservation nằm trong `llm_control`.

Audit này được viết trước thay đổi logic. Migration source hiện đến `0006` (TikTok), có thay đổi video/TikTok chưa commit từ công việc trước. Các commit Groq sẽ chỉ chứa thay đổi lần này.

## Tài liệu chính thức đã đối chiếu

[Groq Structured Outputs](https://console.groq.com/docs/structured-outputs) hỗ trợ strict cho `openai/gpt-oss-120b`, yêu cầu mọi field required và object đóng; [API reference](https://console.groq.com/docs/api-reference) cung cấp Chat Completions; [error codes](https://console.groq.com/docs/errors) mô tả lỗi HTTP. Structured Output bảo vệ format, không chứng minh khẳng định có căn cứ.
# Ghi chú bàn giao

Audit bên dưới ghi nhận trạng thái trước khi triển khai Groq. Phần triển khai và kết quả hiện tại được mô tả tại [HUONG-DAN-GROQ.md](HUONG-DAN-GROQ.md) và [GROQ-VALIDATION.md](GROQ-VALIDATION.md).
