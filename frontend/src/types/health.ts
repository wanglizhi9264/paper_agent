export interface ApiError {
  code: string;
  message: string;
  details: Record<string, unknown>;
  request_id: string | null;
}

export interface ApiErrorEnvelope {
  error: ApiError;
}

export interface ComponentHealth {
  status: "ok" | "degraded" | "down";
  detail?: string;
}

export interface ReadyResponse {
  status: "ok" | "degraded" | "down";
  components: Record<string, ComponentHealth>;
}

export interface LiveResponse {
  status: "ok";
}
