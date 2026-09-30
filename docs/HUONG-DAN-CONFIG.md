# Hướng dẫn cấu hình News Tool

**Bổ sung migration 0006:** có module TikTok Upload tùy chọn, xem [hướng dẫn TikTok](HUONG-DAN-TIKTOK.md). Web và publish-worker nhận file secret riêng `.env.tiktok`; render-worker đã bỏ kế thừa `.env`. Các nhận xét audit về key trên render-worker phía dưới là hiện trạng trước thay đổi 0006.

Đối chiếu `app/config.py`, `compose.yaml`, bộ quản lý LLM và video ngày 30/09/2026. Các giá trị model dưới đây là mặc định trong source, không phải xác nhận model hiện khả dụng với tài khoản provider.

## 1. Chuẩn bị và chạy lần đầu

Cần Docker Engine/Docker Desktop đang chạy với Linux containers và Docker Compose hỗ trợ `env_file.required`. Python trong image ứng dụng là 3.12 trở lên; không cần cài Python trên Windows khi dùng Docker. Cần mạng để tải image, model TTS, lấy tin và gọi LLM. Chưa có benchmark phần cứng tối thiểu được kiểm chứng trong đợt audit này.

Tại PowerShell, chỉ sao chép file mẫu nếu chưa có cấu hình để tránh ghi đè key/cấu hình cũ:

```powershell
Set-Location D:\tiktok_tool\news-tool
if (!(Test-Path .env)) { Copy-Item .env.example .env }
if (!(Test-Path .env.credentials)) { Copy-Item .env.credentials.example .env.credentials }
```

Sửa `.env`: đặt mật khẩu database riêng trước lần khởi tạo đầu. Sửa `.env.credentials` trên máy để nhập key. Không đưa key vào UI hoặc Git.

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

Web ở <http://localhost:8000>, chỉ bind loopback. Chưa có đăng nhập/RBAC; không đổi sang cổng công khai khi chưa bổ sung xác thực. PostgreSQL/TTS không publish cổng host. Lần đầu TTS có thể cần thời gian tải model.

## 2. Database và thu thập

| Biến | Mặc định | Ý nghĩa/giới hạn |
|---|---|---|
| `POSTGRES_DB` | `news_tool` | Tên database |
| `POSTGRES_USER` | `news_tool` | User database |
| `POSTGRES_PASSWORD` | `change-this-local-password` | Đổi trước khởi tạo đầu |
| `WEB_PORT` | `8000` | Cổng web trên host |
| `POLL_SECONDS` | `3` | Chu kỳ worker thu thập; 0,1–60 giây |
| `JOB_TIMEOUT_SECONDS` | `120` | Timeout job thu thập; 5–3600 giây |
| `MAX_ATTEMPTS` | `3` | Số lượt thử job thu thập; 1–10; không phải trần gửi LLM |
| `RETRY_DELAY_SECONDS` | `30` | Chờ retry thu thập; 1–3600 giây |
| `REQUEST_TIMEOUT_SECONDS` | `20` | Timeout HTTP nguồn; 1–120 giây |
| `MAX_RESPONSE_BYTES` | `5242880` | Dung lượng response nguồn tối đa; tối thiểu 1024 byte |
| `MAX_ARTICLES_PER_JOB` | `150` | Tối đa 1–1000 bài/lần |
| `RANK_KEYWORDS` | rỗng trong code | Từ khóa cách nhau bằng dấu phẩy; file mẫu có công nghệ,kinh tế,khoa học |
| `ALLOW_PRIVATE_SOURCES` | `false` | Chỉ bật nếu chủ động cho phép nguồn mạng nội bộ đáng tin cậy |

Compose cố định `DB_HOST=db`, `DB_PORT=5432`. Chạy Python ngoài Docker dùng mặc định `localhost:5432`; `DATABASE_URL` ưu tiên hơn các biến database trong Settings. **Không đặt `DATABASE_URL` trong `.env` của Compose thông thường**: worker/render-worker có thể nhận qua env_file nhưng web không nhận, dẫn tới các dịch vụ dùng database khác nhau.

