# Nghiệm thu module kịch bản — 22/09/2026

**Phần kỹ thuật đã triển khai. Chưa đạt cổng chất lượng API thật để đưa vào sản xuất video.**

## Đã kiểm tra

- Bộ test ứng dụng cuối cùng: **41/41 pass** trong 52.10 giây (15 test nền và 26 test module kịch bản). Có cảnh báo deprecation của thư viện test, không có test lỗi. Bộ lab offline trước đó: 20/20 pass, prompt/schema/fixtures không đổi.
- Docker Compose build thành công; web/worker/db khởi động healthy. Migration 0002 chạy tự động. `alembic check` không phát hiện sai lệch giữa ORM và database.
- Đã sao lưu database trước migration: `results/before-scripts.dump`. Dữ liệu và hai nhóm tin người dùng đã chọn được giữ nguyên. Test dùng database riêng, không TRUNCATE database thật.
- Prompt `script_v1.txt` và schema được sao chép nguyên từ script-api-lab. Không sửa prompt/schema/fixtures của vòng test 1.
- Gemini, DeepSeek chung interface một request/lần xử lý. Fake chỉ phục vụ kiểm thử; bị chặn ở cổng dựng video.
- Queue có idempotency, khóa chống hai job cùng sự kiện, retry lưu PostgreSQL, tối đa 4 sends, Retry-After/backoff, timeout không tự gửi lại. Đã mock 429/500/502/503/504, 400/401/403 và output rỗng/bị cắt/sai schema.
- Snapshot, bản tạo, bản sửa, bản tạo lại và quyết định duyệt có lịch sử riêng. Validator chạy lại khi lưu/duyệt; chặn bản cũ và bản lỗi. Bản approved cũ không bị đổi khi có bản mới.
- Đã kiểm tra trực tiếp trình duyệt: tạo fake → polling → nguồn/quote → sửa hook → lưu bản 2 → duyệt. Hook và lời đọc cảnh đầu đồng bộ. Chưa lưu thì không duyệt được. Tab cũ trả thông báo phiên bản đã thay đổi.
- Kiểm tra XSS: nội dung `<script>window.injected=true</script>` được hiển thị nguyên văn; không có executable script tương ứng trong DOM.
- Restart container test: job 2 đang queued được xử lý thành công sau restart; sinh bản 3 needs_review; bản 2 vẫn approved, review vẫn còn và cả ba dùng snapshot 1.
- Kill process thật sau durable dispatch: job chuyển unknown_outcome, không tự gửi lại; yêu cầu mới chạy thành công. Các test phục hồi crawl cũ vẫn đạt.
- Web không nhận Gemini/DeepSeek key; kiểm tra chỉ bằng cờ có/không. Worker đọc key hiện có từ file lab. Không sao chép hay hiển thị key.

## Smoke adapter tích hợp

| run_id | Provider | Số request | Kết quả | Thời gian |
|---|---|---:|---|---:|
| `9bbddc68-fa12-4af4-95a9-a2b67ac86c25` | Gemini / gemini-3.8-flash | 1 | HTTP 503, chưa có output; live_call=true | 1.911 s |
| `2ddf1960-b22b-47a3-8cf8-b3589256cb62` | DeepSeek / deepseek-flash | 0 | Chưa có key; live_call=false | 0.033 s |

Báo cáo đầy đủ trong `results/llm-integration/`. Không bỏ kết quả lỗi khỏi mẫu số; tỷ lệ HTTP thành công và structural pass ở smoke Gemini lần này đều 0/1. DeepSeek không có lượt HTTP nên không tính tỷ lệ 0/1 cho API. Không cộng kết quả giả lập vào số liệu API thật.

Adapter Gemini dùng MIME enum `APPLICATION_JSON` theo [REST reference](https://ai.google.dev/api/generate-content). Key/model đã có kiểm tra metadata trong vòng lab trước; metadata thành công không xác nhận quota generate còn lại. HTTP 503 hiện tại không được coi là bằng chứng sai key hay hết quota.

## Chấm chất lượng theo run

| run_id | Dữ kiện / quote / toàn bộ nội dung | Văn phong | Hook | Công sửa | Thời lượng audio |
|---|---|---|---|---|---|
| `9bbddc68-fa12-4af4-95a9-a2b67ac86c25` | Không thể chấm: không có kịch bản | N/A | N/A | N/A | Chưa có |
| `2ddf1960-b22b-47a3-8cf8-b3589256cb62` | Không thể chấm: không gọi API | N/A | N/A | N/A | Chưa có |

Chưa chạy 5 tình huống × 3 vì chưa provider nào qua smoke. Chưa có đầu ra để che tên model và chấm mù. Không suy ra “không có lỗi dữ kiện” từ việc API không trả kết quả.

## Các điều kiện còn thiếu

1. Gemini cần qua smoke sau khi dịch vụ hết 503, hoặc người dùng chủ động cấu hình model khác và thử lại. Không tự chuyển model/provider.
2. Cần cấu hình DeepSeek key và kiểm tra quyền/quota/billing ở tài khoản. Không gửi key qua chat.
3. Sau smoke, chạy benchmark bằng adapter tích hợp, giữ toàn bộ run và đánh giá thủ công. Đổi prompt/schema phải tăng phiên bản và chạy lại cùng fixtures.
4. Hiện có 2 nhóm tin đã chọn; chưa có bộ ít nhất 20 nhóm được người dùng chọn và xác minh nguồn. Cần bổ sung bộ này trước bước đánh giá thực tế, gồm số liệu/nhiều nguồn/thiếu dữ kiện/nội dung dài.
5. Chưa có usage sinh nội dung thành công hoặc đối chiếu dashboard để tính chi phí. Cần thống nhất ngưỡng chi phí và latency; chưa thể chọn API theo dữ liệu hiện tại.
6. Văn phong trung bình ≥4/5, lỗi dữ kiện nghiêm trọng bằng 0 sau xử lý, tình huống mâu thuẫn/thiếu dữ kiện/injection và thời lượng audio đều chưa nghiệm thu. TTS/video chưa được nối.

Module luôn lưu draft chờ duyệt. Chỉ bản mới nhất approved và hợp lệ đi qua `renderable_version`; đây là cổng kỹ thuật, không thay thế đánh giá chất lượng nêu trên.
