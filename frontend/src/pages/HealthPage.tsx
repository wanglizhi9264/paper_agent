import { useReadyHealth } from "../features/health";
import { StatusDot } from "../components/StatusDot";

export function HealthPage() {
  const { data, isLoading, isError, error, refetch } = useReadyHealth();

  if (isLoading) {
    return (
      <section className="page page-health">
        <h1>Health</h1>
        <p className="muted">Checking service status…</p>
      </section>
    );
  }

  if (isError) {
    return (
      <section className="page page-health">
        <h1>Health</h1>
        <div className="error-box">
          <p>Could not reach the backend health endpoint.</p>
          <p className="muted">{error instanceof Error ? error.message : "Unknown error"}</p>
        </div>
        <button type="button" onClick={() => void refetch()} className="btn">
          Retry
        </button>
      </section>
    );
  }

  const overall = data?.status ?? "down";
  const components = data?.components ?? {};

  return (
    <section className="page page-health">
      <h1>Health</h1>
      <StatusDot status={overall} label="Overall">
        {overall}
      </StatusDot>
      <div className="status-list">
        {Object.entries(components).map(([name, info]) => (
          <StatusDot key={name} status={info.status} label={name}>
            {info.detail ?? info.status}
          </StatusDot>
        ))}
        {Object.keys(components).length === 0 ? (
          <p className="muted">No component details returned.</p>
        ) : null}
      </div>
    </section>
  );
}
