# Dùng Groq để tạo kịch bản

## Cấu hình

Trong `.env`, đặt các giá trị không bí mật:

```dotenv
LLM_PROVIDER=groq
GROQ_MODEL=openai/gpt-oss-120b
LLM_FALLBACK_PROVIDER=gemini
LLM_MAX_CONCURRENCY=2
LLM_FETCH_ARTICLE=true
ARTICLE_FETCH_TIMEOUT_SECONDS=3
```

Đặt key thật trong `.env.credentials` trên máy chạy worker:

```dotenv
GROQ_API_KEY=thay_bang_key_cua_ban
GEMINI_API_KEY=thay_bang_key_gemini_neu_dung_fallback
```

Không điền key thật vào giao diện, chat, Git hay file ví dụ. `.env.credentials` đã bị loại khỏi Git và Docker build context. Web không nhận các key LLM; `llm-worker` đọc key. Sau khi sửa file môi trường, cần tạo lại container để nhận giá trị mới; `restart` đơn thuần không nạp lại env.

Muốn tắt fallback, để `LLM_FALLBACK_PROVIDER=` và bỏ chọn fallback trên trang cấu hình. Không có cấu hình mới thì mặc định code vẫn là Gemini để giữ tương thích.

**Database đã có policy:** env chỉ khởi tạo policy lần đầu, không ghi đè cấu hình đã lưu. Vào **Cấu hình LLM**, đặt **Thứ tự provider** là `groq,gemini`, model Groq là `openai/gpt-oss-120b`, cho phép hai provider nhận dữ liệu, bật fallback khi lỗi dịch vụ và lưu. Đặt chờ retry ban đầu `1` giây nếu policy cũ đang là `15`. Mỗi provider tối đa 3 lần gửi khi lỗi tạm thời, tổng toàn job tối đa 6; giới hạn ngân sách thấp hơn vẫn có hiệu lực.

## Thêm key trên giao diện

Với key mặc định, dùng credential `groq mặc định` và tham chiếu `env:GROQ_API_KEY`. Nếu cần thêm key riêng, khai báo trong `.env.credentials`:

```dotenv
LLM_KEY_GROQ_A=thay_bang_key_cua_ban
```

Sau đó điền:

| Trường | Ví dụ |
|---|---|
| Tên gợi nhớ | Groq A |
| Provider | Groq |
| Project / tài khoản | Tên project/organization sở hữu key |
| Tên tham chiếu secret | `env:LLM_KEY_GROQ_A` |
| Nhóm quota dùng chung | `groq-project-a` |
| Model được phép | `openai/gpt-oss-120b` |
| Mức ưu tiên | `100` |

Key cùng project dùng cùng nhóm quota; thêm key không làm tăng quota của project. Số ưu tiên nhỏ hơn được chọn trước. Chờ worker cập nhật trạng thái khoảng 30 giây sau khi container đã nhận env mới. Trạng thái có key chỉ xác nhận worker đọc được secret, không chứng minh key còn quyền/quota.

**Lỗi trong ảnh trước:** `LLM_KEY_GEMINI_A` và `env.credentials:LLM_KEY_GEMINI_A` đều sai cú pháp. Giá trị đúng là **`env:LLM_KEY_GEMINI_A`**. `.env.credentials` là tên file lưu giá trị, không phải tiền tố tham chiếu.

Nút **Thử kết nối** gửi một yêu cầu ngắn, tối đa một lần, không fallback và có thể tiêu tốn quota. Chỉ chạy khi chủ động bấm. `/health` chỉ kiểm tra ứng dụng/database/migration, không gọi model.

## Cập nhật và chạy

Sau khi sao lưu database và giữ nguyên các module video/TikTok hiện có trong workspace:

```powershell
docker compose up -d --build web worker llm-worker
docker compose ps
docker compose logs --tail 100 llm-worker
```

Web tự chạy migration đến `0007`. Migration này nối tiếp `0006` của TikTok; phải có đầy đủ migration `0005`, `0006` và source tương ứng. Các commit Groq được tách riêng, không tự gộp thay đổi video/TikTok có sẵn nhưng chưa commit. Lệnh trên chưa được thực thi lên dịch vụ đang chạy trong đợt kiểm thử này.

