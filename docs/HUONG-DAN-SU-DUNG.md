# Hướng dẫn sử dụng News Tool

**Bổ sung migration 0006:** có luồng gửi video đã duyệt sang TikTok để người dùng tự hoàn tất đăng; xem [hướng dẫn TikTok](HUONG-DAN-TIKTOK.md). Chưa có Direct Post. Video duyệt cũ phải được duyệt lại để gắn checksum trước khi gửi.

Cập nhật theo mã nguồn ngày 30/09/2026. Công cụ chạy local: lấy tin → chọn tin → tạo và duyệt kịch bản → nghe audio → dựng và duyệt video dọc. **Chưa có chức năng đăng TikTok.**

## 1. Khởi động

Làm theo [hướng dẫn cấu hình](HUONG-DAN-CONFIG.md) để tạo file cấu hình và cấp API key. Trong PowerShell:

```powershell
Set-Location D:\tiktok_tool\news-tool
docker compose up --build -d
docker compose ps
```

Mở <http://localhost:8000> (thay 8000 nếu đổi `WEB_PORT`). Lần đầu TTS cần tải model; web có thể dùng trước khi TTS sẵn sàng. Web tự chạy migration đến `0005`; worker chờ web healthy.

## 2. Thêm nguồn và lấy tin

1. Vào **Nguồn** (`/sources`), nhập tên, URL RSS/Atom hoặc trang danh sách HTML, chủ đề và chu kỳ lấy tin.
2. Với HTML, chọn adapter `generic` hoặc `vnexpress` phù hợp cấu trúc trang. Đây không phải trình duyệt chạy JavaScript và không tải toàn văn từng bài.
3. Bật nguồn để worker lấy định kỳ. Chu kỳ hợp lệ là 1–10080 phút. Có thể bấm lấy tin thủ công.
4. Xem `/jobs`: trạng thái, số lần thử, số bài mới/trùng/lỗi và thông báo chẩn đoán. Job lấy tin thất bại có thể thử lại thủ công.
5. Xem `/articles`, tìm theo từ khóa, nguồn, chủ đề hoặc ngày. Bộ lọc ngày hiện dùng ranh giới ngày **UTC**, không phải giờ Việt Nam.

Nguồn private/local bị chặn mặc định. Một nguồn trả HTTP thành công nhưng không có bài có thể do RSS rỗng hoặc adapter HTML không còn khớp. Nội dung lưu là metadata/tóm tắt, tối đa 4000 ký tự mỗi bài.

## 3. Chọn và gom tin

Tại **Chọn tin** (`/selection`), kiểm tra các nhóm sự kiện và lý do chấm điểm; chọn, bỏ qua hoặc đưa về chờ duyệt. Có thể đổi tên nhóm, tách bài sang nhóm mới hoặc chuyển vào nhóm có sẵn. Hai nhóm phải có cùng quyết định trước khi chuyển bài.

Sau khi thay từ khóa xếp hạng, yêu cầu chấm điểm lại. Điểm hỗ trợ sắp xếp, không thay thế kiểm chứng nội dung. Các nhóm đã chọn nằm ở `/selected`; nút xuất JSON tải dữ liệu nhóm/bài/nguồn, không xuất kịch bản hoặc video.

## 4. Tạo kịch bản

1. Từ **Tin đã chọn**, bấm **Viết kịch bản**.
2. Chọn Gemini hoặc DeepSeek, giọng trung lập/gần gũi, thời lượng 15–90 giây (mặc định 45), và nhận xét nếu cần.
3. Gửi yêu cầu rồi theo dõi job. Lấy tin và chấm điểm không tự gọi LLM. Tạo kịch bản và thử kết nối có thể sử dụng quota/tính phí nhà cung cấp.
4. Mở bản nháp, kiểm tra hook, lời đọc từng cảnh, chữ trên màn hình, số liệu, trích dẫn và nguồn chụp lại.

Worker chụp tối đa 5 bài. Nguồn quá ngắn bị chặn trước khi gọi API. `insufficient_evidence` nghĩa là thiếu dữ kiện; `validation_error` nghĩa là đầu ra không đạt kiểm tra cấu trúc. Hai trường hợp này không thể duyệt như bản nháp hợp lệ.

Sửa và lưu tạo phiên bản mới. Tạo lại mặc định dùng snapshot nguồn cũ; chọn cập nhật snapshot nếu muốn lấy dữ liệu hiện tại. Khi gặp HTTP 409 do bản nền cũ hoặc job đang chạy, tải lại trang và kiểm tra phiên bản mới nhất trước khi thao tác tiếp.

## 5. Duyệt kịch bản

