import type { ReactNode } from "react";

interface StatusDotProps {
  status: "ok" | "degraded" | "down" | "loading" | "error";
  label: string;
  children?: ReactNode;
}

export function StatusDot({ status, label, children }: StatusDotProps) {
  return (
    <div className="status-row">
      <span className={`status-dot status-${status}`} aria-hidden="true" />
      <span className="status-label">{label}</span>
      <span className="status-detail">{children}</span>
    </div>
  );
}
