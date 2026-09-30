# Hướng dẫn kết nối và gửi video sang TikTok

Phiên bản source: migration `0006`, ngày 30/09/2026. Module dùng **Content Upload / FILE_UPLOAD**. Sau khi nhận video, TikTok gửi thông báo để người dùng mở trình biên tập và hoàn tất đăng. Không có thao tác đăng tự động bằng Direct Post trong bản này.

## 1. Chuẩn bị bên TikTok Developer

1. Đăng ký ứng dụng tại [TikTok for Developers](https://developers.tiktok.com/).
2. Cấu hình **Login Kit Web** và **Content Posting API**.
3. Xin phê duyệt scope `video.upload`; module cũng yêu cầu `user.info.basic` để hiện đúng tên tài khoản nhận. Người dùng phải đồng ý cấp các quyền này khi kết nối.
4. Đăng ký chính xác URL HTTPS dạng `https://ten-mien-cua-ban/tiktok/callback`. Không thêm query/fragment. Callback trong code không có dấu `/` cuối.
5. Lấy Client key và Client secret từ ứng dụng. API key Gemini/DeepSeek không dùng cho TikTok.

Login Kit Web yêu cầu callback HTTPS; `http://localhost:8000/tiktok/callback` không đáp ứng hợp đồng này. Cần reverse proxy HTTPS trỏ đến web local. Mở giao diện và bắt đầu kết nối bằng **cùng host HTTPS** với callback để cookie liên kết trình duyệt hoạt động. Không bắt đầu từ localhost rồi callback về tên miền khác.

Ứng dụng chưa có tài khoản đăng nhập/RBAC. Reverse proxy phải bảo vệ quyền truy cập toàn bộ giao diện (ví dụ xác thực ở proxy hoặc mạng riêng), chuyển đúng Host và HTTPS scheme từ proxy đáng tin cậy. Nếu dùng `FORWARDED_ALLOW_IPS`, cấu hình qua override riêng cho web với IP proxy thực tế; không tin tùy ý mọi proxy. Tắt log query của callback ở proxy vì query chứa code OAuth. Không tự mở ứng dụng local ra Internet chỉ để thử callback.

Tài liệu tham chiếu: [Login Kit Web](https://developers.tiktok.com/docs/en/login-kit-web), [bắt đầu Upload](https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content). Cấu hình local đúng không bảo đảm app đã được TikTok phê duyệt.

## 2. Cấu hình trên máy

Trong `news-tool`, sao chép `.env.tiktok.example` thành `.env.tiktok` nếu chưa có. File này chỉ cấp cho **web** và **publish-worker**. Điền trên máy:

```dotenv
TIKTOK_CLIENT_KEY=client_key_cua_app
TIKTOK_CLIENT_SECRET=client_secret_cua_app
TIKTOK_TOKEN_KEY=khoa_fernet_tao_mot_lan
```

Tạo khóa mã hóa sau khi build image ứng dụng:

```powershell
docker compose build web
docker compose run --rm --no-deps web python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Sao chép kết quả vào `TIKTOK_TOKEN_KEY`. Giữ khóa riêng cùng bản backup an toàn; mất hoặc thay khóa làm token/URL upload đã mã hóa không đọc được. Không tạo lại khóa mỗi lần khởi động. Không dán khóa vào chat, UI hoặc Git. `.env.tiktok` đã bị loại khỏi Git và build context.

Trong `.env` chính:

```dotenv
TIKTOK_ENABLED=true
TIKTOK_DIRECT_POST_ENABLED=false
TIKTOK_REDIRECT_URI=https://ten-mien-cua-ban/tiktok/callback
TIKTOK_TIMEOUT_SECONDS=60
```

Thay URL bằng callback thực đã đăng ký. Các giá trị này được Compose allowlist; đừng đặt chúng vào `.env.tiktok` vì các giá trị `environment` của Compose sẽ ưu tiên hơn env_file.

`TIKTOK_TIMEOUT_SECONDS` được giới hạn 5–120 giây cho timeout HTTP. Direct Post chưa có adapter; đặt cờ Direct Post thành true sẽ chặn cấu hình, không kích hoạt đăng tự động.

Sau khi backup database/media và kiểm tra không có job render đang chạy:

```powershell
docker compose config --quiet
docker compose up --build -d web worker render-worker publish-worker
docker compose ps
docker compose exec -T web alembic current
```

Web chạy migration `0006` trước khi các worker mới khởi động. Không dùng `down -v`. Các lệnh trên là hướng dẫn triển khai; đợt phát triển module không tự migrate database biên tập hiện tại.

Nếu chưa có credential/callback, giữ `TIKTOK_ENABLED=false`. Trang TikTok vẫn mở được để xem dữ liệu nhưng không cho tạo kết nối/upload. Khi đổi secret, tạo lại web và publish-worker bằng `up -d --force-recreate web publish-worker`; `restart` không nạp environment mới.

Có thể dùng biến `TIKTOK_CLIENT_SECRET_FILE`/`TIKTOK_TOKEN_KEY_FILE` trỏ tới Docker secret mount riêng cho hai service này thay vì giá trị trực tiếp. Không mount khóa vào worker LLM/TTS/render-worker.

## 3. Kết nối và ngắt tài khoản

1. Mở ứng dụng qua host HTTPS đã cấu hình, vào **TikTok** (`/publishing`).
2. Bấm **Kết nối TikTok**, đăng nhập đúng tài khoản và đồng ý quyền.
3. Sau callback, kiểm tra tên tài khoản nhận và trạng thái `connected`.

OAuth state hết hạn sau 10 phút, gắn với trình duyệt và chỉ dùng một lần. Callback lỗi hoặc đã sử dụng cần bắt đầu kết nối lại. Token mã hóa lưu trong PostgreSQL; browser không nhận access token/refresh token.

Token hết hạn được refresh dưới khóa tài khoản. Nếu refresh không rõ kết quả hoặc worker chết lúc refresh, tài khoản yêu cầu kết nối lại, không tự lặp lại token có thể đã bị xoay. `refreshing` còn tồn tại sau restart cũng cần kết nối lại.

**Ngắt kết nối** chặn gửi local trước, rồi gọi revoke. `disconnected` nghĩa là đã xác nhận; `revoke_unconfirmed` nghĩa là chưa xác nhận TikTok thu hồi quyền. Có thể thử ngắt lại hoặc thu hồi quyền trong TikTok. Ngắt kết nối không xóa video đã gửi và không thu hồi request đang bay trên mạng.

## 4. Video nào được phép gửi?

Server kiểm tra cả khi tạo job và ngay trước khi truyền file:

- Chính xác `video_version_id` được chọn, trạng thái approved và có review approved gắn checksum.
- Không mang nhãn TEST; không dùng script fake hoặc asset TEST.
- MP4 tồn tại, đường dẫn thuộc volume video và SHA-256 khớp file đã duyệt.
- Tài khoản đúng snapshot và có quyền `video.upload`.

Video duyệt từ trước migration chưa có checksum phải mở trang Video để **duyệt lại file**. Không tự backfill checksum rồi coi đó là đồng ý của người duyệt. Render mới luôn cần duyệt riêng và xác nhận gửi riêng.

App hỗ trợ MP4, 3–600 giây, codec H.264/H.265/VP8/VP9, 23–60 fps, mỗi chiều 360–4096 px và tối đa 4.000.000.000 byte. Đây là giới hạn của module (có thể chặt hơn API); TikTok vẫn có thể từ chối do tài khoản/nội dung. Worker tạo bản sao riêng để hash và truyền đúng bytes, tránh file render bị thay giữa lúc kiểm tra và upload. Cần chỗ trống trong filesystem container cho bản sao video, tối đa bằng dung lượng file mỗi worker.

Video lớn được chia chunk 32.000.000 byte, phần dư gộp vào chunk cuối; gửi tuần tự. URL upload có hạn, được mã hóa trong DB và không trả về UI. Tham khảo [media transfer](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide).

## 5. Gửi và hoàn tất đăng

1. Chọn video đã duyệt và xem MP4 preview.
2. Chọn tài khoản nhận, kiểm tra dòng tóm tắt video/phiên bản → tài khoản.
3. Điền caption/hashtag và bấm **Sao chép caption**. Video Upload API không có trường caption trong request init; nội dung này chỉ lưu local, không được tự gửi kèm file.
4. Đánh dấu xác nhận gửi đúng file sang đúng tài khoản, rồi bấm **Gửi sang TikTok**.
5. Theo dõi lịch sử gửi. Khi thấy **Đã chuyển sang TikTok — cần hoàn tất đăng**, mở thông báo hộp thư trong TikTok, chỉnh sửa, dán caption và tự hoàn tất đăng.
6. App chỉ hiện **Đã đăng — TikTok xác nhận** khi status API trả `PUBLISH_COMPLETE`. `SEND_TO_USER_INBOX` chưa phải đã đăng. Bài không công khai có thể không có public post ID.

Hợp đồng API: [Upload video](https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video), [status](https://developers.tiktok.com/docs/en/content-posting-api-reference-get-video-status).

## 6. Lỗi, phục hồi và chống gửi trùng

| Trạng thái/tình huống | Hành vi và cách xử lý |
|---|---|
| Chờ gửi | Worker chờ đến lịch; kiểm tra publish-worker healthy |
| 429 init được xác nhận | Thử lại với backoff/Retry-After, tối đa 3 lượt init |
| Init timeout/5xx/malformed hoặc crash sau intent | `unknown_outcome`; không tự init lại vì có thể TikTok đã nhận |
| Có publish_id, PUT timeout/restart | Đối chiếu cùng publish_id; chỉ tự tiếp tục nếu API xác nhận offset ở ranh giới chunk hợp lệ và URL còn hạn |
| Không có publish_id | Không thể đối chiếu tự động; kiểm tra TikTok/tài khoản, không sửa DB để gửi lại |
| File thay đổi sau duyệt | Chặn gửi; render phiên bản mới và duyệt lại |
| Quyền bị thu hồi/token lỗi | Kết nối lại tài khoản; kiểm tra trạng thái job cũ |
| Unknown có publish_id | Nút **Kiểm tra trạng thái cũ** chỉ đọc trạng thái, không khởi tạo upload mới |
| Dừng local | Chỉ hủy chắc chắn trước gửi; sau khi đã gửi không có cam kết xóa remote |

Một cặp video-version/tài khoản chỉ có **một job**, kể cả failed/unknown/cancelled. Bấm hai lần hoặc đổi idempotency key không tạo upload trùng. Bản này không có nút retry init thủ công; nếu cần gửi lại sau kết quả chắc chắn, phải tạo/duyệt phiên bản mới và xác nhận mới. Với unknown, luôn kiểm tra remote trước, không tạo phiên bản mới chỉ để né bảo vệ.

Worker dùng khóa job PostgreSQL giữ suốt thao tác, lease 180 giây gia hạn mỗi 10 giây và kiểm tra owner lúc ghi kết quả. Lỗi PUT không gửi lại mù quáng. Tối đa số chunk cần thiết cộng 5 lượt PUT; lỗi liên tiếp khi đối chiếu tối đa 5. Poll tự động tối đa 288 lượt rồi dừng để người dùng kiểm tra thủ công; inbox được kiểm tra mỗi 5 phút. Theo dõi local không có webhook trong bản này.

Giới hạn init/status nội bộ theo tài khoản là 6/30 lượt mỗi 61 giây, không thay thế quota TikTok dùng chung với ứng dụng khác. Trần pending upload của provider cũng có thể chặn gửi; không thay key để né quota.

## 7. Theo dõi và backup

```powershell
docker compose logs --tail=100 publish-worker web
docker compose exec -T web alembic current
```

Không bật HTTP debug hay proxy query logging cho OAuth/upload. Log attempt chỉ chứa loại thao tác, kết quả đã chuẩn hóa, HTTP status và thời gian. Không có raw response, token hoặc signed URL trong UI/log ứng dụng.

Backup đồng bộ database, media video và **khóa mã hóa ở nơi riêng có kiểm soát truy cập**. Khóa không nằm trong DB. Module không chạy downgrade xóa audit. Xem thêm [cấu hình chung](HUONG-DAN-CONFIG.md).

## 8. Kiểm thử thật và điều kiện nghiệm thu

Test mock chứng minh logic local, không chứng minh TikTok đã phê duyệt app hoặc nhận video. Lượt thật cần đủ client credentials, callback HTTPS, scope đã phê duyệt, tài khoản đã cấp quyền và một video thật đã duyệt. Người dùng phải chọn video/tài khoản và xác nhận gửi một lần, kiểm tra inbox TikTok và bước hoàn tất đăng. Ghi nhận publish status, thời gian, kết quả mà không lưu token/signed URL.

Direct Post vẫn chưa triển khai. Ngoài audit/scope và UX riêng, [hướng dẫn Direct Post](https://developers.tiktok.com/docs/en/content-sharing-guidelines?enter_method=left_navigation) có ràng buộc về mục đích sử dụng, trong đó công cụ chỉ dành cho tài khoản của cá nhân/nhóm nội bộ không phù hợp yêu cầu nêu trên. Không coi việc bật một cờ cấu hình là đủ điều kiện sử dụng.
