/** Error thrown for non-2xx API responses. Carries the status and parsed body. */
export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly body: unknown;
  readonly url: string;

  constructor(status: number, statusText: string, body: unknown, url: string) {
    super(`API request failed (${status} ${statusText}) for ${url}`);
    this.name = "ApiError";
    this.status = status;
    this.statusText = statusText;
    this.body = body;
    this.url = url;
  }
}
