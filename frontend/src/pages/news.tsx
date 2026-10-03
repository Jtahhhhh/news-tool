import { useState } from "react";
import { useAction, useApi } from "../hooks/use-api";
import { Button } from "../components/ui/button";
import { Feedback, Status, Empty, Pagination } from "../components/shared";
import type { Article, Page } from "../types";
export function News() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [date, setDate] = useState("");
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<Article | null>(null);
  const params = new URLSearchParams({ q, page: String(page) });
  if (status) params.set("status", status);
  if (source) params.set("source_id", source);
  if (date) params.set("since", new Date(date).toISOString());
  const news = useApi<Page<Article>>("/api/articles?" + params);
  const sources = useApi<{ id: number; name: string }[]>("/api/sources");
  const action = useAction();
  return (
    <>
      <div className="page-heading">
        <div className="eyebrow">DISCOVER & SELECT</div>
        <h1>News</h1>
        <p>
          Pick the stories worth telling. Selection applies to the whole event
          group.
        </p>
      </div>
      <div className="toolbar">
        <Button
          disabled={!source || action.isPending}
          onClick={() =>
            action.mutate({ path: `/api/sources/${source}/crawl` })
          }
        >
          Crawl selected source
        </Button>
        <input
          aria-label="Search articles"
          placeholder="Search headlines…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <select
          aria-label="Filter status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="selected">Selected</option>
          <option value="skipped">Rejected</option>
        </select>
        <select
          aria-label="Filter source"
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All sources</option>
          {sources.data?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <input
          aria-label="Collected since"
          type="date"
          value={date}
          onChange={(e) => {
            setDate(e.target.value);
            setPage(1);
          }}
        />
      </div>
      <Feedback
        loading={news.isPending}
        error={news.error}
        retry={() => news.refetch()}
      />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Headline</th>
              <th>Source</th>
              <th>Status</th>
              <th>Score</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {news.data?.items.map((a) => (
              <tr key={a.id}>
                <td>
                  <button className="title-button" onClick={() => setDetail(a)}>
                    {a.title}
                  </button>
                  <small>
                    {new Date(a.created_at).toLocaleDateString("vi-VN")}
                  </small>
                </td>
                <td>{a.source}</td>
                <td>
                  <Status value={a.status} />
                </td>
                <td>{a.score.toFixed(1)}</td>
                <td>
                  <div className="row-actions">
                    {(["select", "reject", "generate-script"] as const).map(
                      (key) => (
                        <Button
                          variant="outline"
                          size="sm"
                          key={key}
                          disabled={
                            action.isPending ||
                            (key === "generate-script" &&
                              a.status !== "selected")
                          }
                          onClick={() =>
                            action.mutate({
                              path: `/api/articles/${a.id}/${key}`,
                            })
                          }
                        >
                          {key === "generate-script"
                            ? "Generate script"
                            : key === "select"
                              ? "Select"
                              : "Reject"}
                        </Button>
                      ),
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {news.data?.items.length === 0 && <Empty />}
      </div>
      {news.data && (
        <Pagination page={page} total={news.data.total} setPage={setPage} />
      )}{" "}
      {detail && (
        <div className="overlay" onClick={() => setDetail(null)}>
          <section
            className="drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Article detail"
            onClick={(e) => e.stopPropagation()}
          >
            <Button variant="outline" onClick={() => setDetail(null)}>
              Close
            </Button>
            <h2>{detail.title}</h2>
            <p>{detail.content}</p>
            <a
              className="text-link"
              href={/^https?:\/\//.test(detail.url) ? detail.url : "#"}
              target="_blank"
              rel="noreferrer"
            >
              Read source ↗
            </a>
          </section>
        </div>
      )}
    </>
  );
}
