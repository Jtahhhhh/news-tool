import type { ReactNode } from "react";
import { Button } from "./ui/button";
export function Status({ value }: { value: string }) {
  return (
    <span
      className={
        "status " +
        (/failed|rejected|skipped|unknown/.test(value)
          ? "bad"
          : /approved|published|succeeded|selected|ok/.test(value)
            ? "good"
            : "")
      }
    >
      {value.replaceAll("_", " ")}
    </span>
  );
}
export function Panel({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}
export function Feedback({
  loading,
  error,
  retry,
}: {
  loading: boolean;
  error: Error | null;
  retry: () => void;
}) {
  return loading ? (
    <div className="skeleton" aria-label="Đang tải" />
  ) : error ? (
    <div role="alert" className="empty">
      {error.message}{" "}
      <Button variant="outline" onClick={retry}>
        Thử lại
      </Button>
    </div>
  ) : null;
}
export function Empty() {
  return (
    <div className="empty">
      Chưa có dữ liệu. Nội dung mới sẽ xuất hiện tại đây.
    </div>
  );
}
export function Pagination({
  page,
  total,
  setPage,
}: {
  page: number;
  total: number;
  setPage: (n: number) => void;
}) {
  return (
    <div className="pagination">
      <span>
        {total} kết quả · Trang {page}
      </span>
      <Button
        variant="outline"
        disabled={page === 1}
        onClick={() => setPage(page - 1)}
      >
        Trước
      </Button>
      <Button
        variant="outline"
        disabled={page * 25 >= total}
        onClick={() => setPage(page + 1)}
      >
        Sau
      </Button>
    </div>
  );
}
