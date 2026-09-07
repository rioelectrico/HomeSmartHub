import type { ApiErrorEnvelope } from "@/types/api";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message?: string,
  ) {
    super(message ?? code);
    this.name = "ApiError";
  }
}

export function resolveApiUrl(path: string) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "";
  return `${baseUrl}${path}`;
}

function redirectToLogin() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("portero:unauthorized"));
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(resolveApiUrl(path), {
    ...init,
    headers,
    credentials: "include",
  });

  if (!response.ok) {
    let envelope: ApiErrorEnvelope | undefined;
    try {
      envelope = (await response.json()) as ApiErrorEnvelope;
    } catch {
      // The backend may return an empty error response from a proxy.
    }
    if (response.status === 401) redirectToLogin();
    throw new ApiError(
      response.status,
      envelope?.error.code ?? "REQUEST_FAILED",
      envelope?.error.message,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
