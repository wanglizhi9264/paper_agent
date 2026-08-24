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

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return parseResponse<T>(resp);
}

export async function apiPostForm<T>(path: string, formData: FormData): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { accept: "application/json" },
    body: formData,
  });
  return parseResponse<T>(resp);
}

export async function apiDelete<T>(path: string): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const resp = await fetch(url, {
    method: "DELETE",
    headers: { accept: "application/json" },
  });
  return parseResponse<T>(resp);
}

export async function apiPut<T>(path: string): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const resp = await fetch(url, {
    method: "PUT",
    headers: { accept: "application/json" },
  });
  return parseResponse<T>(resp);
}

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export async function* sseStream(
  path: string,
  body: unknown,
): AsyncGenerator<SSEEvent> {
  const url = `${BASE_URL}${path}`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
    },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    const text = await resp.text();
    let error: ApiError;
    try {
      const body = text ? JSON.parse(text) : null;
      const envelope = body as ApiErrorEnvelope | null;
      error = envelope?.error ?? { code: "UNKNOWN", message: resp.statusText, details: {}, request_id: null };
    } catch {
      error = { code: "UNKNOWN", message: resp.statusText, details: {}, request_id: null };
    }
    throw new ApiException(resp.status, error);
  }

  const reader = resp.body?.getReader();
  if (!reader) {
    throw new ApiException(500, {
      code: "SSE_NO_BODY",
      message: "No response body for SSE stream",
      details: {},
      request_id: null,
    });
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const dataStr = line.slice(6);
        try {
          const data = JSON.parse(dataStr) as Record<string, unknown>;
          yield { event: currentEvent, data };
        } catch {
          // Skip non-JSON data (e.g., heartbeat comments)
        }
        currentEvent = "message";
      }
    }
  }
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
