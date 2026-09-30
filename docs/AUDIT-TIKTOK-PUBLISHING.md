# Audit và triển khai TikTok Publishing

Ngày 30/09/2026. Phạm vi: working tree local `news-tool`, gồm thay đổi có sẵn chưa commit; không giả định trạng thái image đang chạy giống source. Không tìm thấy AGENTS.md trong dự án ở lần kiểm tra này. Đã đọc hướng dẫn/config/audit hiện có, models/migrations/routes/services/workers/templates/Compose và tài liệu API chính thức.

## Hiện trạng trước tích hợp

Revision source cuối là `0005` (video), nối tiếp `0004`. Video ở named volume `media_video`, đường dẫn lưu trong `VideoVersion.output_key`; review riêng trong `VideoReview`. Nhãn TEST nằm ở version/config/asset, script fake cần bị chặn độc lập. Render mới có version và review riêng, nhưng chưa có checksum file gắn quyết định duyệt.

Worker video có checkpoint audio, lease cố định 30 phút và heartbeat process; không đủ để dùng lại làm cơ chế publish có side effect remote. Collector/LLM có job riêng nhưng không chứa publish_id, consent tài khoản hoặc khả năng đối chiếu phần đã truyền. Vì vậy thêm publish-worker/bảng riêng thay vì tái sử dụng mù quáng trạng thái render.

Ứng dụng có CSRF và bind loopback, chưa có login/RBAC. LLM secret dùng tham chiếu worker; TikTok OAuth cần web trao đổi code và publish-worker refresh nên cần secret riêng cho hai service này.

## Phát hiện và xử lý

| Mức | Phát hiện / file-hàm | Xử lý hoặc giới hạn |
|---|---|---|
| P1 | `routes/video.review`: chỉ tin probe cũ, không hash file | `media_integrity.seal_review` re-probe và hash; thêm hash vào version và review. Không backfill quyết định cũ |
| P1 | Chưa có cổng publish server | `services/publishing.eligible`, `verified_copy`: chặn thiếu review/hash, file thay đổi/mất, TEST, fake; kiểm tra lúc tạo và trước truyền |
| P1 | Nguy cơ gửi trùng khi init timeout/crash | `publish_worker.dispatch/claim/step`: commit intent trước gửi; unknown không init lại; lưu publish_id trước xử lý URL |
| P1 | Lease đơn thuần có thể cho hai sender chạy | `job_lock`, `owned`, `pulse`: session advisory lock + owner fencing + lease heartbeat; worker khác không chiếm job đang có khóa dù lease hết |
| P1 | OAuth callback replay/CSRF và token rotation đồng thời | `tiktok/accounts.consume_state`, `account_lock`, `access_token`: cookie browser, hash state/TTL/consume một lần, khóa refresh/disconnect, crash refresh cần reconnect |
| P1 | Token/code/signed URL có thể lộ qua log | Fernet ngoài DB key; whitelist lỗi; không persist raw HTTP; tắt Uvicorn access log; no-referrer/no-store; proxy phải cấu hình tương tự |
| P2 | `compose.yaml` render-worker kế thừa .env chứa LLM key | Đổi render-worker sang env_file rỗng, giữ allowlist chung |
| P2 | Hủy local có thể bị hiểu là xóa TikTok | Phân biệt cancelled trước gửi và stopped sau intent; giữ publish_id nếu init trả sau hủy, không ghi đè stopped thành thành công |
| P2 | Upload thành công bị nhầm đã đăng | Mapping SEND_TO_USER_INBOX riêng; chỉ PUBLISH_COMPLETE thành published; test UI/API |
| P2 | App local chưa có auth; OAuth Web yêu cầu HTTPS | Không tự mở port công khai; hướng dẫn reverse proxy có kiểm soát truy cập. Đây là điều kiện triển khai thật, chưa được thiết lập trong đợt này |

Các lỗi cũ ngoài phạm vi publish vẫn còn: kiểm tra JPEG/PNG upload, lease/hủy render, escaping/tràn chữ FFmpeg, bộ lọc TEST toàn danh sách và dọn file tạm. Xem [audit trước](AUDIT-HIEN-TRANG-2026-09-30.md). Publish gate không sửa các lỗi renderer này nhưng từ chối file không khớp nội dung đã duyệt.

## Hợp đồng tích hợp

