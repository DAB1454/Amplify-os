const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Token management ────────────────────────────────────────────

let accessToken: string | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
  if (token) {
    if (typeof window !== "undefined") localStorage.setItem("amplify_token", token);
  } else {
    if (typeof window !== "undefined") localStorage.removeItem("amplify_token");
  }
}

export function getAccessToken(): string | null {
  if (accessToken) return accessToken;
  if (typeof window !== "undefined") {
    accessToken = localStorage.getItem("amplify_token");
  }
  return accessToken;
}

export function clearAuth() {
  accessToken = null;
  if (typeof window !== "undefined") {
    localStorage.removeItem("amplify_token");
    localStorage.removeItem("amplify_refresh_token");
  }
}

// ── API error ───────────────────────────────────────────────────

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(`API error ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

// ── Core fetch ──────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs: number = 90000,
): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> || {}),
  };

  const token = getAccessToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(408, "Request timed out — the server may still be processing. Try refreshing.");
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }

  // Handle 401 — try refresh once
  if (res.status === 401 && token) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      headers["Authorization"] = `Bearer ${getAccessToken()}`;
      const retry = await fetch(`${API_BASE}${path}`, { ...options, headers });
      if (!retry.ok) {
        const detail = await parseErrorDetail(retry);
        throw new ApiError(retry.status, detail);
      }
      if (retry.status === 204) return undefined as T;
      return retry.json();
    }
    clearAuth();
    throw new ApiError(401, "Session expired");
  }

  if (!res.ok) {
    const detail = await parseErrorDetail(res);
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body.detail || body.message || JSON.stringify(body);
  } catch {
    return `HTTP ${res.status}`;
  }
}

async function tryRefreshToken(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  const refreshToken = localStorage.getItem("amplify_refresh_token");
  if (!refreshToken) return false;

  try {
    const res = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    setAccessToken(data.access_token);
    if (typeof window !== "undefined" && data.refresh_token) {
      localStorage.setItem("amplify_refresh_token", data.refresh_token);
    }
    return true;
  } catch {
    return false;
  }
}

// ── Public methods ──────────────────────────────────────────────

export async function apiGet<T>(path: string): Promise<T> {
  return apiFetch<T>(path);
}

export async function apiPost<T>(path: string, body: unknown, timeoutMs?: number): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, timeoutMs);
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
}

export async function apiDelete<T = void>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: "DELETE" });
}

export async function apiUpload<T>(path: string, file: File): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);
  // Don't set Content-Type — browser sets multipart boundary automatically
  return apiFetch<T>(path, {
    method: "POST",
    body: formData,
  });
}

/**
 * Upload a file with progress reporting via XMLHttpRequest.
 * onProgress receives a value from 0 to 100.
 */
export function apiUploadWithProgress<T>(
  path: string,
  file: File,
  onProgress: (pct: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const token = getAccessToken();

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          reject(new Error("Invalid JSON response"));
        }
      } else {
        let detail = `HTTP ${xhr.status}`;
        try {
          const body = JSON.parse(xhr.responseText);
          detail = body.detail || body.message || detail;
        } catch { /* use default */ }
        reject(new ApiError(xhr.status, detail));
      }
    });

    xhr.addEventListener("error", () => reject(new Error("Upload failed — network error")));
    xhr.addEventListener("abort", () => reject(new Error("Upload aborted")));

    const formData = new FormData();
    formData.append("file", file);

    xhr.open("POST", `${API_BASE}${path}`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.send(formData);
  });
}

/**
 * Upload a CSV file (for track import).
 */
export async function apiUploadCSV<T>(path: string, file: File): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<T>(path, {
    method: "POST",
    body: formData,
  });
}

/**
 * POST a FormData body (file + extra fields). Auth handled automatically.
 */
export async function apiFormPost<T>(path: string, formData: FormData): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    body: formData,
  });
}

// ── Auth helpers ────────────────────────────────────────────────

export async function login(email: string, password: string) {
  const data = await apiFetch<{
    access_token: string;
    refresh_token: string;
    token_type: string;
    expires_in: number;
  }>("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  setAccessToken(data.access_token);
  if (typeof window !== "undefined") {
    localStorage.setItem("amplify_refresh_token", data.refresh_token);
  }
  return data;
}

export async function register(
  email: string,
  password: string,
  tenantName: string,
  displayName?: string,
) {
  const data = await apiFetch<{
    access_token: string;
    refresh_token: string;
    token_type: string;
    expires_in: number;
  }>("/api/v1/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      password,
      tenant_name: tenantName,
      display_name: displayName,
    }),
  });
  setAccessToken(data.access_token);
  if (typeof window !== "undefined") {
    localStorage.setItem("amplify_refresh_token", data.refresh_token);
  }
  return data;
}

export async function fetchCurrentUser() {
  return apiGet<{
    id: string;
    email: string;
    display_name: string | null;
    is_active: boolean;
    created_at: string;
  }>("/api/v1/auth/me");
}