Đổi `POSTGRES_PASSWORD` trong file không đổi mật khẩu role của volume PostgreSQL đã khởi tạo; cần thay mật khẩu trong database và đồng bộ cấu hình ứng dụng. Tương tự, đổi tên DB/user không tự chuyển dữ liệu cũ.

`TZ=Asia/Ho_Chi_Minh` có trong file mẫu nhưng không được allowlist vào web và không đổi ranh giới bộ lọc ngày UTC trong code.

## 3. API key và model

| Biến | Mặc định trong source | Phạm vi |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | Provider chọn mặc định |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Model khởi tạo/default; kiểm tra route đã lưu ở UI |
| `DEEPSEEK_MODEL` | `deepseek-flash` | Tương tự |
| `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` | rỗng | Tham chiếu credential mặc định |
| `LLM_TIMEOUT_SECONDS` | `60` | 1–300 giây |
| `LLM_MAX_OUTPUT_TOKENS` | `8192` | 256–32768 mỗi lượt |
| `LLM_ALLOW_FAKE` | `false` | Chỉ bật ở môi trường test riêng |

Ưu tiên lưu key trong `.env.credentials`, ví dụ tên biến `LLM_KEY_GEMINI_A` và `LLM_KEY_DEEPSEEK_A`. Trong `/llm`, tạo credential với ref `env:LLM_KEY_GEMINI_A`, provider tương ứng, model cho phép, project/account và quota group.

Worker đọc env_file theo thứ tự: `script-api-lab/.env` → `.env` → `.env.credentials`. Giá trị file sau ghi đè file trước, kể cả chuỗi rỗng. Các giá trị khai báo trực tiếp trong `environment` của Compose lại ưu tiên hơn env_file. Web không đọc các env_file này và nhận hai key mặc định rỗng.

Sau khi sửa key:

```powershell
docker compose up -d --force-recreate worker
```

Đợi worker cập nhật trạng thái credential (khoảng 30 giây). `ready` chỉ xác nhận đọc được secret. Nút **Thử kết nối** gửi một request thật, có thể tốn quota; dùng sau khi kiểm tra đúng provider/model.

Tên env được chấp nhận: `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, hoặc `LLM_KEY_` theo sau bởi 1–100 ký tự chữ hoa/số/gạch dưới. UI nhận tham chiếu, không nhận nội dung key.

**Lưu ý audit:** render-worker kế thừa `.env`, vì vậy key đặt trong `.env` còn được cấp cho render-worker. Đặt key trong `.env.credentials` để giới hạn cho worker theo Compose hiện tại.

## 4. Quota, retry và fallback trong `/llm`

Policy lưu trong PostgreSQL. Đổi biến model không đảm bảo cập nhật route đã lưu: kiểm tra và sửa route/model trong UI, rồi tạo job mới. Job đã tạo giữ snapshot policy; các thao tác thu hồi quyền/tắt fallback vẫn có thể hạn chế lần gửi tiếp theo.

| Thiết lập policy | Mặc định |
|---|---|
| Fallback | Tắt |
| Tổng lượt gửi/job | 6, tối đa 6, tính cả retry và fallback |
| Backoff cơ sở/trần | 15/120 giây |
| Cửa sổ retry | 900 giây |
| Số request đồng thời theo nhóm/model | 1 |
| Output token đặt trước toàn job | 49152 |
| Circuit | Ngưỡng 3 lỗi, thời gian cấu hình 60 giây |

Key dùng chung quota phải cùng `quota_group`; thêm key không tạo thêm quota. Số ưu tiên nhỏ hơn được chọn trước. Giới hạn/window nội bộ không phải quota hoặc thời điểm reset thực của provider. `observed_calls` là số lượt hệ thống đặt trước, không phải hóa đơn.

Bật fallback chỉ khi chấp nhận gửi dữ liệu nguồn sang từng provider trong route. `unknown_outcome` không tự retry vì request trước có thể đã được xử lý. Trước mở lại nhóm quota hoặc gửi lại, đối chiếu dashboard tài khoản. Xem chi tiết tại [quản lý LLM](README-LLM-CONTROL.md).

## 5. Docker secrets (tùy chọn)

Sao chép `compose.secrets.example.yaml` thành `compose.secrets.yaml`. Tạo file `secrets/llm_gemini_a` chứa key trên máy; override mẫu chỉ mount vào worker. Trong UI dùng `secret:llm_gemini_a`.

```powershell
docker compose -f compose.yaml -f compose.secrets.yaml up --build -d
```

Dùng cùng hai file Compose trong các lệnh tạo lại worker sau đó. Tên secret phải bắt đầu `llm_`; không hỗ trợ đường dẫn tùy ý hoặc symlink. Không commit secret hay override chứa dữ liệu nhạy cảm. Các file `.env`, `.env.credentials`, `secrets/` đã bị loại khỏi Git/build context.

## 6. TTS và render

| Thiết lập | Giá trị hiện tại | Cách thay |
|---|---|---|
| `VIENEU_PRECISION` | `fp32` | `.env`; đánh giá lại phát âm/chất lượng khi đổi |
| `TTS_TIMEOUT_SECONDS` | `900` | `.env`; timeout mỗi request TTS, không phải toàn job |
| `VIENEU_BACKEND` | `onnx` | Cố định trong Compose |
| `VIENEU_MAX_STREAMS` | `1` | Cố định trong Compose |
| `VIENEU_QUEUE` / `VIENEU_QUEUE_TIMEOUT` | `2` / `30` | Cố định trong Compose |
| `VIENEU_WATERMARK` | `1` | Cố định trong Compose |
| `MEDIA_ROOT` | `/data` | Compose cố định; phải đồng bộ mount ở web/render-worker |
| `TTS_URL` | `http://tts:8000` | Compose cố định |
| Giọng/template/chất lượng/cách đọc | Theo từng job | Chọn tại trang Video |

