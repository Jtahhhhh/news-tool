# Audit hiện trạng source — 30/09/2026

## Phạm vi và kết luận

Rà soát cấu hình Compose/Settings, routes, collector, credential/policy, luồng script/video, worker, template và hướng dẫn hiện có trên working tree, bao gồm các file chưa commit. Không sửa logic ứng dụng, không gọi LLM thật, không thay dữ liệu biên tập. Đây không phải kiểm toán bảo mật toàn diện hay audit toàn bộ mã upstream VieNeu-TTS.

Ứng dụng có đủ luồng nghiệp vụ local, nhưng phần video còn lỗi chức năng và race condition cần xử lý trước khi tăng tải hoặc coi việc duyệt là bảo đảm file đầu ra bất biến. Báo cáo cũ `SOURCE-AUDIT-2026-09-30.md` được giữ nguyên như lịch sử; kết quả dưới đây chỉ phản ánh đợt kiểm tra này.

## Phát hiện

### P1 — JPEG/PNG hợp lệ bị từ chối

`app/routes/video.py:98–100`: upload đặt `expected=kind`, với ảnh là `image`, rồi so sánh `ffprobe.stream.codec_type`. FFprobe biểu diễn ảnh tĩnh như stream `video`, nên điều kiện không đạt và file bị xóa. Điều này trái với ghi nhận “đã sửa” trong báo cáo trước.

Ảnh hưởng: người dùng không thể dùng ảnh theo luồng upload được quảng bá. Sửa kiểm tra stream ảnh theo kết quả FFprobe thực, giữ phân loại asset `image` riêng; thêm test upload PNG/JPEG thật và file hỏng. Hiện có thể dùng nền chữ hoặc MP4.

### P1 — Lease render không được gia hạn hoặc kiểm tra owner trước commit

`app/services/video_service.py:66,108,148–160,170–173`: lease 30 phút; checkpoint audio commit và thả khóa ban đầu. Giai đoạn render sau đó không gia hạn lease/kiểm tra quyền sở hữu ở lần ghi thành công. Worker thứ hai có thể recover job khi worker cũ vẫn render, dẫn tới ghi cùng tên output hoặc tranh chấp phiên bản.

Heartbeat process trong `app/render_worker.py` chỉ phục vụ healthcheck, không gia hạn lease database. Cần heartbeat lease và fencing/kiểm tra owner khi chuyển trạng thái, ghi version và xuất file. Giữ một render worker là hạn chế vận hành tạm thời, không thay thế sửa lỗi.

### P1 — Hủy video có thể chờ khóa lâu hoặc bị ghi đè thành succeeded

`app/routes/video.py:64–70`, `app/services/video_service.py:93–110,115–160`: execute giữ row lock qua giai đoạn tạo audio; request hủy dùng cùng row lock nên có thể chờ đến checkpoint. Sau kiểm tra hủy ở đầu cảnh cuối, chuỗi ghép/mux và commit thành công không kiểm tra lại cancelled/owner.

Nếu hủy trong bước cuối, job có thể vẫn ghi version và đổi thành succeeded. Cần rút ngắn transaction, kiểm tra hủy tại điểm commit bằng cập nhật có điều kiện và cơ chế dừng subprocess. Thêm test hủy trong TTS và trong mux cuối.

### P2 — Duyệt video không kiểm tra lại file hoặc hash nội dung

`app/routes/video.py:133` chỉ kiểm tra file tồn tại và `probe` đã lưu. File bị thay/hỏng sau render vẫn có thể được duyệt. Cần lưu hash đầu ra, kiểm tra hash và probe lúc duyệt; gắn review với nội dung cụ thể.

### P2 — Ràng buộc cảnh chưa bảo đảm đúng thứ tự/số lượng

`app/services/video_service.py:40–45` so sánh tập scene ID, nên payload `[1,2,2]` hoặc `[2,1]` có thể qua nếu tập ID khớp và narration từng cảnh đúng. Renderer dùng thứ tự payload, có thể lặp/đảo lời đọc đã duyệt. Cần schema scene chặt chẽ, kiểm tra độ dài, tính duy nhất và thứ tự theo script; test payload trùng/đảo cảnh.

