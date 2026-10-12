// GET-only client for the catalog REST API. This is the only module allowed to call fetch
// (enforced by eslint), so the UI cannot send anything but GET requests to the API.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function createApiClient({ baseUrl, timeoutMs = 5000, fetchImpl = globalThis.fetch }) {
  const base = baseUrl.replace(/\/+$/, '');

  async function get(path, params = {}) {
    const url = new URL(base + path);
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '' && value !== false) {
        url.searchParams.set(key, String(value));
      }
    }
    let response;
    try {
      response = await fetchImpl(url, {
        method: 'GET',
        headers: { accept: 'application/json' },
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      throw new ApiError(502, `catalog API unreachable: ${error.message}`);
    }
    if (!response.ok) {
      throw new ApiError(response.status, `catalog API answered ${response.status} for ${path}`);
    }
    return response.json();
  }

  return { get };
}