- `POST /publish-jobs` nhận version ID cụ thể, checksum người dùng đang xem, account ID, caption local, idempotency key và `confirmed=true`; chỉ chấp nhận mode upload.
- Snapshot giữ account open_id/name, version, review ID, path, size, SHA-256 qua trường job, caption và thời điểm xác nhận. Duyệt nội dung không tự tạo consent gửi.
- Unique `(video_version_id, account_id)` tồn tại suốt lịch sử để chặn tạo job mới kể cả đổi key sau timeout. Đây là lựa chọn bảo thủ; chưa có retry init thủ công.
- Worker truyền từ bản sao riêng đã kiểm tra hash, không đọc lại đường dẫn mutable sau hash để gửi. Volume media của publish-worker chỉ đọc.
- Sau crash ở init không có publish_id: unknown. Có ID hoặc crash khi PUT: đối chiếu ID cũ; chỉ tiếp tục chunk khi server xác nhận ranh giới bytes và URL chưa hết hạn.
- Giới hạn retry init: chỉ 429 có lỗi xác nhận, tối đa 3. Network/5xx init luôn unknown. Lỗi chunk đối chiếu trước; tổng PUT tối đa chunks+5; status lỗi tối đa 5 lần liên tiếp. Tôn trọng Retry-After khi có.
- Kiểm tra thủ công là status-only, không tự gửi phần còn thiếu sau khi người dùng dừng.
- Không có webhook/Direct Post. Cờ Direct Post mặc định false và fail-closed nếu bật; chưa có thực thi đăng trực tiếp.

## Đối chiếu tài liệu API chính thức

Đã đọc ngày 30/09/2026:

| Nội dung | Kết luận dùng trong code | Nguồn |
|---|---|---|
| OAuth Web | HTTPS callback tĩnh, state; module yêu cầu cùng host browser | [Login Kit Web](https://developers.tiktok.com/docs/en/login-kit-web) |
| Token | exchange/refresh `/v2/oauth/token/`, revoke `/v2/oauth/revoke/`; token refresh có thể thay đổi | [Token management](https://developers.tiktok.com/docs/en/oauth-user-access-token-management) |
| Scope | App được duyệt và user cấp video.upload; thêm user.info.basic để nhận diện | [Get started Upload](https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content) |
| Upload video | `/v2/post/publish/inbox/video/init/`; body source_info; không dùng caption của Direct Post/photo | [Upload reference](https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video) |
| Transfer | FILE_UPLOAD, PUT tuần tự, chunk floor division, final chứa dư; URL có hạn | [Transfer guide](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide) |
| Status | `/v2/post/publish/status/fetch/`; inbox khác posted, uploaded_bytes dùng đối chiếu | [Get Post Status](https://developers.tiktok.com/docs/en/content-posting-api-reference-get-video-status) |
| Direct Post | Có scope/audit/UX/mục đích sử dụng riêng; tiện ích chỉ cho nhóm nội bộ không đáp ứng intended use nêu trong guide | [Sharing guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines?enter_method=left_navigation) |

Không thử lấy token, không dùng credential thật, không gửi video thật trong đợt triển khai. Không coi quyền phát triển app hoặc test mock là quyền API production.

## File bàn giao

- Models `app/models/publishing.py`, migration `0006_tiktok_publishing.py`, hash review/video.
- Adapter/OAuth/config `app/tiktok/`; server gate `app/services/publishing.py`, `media_integrity.py`.
- Worker `app/publish_worker.py`; routes `app/routes/publishing.py`.
- UI `app/templates/publishing.html`, `app/static/publishing.js`; menu TikTok và duyệt lại video cũ.
- Compose service publish-worker, `.env.tiktok.example`, dependency cryptography khóa trong requirements.
- [Hướng dẫn vận hành](HUONG-DAN-TIKTOK.md) và [báo cáo kiểm thử](TIKTOK-VALIDATION.md).

## Giới hạn nghiệm thu

Nghiệm thu code cuối: **126 test đạt**, không skip, trên image đã đóng gói và PostgreSQL test riêng; Compose, dependency, cú pháp JavaScript và Alembic metadata check đạt. Chi tiết và phần chưa chạy được ghi trong [báo cáo kiểm thử](TIKTOK-VALIDATION.md).

Code và mock test không chứng minh luồng OAuth/Upload thực đã được TikTok chấp nhận. Cần app credentials/scopes, HTTPS callback có xác thực, chọn video thật và xác nhận gửi riêng để nghiệm thu live. Chưa triển khai lên database/containers biên tập đang chạy; cần backup và apply migration khi triển khai.

Khóa/lease và test đồng thời bảo vệ sender local, nhưng không tạo được giao dịch nguyên tử giữa PostgreSQL và TikTok. Khoảng mất kết nối sau gửi là lý do giữ unknown_outcome, không cam kết exactly-once ở nhà cung cấp. Root/DB administrator có thể thay dữ liệu; đây không phải mô hình chống người quản trị máy chủ độc hại.