Đặt biến cùng tên vào `.env` không thay được các giá trị Compose đã hardcode. Source hiện không có biến `.env` cho số render worker hay thời hạn lease 30 phút. Giữ một worker; xem rủi ro lease trong audit. Đổi cấu hình bằng `up -d --force-recreate`, vì `restart` không nạp lại environment mới.

## 7. Lưu trữ, sao lưu và nâng cấp

Các named volume: `postgres_data` (DB), `tts_models` (model tải về), `media_assets`, `media_audio`, `media_video`, `media_tmp`. JSON xuất tin đã chọn không thay thế backup database.

Để có backup nhất quán, dừng web/worker/render-worker trong thời gian sao lưu, giữ db chạy. Ví dụ dưới dùng user/database mặc định; thay nếu đã cấu hình khác:

```powershell
New-Item -ItemType Directory -Force backups
docker compose stop web worker render-worker
docker compose exec -T db pg_dump -U news_tool -d news_tool -Fc -f /tmp/news-tool.dump
docker compose cp db:/tmp/news-tool.dump ./backups/news-tool.dump
```

Kiểm tra từng lệnh thành công trước bước tiếp. Sao lưu thêm volume assets/audio/video cùng thời điểm bằng chức năng export volume hoặc công cụ backup Docker của môi trường; giữ manifest tên volume và cấu hình không chứa secret. DB chứa đường dẫn tới file, nên chỉ phục hồi DB sẽ thiếu media. Sau khi sao lưu xong:

```powershell
docker compose start web worker render-worker
```

Đặt tên file backup riêng cho từng lần để không ghi đè bản trước. Kiểm tra restore trên môi trường riêng trước khi cần rollback. Migration hiện có phần chặn downgrade; rollback cần bản backup phù hợp, không chỉ đổi image.

Sau khi cập nhật source và backup, chạy `docker compose up --build -d`, kiểm tra `/health` và log. `/health` kiểm tra DB/migration, không chứng minh key, TTS và toàn luồng video hoạt động.

## 8. Kiểm tra

```powershell
docker compose config --quiet
docker compose ps
docker compose logs --tail=100 web worker render-worker tts
docker compose exec -T web alembic current
```

Test tích hợp cần database riêng có tên kết thúc `_test` và thực hiện TRUNCATE trên database đó. Không dùng database sản xuất. Xem `tests/conftest.py` và các tài liệu validation nếu thiết lập môi trường test. Các script `live_*` gọi provider thật không phải test miễn phí/offline.
