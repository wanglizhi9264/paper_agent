import type { ApiError, ApiErrorEnvelope } from "../types/health";

const BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://127.0.0.1:8000";

export class ApiException extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly details: Record<string, unknown>;

  constructor(status: number, error: ApiError) {
    super(error.message);
    this.name = "ApiException";
    this.code = error.code;
    this.status = status;
    this.requestId = error.request_id ?? null;
    this.details = error.details ?? {};
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const url = path.startsWith("http") ? path : `${BASE_URL}${path}`;
  const resp = await fetch(url, {
    method: "GET",
    headers: { accept: "application/json" },
  });
  return parseResponse<T>(resp);
}

async function parseResponse<T>(resp: Response): Promise<T> {
  const text = await resp.text();
  const body = text ? (JSON.parse(text) as unknown) : null;
  if (!resp.ok) {
    const envelope = body as ApiErrorEnvelope | null;
    const error: ApiError = envelope?.error ?? {
      code: "UNKNOWN",
      message: resp.statusText,
      details: {},
      request_id: null,
    };
    throw new ApiException(resp.status, error);
  }
  return body as T;
}
