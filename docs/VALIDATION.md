# Kết quả nghiệm thu — 22/09/2026

Đã kiểm tra trên Docker Desktop Linux containers, PostgreSQL 17 và image Python dùng chung.

| Hạng mục | Kết quả |
| --- | --- |
| `docker compose up --build -d` | Khởi động đủ `web`, `worker`, `db`; cả ba healthy |
| Migration | Chạy trước web; `alembic check`: không có chênh lệch schema |
| Image dùng chung | `docker inspect` xác nhận web và worker có cùng image ID |
| Cổng ra máy chủ | Chỉ web, `127.0.0.1:8000` |
| Bộ kiểm thử PostgreSQL | **15 passed**; database `news_tool_test` riêng biệt |
| RSS thật | Thêm VnExpress qua UI, worker tự lấy 48 bài, 0 lỗi |
| Chống trùng thực tế | Yêu cầu lấy lại qua UI: 0 mới, 48 trùng, 0 lỗi |
| Toàn luồng UI | Thêm nguồn → lấy tin → gom/xếp hạng → chọn → xuất JSON → hoàn tác |
| Tạo lại container | `docker compose up -d --force-recreate --wait`; 48 bài và 1 quyết định đã chọn được giữ nguyên |
| Phục hồi worker | Test tiến trình thật qua SIGKILL, SIGTERM và timeout: job hoàn tất sau retry, đúng 1 bản tin |
| Chấm lại | Test giữ cả `selected` và `skipped`; bài từ nguồn khác nhập sự kiện không ghi đè quyết định |
| Đồng thời/lỗi nguồn | Test claim đồng thời, một job hoạt động/nguồn, nguồn lỗi không cản nguồn tốt, retry hữu hạn |
| Kiểm tra UI | Đã xem bố cục trên trình duyệt, điểm thành phần/lý do và trạng thái polling |

File `selected-news.json` là bản xuất mẫu từ ca thử nghiệm. Đã hoàn tác lựa chọn này trong UI sau khi xác minh dữ liệu bền vững. Nguồn VnExpress vẫn bật lịch 30 phút; các bài ở trạng thái chờ duyệt.

Các cảnh báo của bộ test gồm deprecation từ Alembic/Starlette/AnyIO và thư mục cache pytest không có quyền ghi trong image chạy bằng user thường; không có test thất bại. Các ca worker dùng HTTP fixture có kiểm soát, không phụ thuộc việc RSS công khai có thay đổi giữa các lần chạy.

Giới hạn hiện tại: gom nhóm theo độ giống tiêu đề, cần biên tập viên sửa trường hợp khó; adapter HTML generic được test bằng fixture, adapter VnExpress chưa được nghiệm thu trên HTML live. Ứng dụng dành cho local, chưa có đăng nhập/phân quyền.
