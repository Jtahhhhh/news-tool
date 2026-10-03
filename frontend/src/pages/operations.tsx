import { useState } from "react";
import { backend } from "../api/client";
import { useAction, useApi } from "../hooks/use-api";
import { Button } from "../components/ui/button";
import {
  Panel,
  Feedback,
  Status,
  Empty,
  Pagination,
} from "../components/shared";
import type { Page, Video, Job } from "../types";
import { JobDetail } from "../components/job-detail";
export function Videos() {
  const [page, setPage] = useState(1);
  const query = useApi<Page<Video>>("/api/videos?page=" + page);
  const action = useAction();
  return (
    <>
      <h1>Video library</h1>
      <p>Preview every cut before approving it for publishing.</p>
      <Feedback
        loading={query.isPending}
        error={query.error}
        retry={() => query.refetch()}
      />
      <div className="cards">
        {query.data?.items.map((v) => (
          <article className="content-card" key={v.id}>
            <video
              controls
              preload="metadata"
              src={backend(
                "/media/video/" +
                  encodeURIComponent(v.output_key.split(/[\\/]/).pop() || ""),
              )}
            />
            <h2>Video #{v.id}</h2>
            <Status value={v.status} />
            <p>
              {v.duration_seconds.toFixed(1)}s · {v.config.width} ×{" "}
              {v.config.height}
            </p>
            <div className="row-actions">
              <Button
                disabled={action.isPending || v.status !== "needs_review"}
                onClick={() =>
                  action.mutate({ path: `/api/videos/${v.id}/approve` })
                }
              >
                Approve
              </Button>
              <a className="text-link" href={backend("/videos/" + v.id)}>
                Details / re-render ↗
              </a>
              <Button
                variant="outline"
                disabled={action.isPending}
                onClick={() =>
                  action.mutate({ path: `/api/videos/${v.id}/render` })
                }
              >
                Re-render
              </Button>
            </div>
          </article>
        ))}
      </div>
      {query.data?.items.length === 0 && <Empty />}
      {query.data && (
        <Pagination page={page} total={query.data.total} setPage={setPage} />
      )}
    </>
  );
}
export function Jobs() {
  const [detail, setDetail] = useState<Job | null>(null);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const query = useApi<Page<Job>>(
    `/api/operations/jobs?page=${page}&status=${status}`,
    4000,
  );
  const action = useAction();
  return (
    <>
      <h1>Jobs & errors</h1>
      {detail && (
        <JobDetail
          kind={detail.job_type}
          id={detail.id}
          close={() => setDetail(null)}
        />
      )}
      <p>
        One place to follow your background work. Refreshes every 4 seconds.
      </p>
      <select
        aria-label="Job status"
        value={status}
        onChange={(e) => {
          setStatus(e.target.value);
          setPage(1);
        }}
      >
        <option value="">All statuses</option>
        {["queued", "running", "failed", "unknown_outcome", "succeeded"].map(
          (s) => (
            <option key={s}>{s}</option>
          ),
        )}
      </select>
      <Feedback
        loading={query.isPending}
        error={query.error}
        retry={() => query.refetch()}
      />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th>Created</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {query.data?.items.map((j) => (
              <tr key={j.job_type + j.id}>
                <td>
                  {j.job_type} #{j.id}
                </td>
                <td>{new Date(j.created_at).toLocaleString("vi-VN")}</td>
                <td>
                  <Status value={j.status} />
                </td>
                <td>
                  <div className="row-actions">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setDetail(j)}
                    >
                      Error details
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={
                        action.isPending ||
                        !["failed", "cancelled"].includes(j.status)
                      }
                      onClick={() =>
                        action.mutate({
                          path: `/api/operations/jobs/${j.job_type}/${j.id}/retry`,
                        })
                      }
                    >
                      Retry
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={
                        action.isPending ||
                        ![
                          "queued",
                          "running",
                          "rendering",
                          "generating_audio",
                        ].includes(j.status) ||
                        j.job_type === "crawl"
                      }
                      onClick={() =>
                        action.mutate({
                          path: `/api/operations/jobs/${j.job_type}/${j.id}/cancel`,
                        })
                      }
                    >
                      Cancel
                    </Button>
                    <a
                      href={backend(
                        j.job_type === "script"
                          ? `/llm/jobs/${j.id}`
                          : j.job_type === "publish"
                            ? "/publishing"
                            : j.job_type === "render"
                              ? "/videos"
                              : "/jobs",
                      )}
                    >
                      Details ↗
                    </a>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {query.data?.items.length === 0 && <Empty />}
      </div>
      {query.data && (
        <Pagination page={page} total={query.data.total} setPage={setPage} />
      )}
    </>
  );
}
export function Publish() {
  const query = useApi<{
    items: { id: number; status: string; video_version_id: number }[];
  }>("/api/publish");
  return (
    <>
      <h1>Publish</h1>
      <p>
        Review the exact video and choose a connected TikTok account before
        sending.
      </p>
      <a className="text-link" href={backend("/publishing")}>
        Open publishing & account controls ↗
      </a>
      <p className="warning">
        An upload to the TikTok inbox still needs completion in TikTok. Only
        confirmed posts count as published.
      </p>
      <Feedback
        loading={query.isPending}
        error={query.error}
        retry={() => query.refetch()}
      />
      {query.data?.items.map((j) => (
        <Panel title={"Publish #" + j.id} key={j.id}>
          <Status value={j.status} />
          <p>Video #{j.video_version_id}</p>
        </Panel>
      ))}
      {query.data?.items.length === 0 && <Empty />}
    </>
  );
}
export function Storage() {
  const action = useAction();
  const query = useApi<
    {
      category: string;
      bytes: number;
      count: number;
      files: { name: string; bytes: number }[];
    }[]
  >("/api/storage");
  return (
    <>
      <h1>Storage</h1>
      <Button
        variant="outline"
        disabled={action.isPending}
        onClick={() => action.mutate({ path: "/api/storage/cleanup" })}
      >
        Clean temporary files older than 24 hours
      </Button>
      <p>Your media inventory. Up to 100 files shown per category.</p>
      <Feedback
        loading={query.isPending}
        error={query.error}
        retry={() => query.refetch()}
      />
      <div className="two-col">
        {query.data?.map((c) => (
          <Panel key={c.category} title={c.category}>
            <strong>{(c.bytes / 1024 / 1024).toFixed(1)} MB</strong>
            <p>{c.count} objects</p>
            {c.files.map((f) => (
              <div className="file-row" key={f.name}>
                <span>{f.name}</span>
                <small>{(f.bytes / 1024).toFixed(0)} KB</small>
              </div>
            ))}
          </Panel>
        ))}
      </div>
    </>
  );
}
export function Settings() {
  const query = useApi<{
    environment: string;
    provider: string;
    models: Record<string, string>;
    storage: string;
  }>("/api/settings");
  return (
    <>
      <h1>Settings</h1>
      <p>Configuration overview and existing management tools.</p>
      <Feedback
        loading={query.isPending}
        error={query.error}
        retry={() => query.refetch()}
      />
      {query.data && (
        <div className="two-col">
          <Panel title="AI generation">
            <p>Default provider: {query.data.provider}</p>
            {Object.entries(query.data.models).map(([k, v]) => (
              <p key={k}>
                {k}: {v}
              </p>
            ))}
            <a className="text-link" href={backend("/llm")}>
              Manage AI routes & credentials ↗
            </a>
          </Panel>
          <Panel title="Workspace">
            <p>Environment: {query.data.environment}</p>
            <p>Storage: {query.data.storage}</p>
            <a className="text-link" href={backend("/sources")}>
              Manage news sources ↗
            </a>
            <p>
              <a className="text-link" href={backend("/publishing")}>
                Manage TikTok accounts ↗
              </a>
            </p>
          </Panel>
        </div>
      )}
    </>
  );
}
