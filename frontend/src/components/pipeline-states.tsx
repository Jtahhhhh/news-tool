import { useApi } from "../hooks/use-api";
import { Panel, Status, Feedback } from "./shared";
export function PipelineStates() {
  const query = useApi<{ status: string; count: number }[]>(
    "/api/dashboard/states",
  );
  return (
    <Panel title="Current workflow states">
      <Feedback
        loading={query.isPending}
        error={query.error}
        retry={() => query.refetch()}
      />
      <div className="row-actions">
        {query.data?.map((s) => (
          <div key={s.status}>
            <Status value={s.status.toLowerCase()} /> <strong>{s.count}</strong>
          </div>
        ))}
      </div>
    </Panel>
  );
}