Đọc đối chiếu nguồn, nhập tên người duyệt và nhận xét rồi duyệt/từ chối đúng phiên bản. Tên người duyệt là tên tự khai; ứng dụng chưa có tài khoản xác thực. Validator kiểm tra cấu trúc và một số ràng buộc, không chứng minh nội dung đúng.

Video hiện nhận **bản mới nhất trong các bản đã duyệt có lịch sử review**, không nhất thiết là bản mới nhất tuyệt đối. Nếu đang sửa hoặc tạo bản mới, hãy hoàn tất và duyệt bản đó trước khi dựng; xem hạn chế trong [báo cáo audit](AUDIT-HIEN-TRANG-2026-09-30.md).

## 6. Nghe thử và dựng video

1. Vào **Video** (`/videos`), chọn kịch bản thật đã duyệt.
2. Có thể bỏ trống asset để dùng nền chữ. Giao diện nhận JPEG/PNG/MP4 tối đa 25.000.000 byte, nhưng **mã hiện tại có lỗi từ chối ảnh JPEG/PNG hợp lệ**; tạm dùng MP4 hoặc nền chữ cho đến khi sửa.
3. Với mỗi cảnh, chọn asset, vị trí crop và mức phóng. Điền tác giả/giấy phép cho asset khi upload.
4. Chọn giọng (mặc định `Hải Đăng`), template `News Dark`, nháp 720×1280 hoặc bản cuối 1080×1920.
5. Quy tắc cách đọc là JSON, ví dụ `{"120":"một trăm hai mươi","API":"ây pi ai"}`. Kiểm tra nghe vì thay cách đọc có thể làm lệch ý nghĩa.
6. Bấm **Chỉ tạo audio để nghe thử**. Khi job đạt `audio_ready`, nghe từng cảnh; tải lại trang nếu các audio chưa xuất hiện.
7. Bấm **Tạo audio và render** với cùng cấu hình để tái sử dụng audio cache. Không sửa lời đọc tại bước video; muốn sửa phải tạo và duyệt kịch bản mới.
8. Mở phiên bản kết quả, xem hết MP4, nghe âm thanh, kiểm tra phụ đề, chữ tràn, crop và thời lượng thực rồi duyệt/từ chối.

Render mới tạo phiên bản chờ duyệt mới. Thời lượng theo audio thực tế, có thể vượt mục tiêu; không dựa vào số giây dự kiến để quyết định video đã đạt. Giữ một render worker vì cơ chế lease chưa an toàn khi render dài/chạy nhiều worker.

Dữ liệu giả lập/asset test phải giữ nhãn TEST. Nút ẩn TEST hiện chưa lọc toàn bộ danh sách video, job và asset; đừng hiểu đây là cơ chế cách ly dữ liệu thử nghiệm.

## 7. Xử lý trạng thái và lỗi

| Dấu hiệu | Thao tác |
|---|---|
| `queued` lâu | Kiểm tra worker tương ứng và dependency có healthy không |
| `retry_wait` | Xem lịch thử lại, không tạo job trùng để né thời gian chờ |
| `waiting_quota` | Kiểm tra quota group, giới hạn nội bộ và tài khoản provider tại `/llm` |
| `unknown_outcome` | Kiểm tra usage trước khi chủ động thử lại; lần trước có thể đã tính phí |
| Thiếu credential/model không được phép | Đối chiếu secret ref, model của credential và route trong `/llm` |
| Không có kịch bản để dựng | Cần `draft`, `approved` và lịch sử duyệt thật; xem lý do trên trang Video |
| TTS chậm/lỗi | Xem log TTS, tải model, bộ nhớ và `TTS_TIMEOUT_SECONDS` |
| Render lỗi | Xem log render-worker; kiểm tra asset, câu chữ đặc biệt, dung lượng volume |
| CSRF 403 | Tải lại trang rồi gửi lại thao tác |

Job video đi qua `queued → generating_audio → rendering → succeeded/failed/cancelled`. API có retry cho job lỗi/hủy; hủy chưa bảo đảm ngắt ngay TTS/FFmpeg hoặc ngăn kết quả commit ở cuối render. Không dựa vào trạng thái hủy để xóa file đang dùng.

## 8. Dừng và xem log

```powershell
docker compose logs --tail=100 web worker
docker compose logs --tail=100 render-worker tts
docker compose stop
docker compose start
```

`stop` giữ container và volume. `down` giữ named volume mặc định; **không dùng `down -v` nếu cần giữ dữ liệu**. Dữ liệu cần sao lưu gồm PostgreSQL và các volume media, xem hướng dẫn cấu hình.
