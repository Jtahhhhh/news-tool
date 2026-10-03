import { useApi } from "../hooks/use-api";
import { Panel, Feedback, Status } from "./shared";
import { Button } from "./ui/button";
export function JobDetail({
  kind,
  id,
  close,
}: {
  kind: string;
  id: number;
  close: () => void;
}) {
  const query = useApi<{
    status: string;
    retry_count: number;
    progress: string | number | null;
    error_message: string;
    started_at: string | null;
    finished_at: string | null;
  }>(`/api/operations/jobs/${kind}/${id}`, 4000);
  return (
    <Panel title={`${kind} #${id}`}>
      <Button variant="outline" onClick={close}>
        Close details
      </Button>
      <Feedback
        loading={query.isPending}
        error={query.error}
        retry={() => query.refetch()}
      />
      {query.data && (
        <>
          <p>
            <Status value={query.data.status} />
          </p>
          <p>
            Retries: {query.data.retry_count} · Progress:{" "}
            {query.data.progress ?? "—"}
          </p>
          <p>
            Started: {query.data.started_at ?? "—"} · Finished:{" "}
            {query.data.finished_at ?? "—"}
          </p>
          <pre className="error-detail">
            {query.data.error_message || "No error recorded."}
          </pre>
        </>
      )}
    </Panel>
  );
}
