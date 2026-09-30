# Source audit — 2026-09-30

## Kết luận

Source đã có đủ khung chính cho chuỗi **lấy tin → gom nhóm → tạo/duyệt kịch bản → TTS → render → duyệt video**. Image ứng dụng build thành công, Compose hợp lệ, migration đang ở `0005 (head)` và bộ test cuối đạt **81/81**.

Luồng video chưa thể dùng với tin thật trong dữ liệu hiện tại. Database có 2 `script_versions` nhưng không có `script_reviews`; bản duy nhất từng xuất hiện như đã duyệt là fixture `fake`. Ba `video_versions` hiện có đều là kết quả test. Màn hình Video đã được siết lại để chỉ nhận bản kịch bản thật có lịch sử duyệt; fixture chỉ hiện khi bật chế độ test.

## Thành phần hiện có

- FastAPI + Jinja2/Bootstrap, CSRF, health endpoint và chỉ publish web tại loopback.
- PostgreSQL + Alembic; named volume giữ dữ liệu.
- Worker lấy RSS/HTML, chuẩn hóa URL/thời gian, chống trùng, retry, lease và phục hồi job.
- Gom sự kiện, chấm điểm, quyết định chọn/bỏ qua và xuất JSON.
- Tạo kịch bản qua Gemini/DeepSeek, nhiều credential, quota/routing/fallback, snapshot nguồn, validator, version và review audit.
- VieNeu-TTS local bằng ONNX CPU, cache audio theo nội dung/cấu hình.
- Render worker FFmpeg, asset upload, timeline theo audio, subtitle, draft/final, ffprobe, version và review.
- Các volume riêng cho PostgreSQL, model TTS, asset, audio, video và file tạm.

## Trạng thái dữ liệu đang chạy

| Thành phần | Số lượng |
|---|---:|
| Nguồn | 2 |
| Bài viết | 356 |
| Nhóm sự kiện | 356 |
| Phiên bản kịch bản | 2 |
| Lịch sử duyệt kịch bản | 0 |
| Job video | 4 |
| Phiên bản video | 3 |
| Lịch sử duyệt video | 0 |

## Phát hiện và xử lý trong đợt audit

### Đã sửa

1. Danh sách Video từng tin vào trường `status='approved'` dù không có bản ghi `ScriptReview`. Điều này làm fixture giả lập trông như kịch bản thật đã duyệt. Danh sách và API tạo job giờ yêu cầu audit review hợp lệ với kịch bản thật, chỉ nhận phiên bản được duyệt mới nhất.
2. Upload JPEG/PNG luôn bị `ffprobe` kiểm tra như luồng video, nên ảnh hợp lệ cũng bị từ chối. Kiểm tra giờ dùng đúng loại media.
3. HTML của bộ lọc trạng thái/provider trên trang Kịch bản bị ghép sai thẻ `option`. Đã sửa lại markup.

### Cần hoàn thiện trước khi vận hành thật

1. **Cao — kiểm tra lại file ngay lúc duyệt video.** API duyệt chỉ kiểm tra file còn tồn tại và dùng kết quả `probe` đã lưu. Nên chạy `ffprobe` lại tại thời điểm duyệt để chặn file đã hỏng hoặc bị thay thế.
2. **Cao — lease render không có heartbeat.** Lease cố định 30 phút; render dài hơn có thể bị một worker khác phục hồi và chạy lại nếu tăng số worker. Cần gia hạn lease theo giai đoạn hoặc giữ khóa/fencing token đến lúc commit.
3. **Trung bình — dọn file tạm.** Thư mục job lỗi có thể còn lại trong volume `media_tmp`; chưa có tác vụ dọn theo tuổi và tham chiếu.
4. **Trung bình — chữ trên màn hình.** `drawtext` chưa có cơ chế wrap/đo tràn đầy đủ và escaping còn mong manh với ký tự đặc biệt. Cần dựng text thành ASS hoặc file text, rồi test câu dài tiếng Việt.
5. **Trung bình — hủy job.** Hủy chỉ được quan sát ở ranh giới giai đoạn; tiến trình TTS/FFmpeg đang chạy chưa bị ngắt ngay.
6. **Trung bình — dữ liệu test trong database thật.** Fixture video hiện được ẩn theo mặc định và gắn nhãn test, nhưng vẫn nằm trong database/volume. Nên có lệnh quản trị xóa dữ liệu test có kiểm tra tham chiếu.
7. **Thấp — migration chỉ tiến tới.** Migration video chủ động không hỗ trợ downgrade; vận hành rollback phải dùng backup/restore.
8. **Thấp — cảnh báo dependency test.** Alembic thiếu `path_separator=os`; FastAPI TestClient phát cảnh báo chuyển từ `httpx` sang `httpx2`.

## Kiểm chứng

- `docker compose config --quiet`: đạt.
- Build image `web`, `worker`, `render-worker`: đạt.
- Alembic: `0005 (head)`.
- Dependency image ứng dụng: `pip check` đạt.
- Test cuối trên image mới build: **81 passed**, 53 cảnh báo deprecation.
- Bài test phục hồi worker từng trượt một lần do đếm 3 thay vì 2 attempt; chạy riêng lại đạt 3/3 và lượt full cuối đạt. Đây là dấu hiệu test timing còn nhạy, nên theo dõi trong CI.
- Compose báo 2 orphan container smoke cũ: `news-tool-smoke-review`, `news-tool-request-smoke`.

## Mức sẵn sàng

| Luồng | Trạng thái |
|---|---|
| Thu thập và lưu tin | Có, đã có dữ liệu thật |
| Gom nhóm/chấm điểm/duyệt tin | Có |
| Tạo và duyệt kịch bản | Có về source; dữ liệu chạy hiện chưa có review thật |
| TTS local | Có, CPU, một luồng |
| Render video | Có, đã smoke test bằng fixture |
| Duyệt video theo phiên bản | Có; cần harden kiểm tra file lúc duyệt |
| Chạy toàn luồng với tin thật | Chưa được chứng minh vì chưa có kịch bản thật có `ScriptReview=approved` |
| Tự đăng TikTok | Chưa có, đúng phạm vi bản đầu |

