import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, backend } from "../api/client";
import { useAction, useApi } from "../hooks/use-api";
import { Button } from "../components/ui/button";
import {
  Panel,
  Feedback,
  Status,
  Empty,
  Pagination,
} from "../components/shared";
import type { Script, Page } from "../types";
export function Scripts() {
  const [page, setPage] = useState(1);
  const query = useApi<Page<Script>>("/api/scripts?page=" + page);
  return (
    <>
      <h1>Script review</h1>
      <p>Shape your story before it becomes a video.</p>
      <Feedback
        loading={query.isPending}
        error={query.error}
        retry={() => query.refetch()}
      />
      <div className="cards">
        {query.data?.items.map((s) => (
          <Link className="content-card" key={s.id} to={"/scripts/" + s.id}>
            <Status value={s.status || s.outcome} />
            <h2>{s.data?.title || "Script #" + s.id}</h2>
            <p>{s.data?.hook}</p>
            <small>
              Version {s.version} · {s.data?.scenes?.length || 0} scenes
            </small>
          </Link>
        ))}
      </div>
      {query.data?.items.length === 0 && <Empty />}
      {query.data && (
        <Pagination page={page} total={query.data.total} setPage={setPage} />
      )}
    </>
  );
}
export function ScriptDetail() {
  const { id } = useParams();
  const query = useApi<Script>("/api/scripts/" + id);
  const action = useAction();
  const [draft, setDraft] = useState<Script["data"]>(null);
  const [saving, setSaving] = useState(false);
  const client = useQueryClient();
  const navigate = useNavigate();
  useEffect(() => {
    setDraft(query.data?.data || null);
  }, [id, query.data?.id]);
  const seconds = draft?.scenes.reduce((n, s) => n + s.seconds, 0) || 0;
  async function save() {
    setSaving(true);
    try {
      const v = await api<Script>("/api/scripts/" + id, {
        method: "PATCH",
        body: JSON.stringify({ data: draft }),
      });
      await client.invalidateQueries();
      navigate("/scripts/" + v.id);
      toast.success("Saved as a new version");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <Link className="text-link" to="/scripts">
        ← All scripts
      </Link>
      <h1>{draft?.title || "Script review"}</h1>
      <Feedback
        loading={query.isPending}
        error={query.error}
        retry={() => query.refetch()}
      />
      {draft && (
        <>
          <div className="review-stats">
            <Status value={query.data?.status || "draft"} />
            <span>{draft.scenes.length} scenes</span>
            <span>{seconds}s estimated</span>
            <span>Target: {query.data?.target_seconds ?? "—"}s</span>
          </div>
          {query.data?.target_seconds &&
          (seconds < query.data.target_seconds * 0.8 ||
            seconds > query.data.target_seconds + 3) ? (
            <p className="warning">Estimated duration differs from target.</p>
          ) : null}
          <Panel title="Hook">
            <p>{draft.hook}</p>
          </Panel>
          {draft.scenes.map((scene, i) => (
            <Panel
              key={scene.scene_id}
              title={`Scene ${scene.scene_id} · ${scene.seconds}s`}
            >
              {!scene.visual_brief && (
                <p className="warning">This scene needs a visual.</p>
              )}
              {(["narration", "on_screen_text", "visual_brief"] as const).map(
                (key) => (
                  <label key={key}>
                    {key.replaceAll("_", " ")}
                    <textarea
                      value={scene[key]}
                      onChange={(e) => {
                        const scenes = draft.scenes.map((s, n) =>
                          n === i ? { ...s, [key]: e.target.value } : s,
                        );
                        setDraft({
                          ...draft,
                          scenes,
                          ...(key === "narration" && i === 0
                            ? { hook: e.target.value }
                            : {}),
                        });
                      }}
                    />
                  </label>
                ),
              )}
            </Panel>
          ))}
          <div className="sticky-actions">
            <Button disabled={saving} onClick={save}>
              Save new version
            </Button>
            {["approve", "reject", "regenerate"].map((a) => (
              <Button
                key={a}
                variant="outline"
                disabled={
                  action.isPending ||
                  saving ||
                  JSON.stringify(draft) !== JSON.stringify(query.data?.data)
                }
                onClick={() =>
                  action.mutate({ path: `/api/scripts/${id}/${a}` })
                }
              >
                {a}
              </Button>
            ))}
            {query.data?.status === "approved" && (
              <a className="text-link" href={backend("/videos/editor/" + id)}>
                Open video editor ↗
              </a>
            )}
          </div>
        </>
      )}
    </>
  );
}
