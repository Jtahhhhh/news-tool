# Audit số cảnh — 01/10/2026

## Kết luận đã chứng minh

Không có giới hạn cứng 2 cảnh trong schema, validator, DB, giao diện hoặc renderer đã kiểm tra. Video #4 (video version 1, video job #7) của story/event **373** dùng **ScriptVersion #10, bản 3, origin=edit**, không phải phản hồi Groq trực tiếp. Bản này có hai cảnh, thừa kế từ bản #9 của job #13 rồi được sửa lời theo chứng cứ để qua validator. Nhãn đầu trang chỉ ghi Groq/model trước đây dễ làm hiểu nhầm bản chỉnh sửa là output model; đã bổ sung nhãn và liên kết bản cha.

### Chuỗi dữ liệu

| Bước | Bằng chứng | Số cảnh |
|---|---|---:|
| Job #12, attempt #12 | Groq HTTP 200, raw JSON | 3 |
| Job #12, attempt #13 | Repair HTTP 200, raw JSON, ScriptVersion #8/bản 1 | 4 |
| Job #13, attempt #14 | Groq HTTP 200, raw JSON | 2 |
| Job #13, attempt #15 | Repair HTTP 200, ScriptVersion #9/bản 2 | 2 |
| Validator hai job | Lỗi bám nguồn; không lưu draft và không tự xóa cảnh | Không áp dụng |
| Chỉnh sửa #10/bản 3 | parent_version_id=9, origin=edit, warnings ghi sửa thủ công | 2 |
| VideoJob #7 input và checkpoint | Hai narration tương ứng bản #10 | 2 |
| VideoVersion #4 timeline | Hai audio #5/#6 và hai cảnh | 2 |

Điểm xuất hiện hai cảnh đầu tiên trong nhánh video này là **raw response attempt #14 của job #13**. Repair giữ hai cảnh, bản sửa tay giữ hai cảnh, render không làm mất cảnh. Chưa chứng minh được nguyên nhân nhân quả khiến model chọn hai thay vì ba/bốn ở lần sinh đó; không quy kết thiếu dữ kiện hay chất lượng model.

Thời lượng job #12/#13 và snapshot gửi prompt: **45 giây**. Bản sửa #10 lên kế hoạch **13+17=30 giây**; video config ghi 30. Audio thực tế **6,48+8,56=15,04 giây**, video đo bằng ffprobe **15,033333 giây**. Không có chứng cứ default/fallback đổi yêu cầu người dùng thành 15 giây. Log lưu yêu cầu 45 giây; không có bản ghi thao tác bàn phím của người dùng để chứng minh thêm ngoài đó.

## Đầu vào và prompt

- Một nguồn `article-9991`, bài VnExpress về doanh nghiệp tư nhân tại Đà Nẵng; RSS summary 165 ký tự, nhưng snapshot gửi model là **toàn văn 2.924 ký tự**, không phải summary.
- Một khối văn bản sau chuẩn hóa khoảng trắng; số đoạn HTML gốc không được lưu nên không thể phục dựng chính xác từ snapshot. Có lặp tiêu đề và chú thích ảnh; chưa thấy mất ví dụ Thaco/FPT/Sun Group, nhà máy năm 2017, PCI 2025/top 5, phát biểu ông Nguyễn Duy Hưng.
- Lấy lại bài qua extractor hiện tại: 2.924 ký tự, **khớp chính xác snapshot**. Kết quả trong `results/scene-audit/source-comparison.json`. Đây là so sánh nội dung trích xuất; không chứng minh extractor bao phủ mọi phần HTML.
- Nguồn có giới hạn 20.000 ký tự; không áp dụng ngưỡng 4.000 ký tự. Mức 4.000 thuộc feedback/thông báo lỗi ở các vị trí khác.
- Snapshot bất biến, tạo lại mặc định dùng snapshot cũ; checkbox `refresh_sources` yêu cầu tải lại. Đây là hành vi có chủ ý; bài hiện tại khớp snapshot nên không có chứng cứ stale gây lỗi này.
- Prompt không có ví dụ cố định hai cảnh; không có `minItems/maxItems` trên scenes. Mục tiêu là giới hạn tối đa, được viết ngắn nếu nguồn thiếu; không có yêu cầu số cảnh hoặc độ dài lời đọc tối thiểu.
- `max_completion_tokens=8192`, `temperature=0.2`, `reasoning_effort=low`, `stream=false`, không có `stop` tùy chỉnh. Historical successful parsing đòi `finish_reason=stop`; log cũ chưa lưu trường này tách biệt. Đã thêm lưu `finish_reason` cho các lần sau.
- `scenes` hiện là **cảnh lời đọc kèm một hình**, renderer TTS từng cảnh và dựng một segment tương ứng. Chưa có mô hình nhiều cảnh hình cho một cảnh lời; không thêm `visual_scenes` chưa được renderer hỗ trợ.