### P2 — Hai cổng kiểm tra kịch bản có tiêu chí khác nhau

`app/services/script_service.py:361` có `renderable_version` yêu cầu bản mới nhất tuyệt đối và không có job tạo mới. Video dùng `app/services/video_service.py:33–39`, lấy bản mới nhất trong các bản approved có review, không dùng helper đó.

Do đó bản approved cũ vẫn có thể render khi có bản nháp mới hoặc job đang tạo bản mới. Cần chốt quy tắc nghiệp vụ và dùng chung một cổng; tài liệu sử dụng mới ghi đúng hành vi hiện tại, không khẳng định ràng buộc chặt hơn code.

### P2 — Render-worker nhận LLM key nếu key đặt trong `.env`

`compose.yaml:5–7` khai báo env_file trong anchor chung; render-worker kế thừa nguyên anchor. Chỉ web xóa env_file, còn worker có danh sách riêng. Vì vậy tuy tài liệu cũ nói chỉ worker nhận key, key trong `.env` cũng đến render-worker.

Nên dùng allowlist env riêng cho render-worker. Với source hiện tại, lưu key trong `.env.credentials` giúp giới hạn cho worker. Không có bằng chứng key bị lộ ra ngoài trong đợt kiểm tra này.

### P2 — Chữ trên video chưa xử lý đầy đủ ký tự và tràn dòng

`app/services/video_service.py:117–119` đưa text vào FFmpeg filter với escaping giới hạn, không wrap/đo khung chữ. Dấu đặc biệt có thể làm filter lỗi hoặc biến đổi hiển thị; câu dài có thể tràn hình. Nên dùng file text/ASS và test Unicode tiếng Việt, xuống dòng, dấu phần trăm, dấu gạch chéo và câu dài.

### P3 — Nút ẩn TEST không lọc toàn bộ dữ liệu

`app/routes/video.py:19,33–34`: query versions/jobs/assets không lọc `test_only`; `include_test` chủ yếu tác động danh sách script fake. Nhãn “Hiện riêng dữ liệu TEST” cũng không có nghĩa chỉ hiện test. Cần sửa bộ lọc và nhãn UI cho khớp.

### P3 — File tạm lỗi còn tồn tại

`app/services/video_service.py`: chỉ dọn thư mục job sau render thành công. Job lỗi có thể để lại file trong `media_tmp`; cần tác vụ dọn theo tuổi và trạng thái tham chiếu, không xóa file job đang chạy.

## Kiểm chứng trong đợt này

- `docker compose config --quiet`: không báo lỗi schema; môi trường sandbox có cảnh báo quyền đọc Docker config.
- Sau khi được phép truy cập Docker, `docker compose ps`: cả 5 dịch vụ db/web/worker/tts/render-worker healthy; có 2 orphan container smoke cũ, không xóa.
- Test source hiện tại bằng mount chỉ đọc vào container web, không truyền `TEST_DATABASE_URL`: **30 passed, 51 skipped**, 4,55 giây. Lệnh dùng `pytest -q -p no:cacheprovider` và working directory `/audit`, nên kiểm tra source working tree thay vì chỉ bản đóng trong image.
- 51 test cần DB không được chạy. Không dùng lại con số 81/81 trước đây để khẳng định lần kiểm tra này đạt toàn bộ.
- Không build lại image, không chạy smoke thật TTS/LLM hoặc toàn luồng video; các race condition nêu trên là phát hiện qua phân tích code, chưa tái hiện bằng test đồng thời trong đợt này.

## Thứ tự xử lý đề xuất

1. Sửa upload ảnh và thêm test ảnh thật.
2. Sửa ownership/lease/hủy render cùng test nhiều worker và crash/restart.
3. Siết thứ tự cảnh, thống nhất cổng kịch bản, kiểm tra hash file lúc duyệt.
4. Tách env render-worker, hoàn thiện text rendering, lọc TEST và dọn file tạm.
5. Chạy đủ suite trên DB test riêng, rồi nghiệm thu một luồng tin thật có người duyệt kịch bản/audio/video.

Tài liệu vận hành mới: [sử dụng](HUONG-DAN-SU-DUNG.md), [cấu hình](HUONG-DAN-CONFIG.md).