`worker` trong Compose chỉ thu thập tin. `llm-worker` xử lý hàng đợi LLM, có tối đa `LLM_MAX_CONCURRENCY` tiến trình. Giới hạn đặt trước trong PostgreSQL còn bảo đảm tổng request LLM trên nhiều worker không vượt mức này. Giới hạn từng nhóm quota/model có thể thấp hơn. Chạy trực tiếp `python -m app.worker` vẫn hỗ trợ chế độ kết hợp cũ; dùng Compose để tách crawl và LLM.

## Sử dụng

1. Chọn nhóm tin, mở tạo kịch bản, chọn Groq và thời lượng mục tiêu.
2. Worker thử đọc nội dung bài từ URL, loại thành phần điều hướng/quảng cáo phổ biến. Nếu không đọc được, chỉ dùng tiêu đề và tóm tắt RSS. Không vượt paywall hoặc trang cần đăng nhập.
3. Nguồn, schema, prompt và payload được cố định trước lần gọi model. Retry và fallback dùng lại cùng nguồn; chọn làm mới nguồn khi cần đọc lại bài.
4. Đầu ra phải đúng schema và trích dẫn. Sai nội dung/cấu trúc sẽ được yêu cầu sửa **một lần trên cùng provider**, không chuyển sang Gemini để bỏ qua grounding. Kết quả vẫn lỗi không trở thành bản nháp có thể duyệt.
5. Xem nguồn và bản nháp, sửa lời đọc nếu cần, duyệt kịch bản rồi tạo audio/video. Kịch bản ít dữ kiện được phép ngắn hơn mục tiêu. Thời lượng audio thật mới quyết định timeline video.

Schema public giữ nguyên `hook`, `claims[].evidence`, `scenes[].scene_id`, `scenes[].seconds`, `claim_ids`…; không thêm/đổi thành `figures`, `citations` hay `scene_number`.

## Lỗi và cách xử lý

| Hiện tượng | Xử lý |
|---|---|
| `missing_secret` | Kiểm tra đúng tên biến trong `.env.credentials`, tham chiếu `env:...`, tạo lại `llm-worker`. |
| Provider chưa được cho phép | Cập nhật policy đã lưu trên `/llm`; thay env không ghi đè policy cũ. |
| HTTP 400/401/403 | Dừng ngay; kiểm tra schema/model, key hoặc quyền. Không tự chuyển provider. |
| HTTP 429/500/502/503/504 | Thử lại có giới hạn: mặc định chờ 1–2 giây, rồi 3–5 giây; tôn trọng `Retry-After` dài hơn. Sau 3 lần lỗi mới chuyển fallback được cho phép. |
| Timeout/kết quả không rõ | Không tự gửi lại vì lần trước có thể đã tính phí; xem lịch sử trước khi tạo yêu cầu mới. |
| `grounding_error` | So lời đọc với trích dẫn. Bỏ số liệu/đơn vị, điều kiện, đối tượng, lợi ích không có trong nguồn; giữ các từ như “đề xuất”, “dự kiến”, “chưa”. |
| `insufficient_evidence` | Bổ sung nguồn rồi tạo lại; không ép kéo dài tin ít dữ kiện. |
| Đang chờ concurrency/quota/circuit | Xem lịch sử job và nhóm quota. Bộ đếm nội bộ không phải quota thực của nhà cung cấp. |

Grounding là bộ lọc bảo thủ theo số liệu, từ ngữ, trích dẫn và mức độ chắc chắn, **không phải bằng chứng máy đã hiểu đúng mọi mệnh đề**. Nó có thể từ chối cách diễn đạt đồng nghĩa chưa có trong trích dẫn, và không chứng minh được mọi quan hệ giữa các từ. Người biên tập vẫn phải đối chiếu nguồn trước khi duyệt. Bộ kiểm thử học bổng kiểm tra các dữ kiện được cung cấp và những suy diễn đã nêu; không khẳng định độ chính xác tuyệt đối với mọi bài báo.

Log vận hành ghi provider/model, request ID, article IDs, thời gian chờ/xử lý, token khi API trả usage, retry, kết quả validator và fallback; không ghi toàn bộ bài/prompt. Payload và phản hồi vẫn được lưu trong database để đối chiếu lịch sử, nên database cần được bảo vệ như dữ liệu nội bộ.

Groq dùng [Structured Outputs](https://console.groq.com/docs/structured-outputs) với `json_schema` và `strict: true`. Tham khảo [API](https://console.groq.com/docs/api-reference) và [mã lỗi](https://console.groq.com/docs/errors) khi cấu hình model/quyền truy cập.
