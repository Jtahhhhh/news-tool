# Chạy web và toàn bộ worker trong một service

Docker Command trên Render Web Service:

```sh
python -m app.worker_all --with-web
```

Lệnh này chạy migration và kiểm tra database trước, sau đó chạy web cùng bốn
worker: lấy tin, viết kịch bản, dựng video, upload TikTok. Worker lấy tin luôn
nhận WORKER_ROLE=collect. Không đổi CMD mặc định của Dockerfile để các deployment
Compose hiện tại không khởi động trùng worker.

Nếu chỉ cần một Background Worker tổng, dùng `python -m app.worker_all`.
Chế độ đó không mở HTTP và không chia sẻ filesystem với Web Service riêng.

Environment cho chế độ gộp:

```dotenv
APP_ENV=production
DEBUG=false
DATABASE_URL=<Internal Database URL, direct port 5432>
DB_POOL_MODE=direct
MEDIA_ROOT=/data
LLM_MAX_CONCURRENCY=2
LLM_ALLOW_FAKE=false
TTS_URL=http://<private-tts-host>:8000
TTS_TIMEOUT_SECONDS=900
```

Thêm API key đúng với secret reference trong Cấu hình LLM. Nếu dùng TikTok,
thêm TIKTOK_ENABLED=true, TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET,
TIKTOK_TOKEN_KEY và TIKTOK_REDIRECT_URI; giữ TIKTOK_DIRECT_POST_ENABLED=false.
Không ghi secret vào repository.

Mount persistent disk tại /data và bảo đảm user newsroom có quyền ghi.
Chạy một instance để web và các worker cùng đọc được media. TTS vẫn chạy riêng
bằng image vendor/VieNeu-TTS, không có trong image ứng dụng chính. DATABASE_URL
phải dùng direct port 5432 vì publish worker sử dụng session advisory locks.

Sau khi service gộp hoạt động, dừng các worker riêng đã được thay thế để tránh
chạy thêm tiến trình không cần thiết. Không bật đồng thời lệnh gộp trong từng
service của Compose.

Log Supervisor hiển thị từng tiến trình được khởi chạy. Khi một tiến trình thoát,
supervisor dừng toàn bộ và trả exit code 1 để nền tảng phát hiện lỗi và restart.
SIGTERM/SIGINT dừng các nhóm tiến trình con; sau tối đa 20 giây, tiến trình còn
lại bị kết thúc cưỡng bức. Job bị ngắt dựa vào cơ chế lease/recovery hiện có.
Health endpoint của web không chứng minh mọi worker xử lý job thành công;
cần kiểm tra trạng thái job và log từng worker.

Các tiến trình chia sẻ CPU/RAM: cần theo dõi tài nguyên khi render video cùng
lúc với LLM. Đây là thay đổi cách chạy process, không thay thế dịch vụ TTS hay
API key và không chứng minh kết nối production đã thành công.
