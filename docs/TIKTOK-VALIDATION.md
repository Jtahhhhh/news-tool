# Kiểm thử TikTok Upload — 30/09/2026

## Môi trường và phạm vi

- Image kiểm thử riêng `news-tool:tiktok-audit`, Python 3.12, PostgreSQL 17 trong container `news-tool-tiktok-audit-db`, database `news_tool_publishing_test`.
- Network Docker riêng, không publish cổng database. Không dùng database/media của ứng dụng đang chạy.
- TikTok API dùng MockTransport hoặc adapter fake trong test. Không gọi OAuth/Upload/Direct Post thật, không gửi video ra TikTok.
- Test tích hợp dùng TRUNCATE trên database `_test`; không chạy lệnh test với database sản xuất.

## Kết quả đã chạy

| Kiểm tra | Kết quả |
|---|---|
| Build image ứng dụng cùng cryptography đã khóa version | Đạt |
| Compose config | Hợp lệ |
| JavaScript `node --check app/static/publishing.js` | Đạt |
| `alembic check` trên DB test đã migrate | No new upgrade operations detected |
| `pip check` trong image | No broken requirements found |
| Vòng đầu test TikTok + video | 36 passed, 26 warnings, 36,92 giây |
| Toàn bộ suite trước khi bổ sung các ca cuối | 113 passed, 74 warnings, 127,64 giây |
| Module TikTok sau khi bổ sung các ca cuối | 45 passed, 34 warnings, 43,93 giây |
| **Nghiệm thu cuối toàn bộ suite trong image đã đóng gói (không mount source)** | **126 passed, 85 warnings, 109,10 giây** |

Các con số trên là từng lượt riêng, không cộng chúng thành số test độc lập. Warnings hiện có về Alembic path_separator và FastAPI/Starlette TestClient deprecation; không phải lỗi TikTok API.

Lượt cuối chạy `pytest -q -p no:cacheprovider` trong image `news-tool:tiktok-audit`, truyền `TEST_DATABASE_URL` của database tách biệt nêu trên. Migration từ database mới và metadata check đã được kiểm chứng. Container/database/network test được dọn sau nghiệm thu; image kiểm thử được giữ để có thể chạy lại.

## Ma trận nghiệm thu mock

| Yêu cầu | Bằng chứng test trong `tests/test_publishing.py` |
|---|---|
| Chặn chưa duyệt, TEST, fake, thiếu review/hash, mất/thay file | `test_gate_rejects_bypass` |
| Consent bắt buộc, chặn Direct Post, idempotency + hai request đồng thời | `test_consent_idempotency_and_concurrent_requests` |
| Hash/review kiểm tra lại trước init và PUT | `test_mutation_between_enqueue_and_send_never_calls_api`, `test_rechecks_review_and_bytes_before_transfer` |
| Phiên bản mới không kế thừa quyền gửi | `test_new_version_does_not_inherit_publish_approval` |
| Re-probe lúc duyệt | `test_review_reprobes_and_new_render_is_not_approved`, test review video dùng MP4 thật bằng FFmpeg |
| OAuth state sai browser/hết hạn/replay | `test_oauth_state_binding_expiry_and_replay` |
| OAuth callback thất bại tiêu thụ state, thành công có cookie secure | `test_oauth_callback_consumed_even_when_provider_fails`, `test_oauth_success_and_secure_cookie` |
| Refresh đồng thời, hết hạn và thất bại | `test_refresh_serialized_and_disconnect`, `test_refresh_failure_requires_reconnect` |
| Ngắt tài khoản/revoke chưa rõ kết quả | `test_revoke_failure_blocks_local_sending` |
| Token mã hóa, sai key không fallback plaintext | `test_token_wrong_encryption_key_is_not_plaintext_fallback` |
| Init schema video chỉ source_info, Content-Range đúng, không gửi bearer đến upload host | `test_adapter_contract_and_redaction` |
| Chunk boundary, URL upload không tin cậy | `test_chunks`, `test_upload_url_rejects_untrusted` |
| Timeout init không tạo upload mới | `test_timeout_init_no_duplicate_after_restart` |
| Crash sau commit init intent | `test_crash_after_init_intent_never_reinitializes` |
| Crash/timeout PUT phải đối chiếu ID cũ | `test_upload_timeout_reconciles_existing_id`, `test_crash_during_put_reconciles_before_continuing` |
| Hai worker, lease hết nhưng request còn chạy | `test_two_workers_and_expired_lease_cannot_duplicate_live_request` |
| Hủy trong init vẫn giữ publish_id, manual recovery chỉ status | `test_cancel_during_init_preserves_publish_id_without_resuming` |
| Retry backoff/Retry-After, số lần PUT/status hữu hạn | `test_api_limit_rejection_retries_with_backoff`, `test_bounded_transfer_retries`, `test_remote_failed_and_status_retry_limit` |
| Offset không hợp lệ không gửi tiếp | `test_unconfirmed_offset_never_resumes` |
| Inbox không báo đã đăng; chỉ status chứng minh posted | `test_complete_upload_requires_api_post_evidence` |
| Không echo raw lỗi/token/URL vào HTML/JSON | `test_adapter_contract_and_redaction`, `test_api_errors_do_not_echo_unexpected_provider_values`, `test_complete_upload_requires_api_post_evidence` |

Các test restart tái tạo checkpoint/lease/intent đã lưu trong DB và khởi chạy lượt worker tiếp theo; chưa phải phép thử kill Docker process tại mọi thời điểm mạng. UI đã được kiểm tra qua TestClient/HTML và cú pháp JavaScript; chưa nghiệm thu browser tự động toàn bộ thao tác OAuth ngoài TikTok.

## Chưa chạy / điều kiện còn lại

- OAuth với app TikTok thực, xác nhận scope và callback HTTPS qua reverse proxy có xác thực.
- Upload một video thật do người dùng chọn và xác nhận, kiểm tra inbox trên thiết bị TikTok và hoàn tất đăng.
- Benchmark file lớn/đường truyền chậm; bộ test chia chunk kiểm tra hợp đồng/số học, không truyền file 4 GB thật.
- Load test nhiều tài khoản và kill-process/network-partition kéo dài.
- Direct Post không được triển khai hoặc nghiệm thu; cờ mặc định tắt và bật cờ sẽ fail-closed.
- Không tự triển khai migration 0006 vào DB biên tập hay khởi động lại các container thật trong đợt này.

Không dùng kết quả mock để công bố “đã gửi TikTok thành công”. [Hướng dẫn cấu hình và quy trình kiểm thử thật](HUONG-DAN-TIKTOK.md).
