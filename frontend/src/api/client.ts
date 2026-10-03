const base = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
let csrf = "";
export const backend = (path: string) => base + path;
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(backend(path), {
    ...options,
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      ...options.headers,
    },
  });
  if (!response.ok) {
    if (response.status === 401)
      window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(
      response.status,
      response.status === 401
        ? "Phiên đăng nhập đã hết hạn."
        : response.status === 409
          ? "Thao tác chưa được phép. Tải lại và kiểm tra trạng thái."
          : `Yêu cầu thất bại (${response.status}). Vui lòng thử lại.`,
    );
  }
  const data = await response.json();
  if (data.csrf_token) csrf = data.csrf_token;
  return data;
}
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