## Thử nghiệm có kiểm soát

Đã xác nhận provider/endpoint/model từ job và request đóng băng, dùng một credential local. Người dùng đã cho phép cụ thể việc gửi snapshot đến Groq. Script gửi từng lần, không retry HTTP, không đổi key, ghi ledger trước mỗi send, từ chối chạy lại cùng ledger.

| Thử nghiệm | HTTP | Kết quả |
|---|---:|---|
| Schema strict tối thiểu | 200 | JSON `ok`, finish_reason=stop |
| A: phát lại request đã lưu của #13 | 200 | 2 cảnh; lỗi grounding cảnh 1, từ “doanh” không trong quote |
| B: toàn văn | 200 | 2 cảnh; cùng lỗi. Vì A đã có toàn văn, B là đối chứng trùng, không phải phép thay summary thành toàn văn |
| C: 45 → 15 giây | 429 | Giới hạn TPM: 8.000; chưa có output để đánh giá |
| D, E, F và lặp baseline/best | Chưa gửi | Dừng toàn đợt khi quota trả 429 |

**Đã gửi 4/12 request**, gồm probe tối thiểu và request 429. Không tiếp tục sau thời gian chờ của provider. A phát lại **request repair cuối cùng** có `PREVIOUS_OUTPUT_JSON` hai cảnh; không phải fresh generation. Vì vậy hai lượt A/B không chứng minh model luôn sinh hai cảnh, và không đủ ba lượt độc lập baseline.

Chưa có cấu hình tốt nhất hợp lệ, nên không sửa prompt để ép nhiều cảnh và không dựng baseline/best như thể đã đạt chất lượng. Raw A/B hợp lệ JSON nhưng lỗi grounding; không được đưa qua cổng duyệt/render. Video #4 là baseline lịch sử đã có, không phải kết quả A/B mới.

## Bản sửa tối thiểu và kiểm thử

- Hiển thị rõ bản chỉnh sửa, nguồn gốc model và bản cha ở trang review.
- Tách profile schema gửi Groq khỏi validator nội bộ; giữ nguyên contract 1.0, lưu hash schema provider riêng. Không bỏ kiểm tra dẫn chứng hay ép `minItems`.
- Lưu `finish_reason` trong diagnostics trước khi parse, kể cả output bị cắt.
- Fixture kiểm tra 1/2/4/8 cảnh qua parse → validator → serialize → video snapshot; integration kiểm tra 4 cảnh qua DB → edit → duyệt → UI → enqueue render. Có test nguồn dài, nguồn mâu thuẫn, chỉ dẫn giả, sai số/nguồn, thiếu mức độ chắc chắn và nhánh không đủ chứng cứ.
- Các test deterministic không chứng nhận khả năng hiểu mâu thuẫn/người nói của model. Validator dùng từ vựng bảo thủ có thể từ chối cách diễn đạt hợp lý; không nới lỏng chỉ để đạt nhiều cảnh.

Kết quả cuối: **73 passed, 0 failed, 0 skipped** trong 15,98 giây, DB riêng `news_tool_scene_audit_test`, media riêng trong `/tmp`. 18 cảnh báo deprecation từ dependencies. Bộ chạy dùng `LLM_FETCH_ARTICLE=false` cho test request đóng băng; các test fetch riêng tự mock/bật theo fixture. Có một lần thiết lập runner sai cache settings bị chặn bởi guard DB và một lần test cũ không khớp môi trường fetch=true; đã sửa cấu hình runner và chạy lại toàn bộ bốn file. Không sửa/xóa dữ liệu production. `results/scene-audit/verification.json` ghi kết quả. `git diff --check` qua; JSON artifact đọc được và không thấy mẫu credential API phổ biến.

## Bàn giao và phần chưa hoàn tất

`results/scene-audit/history.json`: nguồn, các job/request đã lưu, raw output, lỗi validation, video input/checkpoint/timeline. `input.json` và `source-summary.json`: fixture nguồn. `ledger.json`: request không có Authorization, response gốc đã che API secrets, usage, độ trễ, số cảnh và lỗi. `scripts/audit_scene_count.py`: benchmark giới hạn 12 sends, dừng quota. `scripts/verify_scene_sources.py`: so sánh extractor, xuất provider schema, không gọi LLM.

**Chưa đạt toàn bộ tiêu chí hoàn tất**: chưa chạy xong C–F, chưa lặp ba lần baseline/best, chưa kiểm chứng min/max scenes thực tế trên API, chưa có bản tốt nhất hợp lệ và cặp render so sánh. Cần một đợt thử tiếp theo được xác định ngân sách sau khi xử lý quota; không tự tiếp tục hoặc xoay key. Các thay đổi hiện ở source workspace, chưa triển khai lại dịch vụ production.
