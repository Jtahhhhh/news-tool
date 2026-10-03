# Groq schema validation — 01/10/2026

## Cấu hình đã xác minh và tài liệu

Job #13 dùng Groq `openai/gpt-oss-120b`, endpoint `https://api.groq.com/openai/v1/chat/completions`. Không dựa riêng vào default `.env`. Request 8192 completion tokens, temperature 0.2, reasoning low, không stream, không custom stop.

[Tài liệu Structured Outputs chính thức](https://console.groq.com/docs/structured-outputs), đọc ngày audit, liệt kê model này hỗ trợ JSON Schema strict và best-effort; JSON Object là chế độ JSON không bảo đảm contract. Strict đòi tất cả thuộc tính required và object có additionalProperties=false. Tài liệu đã đọc không nêu rõ `minItems/maxItems`, nên không suy ra hỗ trợ các keyword này từ riêng mô tả strict.

| Chế độ | Bằng chứng live trong đợt này |
|---|---|
| Text | Chưa test riêng |
| JSON Object | Chưa test riêng |
| JSON Schema best-effort | Chưa test riêng |
| JSON Schema strict tối thiểu | HTTP 200, stop |
| JSON Schema strict Output 1.0 đầy đủ | A/B HTTP 200, stop, parse được; grounding không đạt |
| Strict scenes maxItems=8 | Chưa chạy E vì 429 ở C |

HTTP 429 là lỗi request/quota trước nội dung, không phải lỗi schema. Lỗi A/B là lỗi nội dung do validator local sau HTTP 200, không phải JSON/schema request bị API từ chối. Raw response/usage/time lưu trong `results/scene-audit/ledger.json`.

## Schema chuẩn và tương thích

Giữ **schema_version=1.0** để đọc/edit/review/render các bản cũ không cần migration. Tách `app/llm/provider_schema.py` profile **groq-strict-v1** khỏi `Output` và `validate_output`. Profile hiện deepcopy, kiểm tra required/closed objects rồi giữ nguyên keywords đã được request đầy đủ chấp nhận. Không tự bỏ ràng buộc khi HTTP 400. Thay đổi capability về sau phải dùng profile mới và live test; validator nội bộ luôn giữ toàn bộ ràng buộc nội dung.

Request lưu `schema_hash` nội bộ, `provider_schema_hash`, `provider_schema_profile`, và hash toàn payload. Request cũ đã đóng băng tiếp tục được phát nguyên trạng; không ghi đè lịch sử. `results/scene-audit/provider-schema.json` là bản schema profile xuất ra.

| Thành phần | Contract hiện dùng / thiết kế |
|---|---|
| Định danh | schema_version, story_id |
| Quyết định | decision, reason, warnings |
| Nội dung | title, hook, caption |
| Mục tiêu thời lượng | Input/snapshot.target_seconds; không tự thêm target_duration_seconds vào Output 1.0 |
| Dẫn chứng | claims[].claim_id/text/evidence[].source_id/quote |
| Cảnh lời | scenes[].scene_id/narration/on_screen_text/claim_ids/seconds |
| Vai trò cảnh | Chưa có field role trong 1.0; chỉ thêm ở phiên bản mới nếu thực nghiệm chứng minh cần thiết |
| Cảnh hình | visual_brief cùng cảnh lời; không có visual_scenes độc lập khi renderer còn quan hệ 1:1 |

Draft phải có title/hook/caption/claims/scenes. Insufficient_evidence có reason, publishable fields rỗng, không ép scenes tối thiểu. Hook bằng narration cảnh đầu, renderer chỉ đọc scenes. Claim ID duy nhất, nguồn tồn tại, quote nguyên văn trong snapshot, scene ID liên tiếp và claim_ids hợp lệ. Planned seconds không vượt target+3; thời gian cuối lấy từ TTS/ffprobe.

Không áp dụng floor số cảnh chung. Nếu thêm minItems trong thử nghiệm cần ràng buộc riêng nhánh draft và không làm mất nhánh insufficient_evidence; không nhồi dữ kiện để đủ số cảnh. Test E hiện chỉ dùng maxItems=8, chưa chứng minh keyword được provider hỗ trợ. Số cảnh nên theo các ý độc lập và thời lượng audio, không phải mục tiêu chất lượng duy nhất.

Schema 1.1/2.0 có thể thêm role và target_duration_seconds nếu cần, nhưng phải có reader phân nhánh theo version và chuyển đổi các bản 1.0. Đợt này không đổi output fields khi chưa có bằng chứng cần thiết.

## Chạy lại và giới hạn

Chạy trong môi trường worker có dependencies, DB và credential local; thêm project root vào PYTHONPATH. Offline: `python scripts/audit_scene_count.py --job 13 --out <audit-folder>`. `--live` thực hiện tối đa 12 request, từ chối ledger đã có và dừng ngay khi 401/403/429 hoặc kết quả mạng không xác định. Chỉ dùng live trong ngân sách và phạm vi nguồn đã được cho phép. Không có header credential trong artifact.

Đợt 01/10 đã dừng sau 4 sends, không tự resume. B là negative control vì nguồn đã full text; C đảo chiều 45→15 để chỉ thay biến thời lượng; F gồm hai sends. Chọn candidate hợp lệ trong script chỉ là sàng lọc, phải đánh giá biên tập trước khi kết luận “tốt nhất” hay dựng video so sánh.
