import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiError, createApiClient } from '../src/api.js';

function fakeFetch(response) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url: String(url), init });
    if (response instanceof Error) throw response;
    return response;
  };
  return { calls, impl };
}

test('GET with query parameters, skipping empty values', async () => {
  const { calls, impl } = fakeFetch({ ok: true, status: 200, json: async () => ({ ok: 1 }) });
  const api = createApiClient({ baseUrl: 'http://api:8000/api/v1/', fetchImpl: impl });
  const data = await api.get('/products', { q: 'a b', brand: '', limit: 50, show_inactive: false, x: null });
  assert.deepEqual(data, { ok: 1 });
  assert.equal(calls[0].url, 'http://api:8000/api/v1/products?q=a+b&limit=50');
  assert.equal(calls[0].init.method, 'GET');
  assert.ok(calls[0].init.signal);
});

test('HTTP errors become ApiError with the status', async () => {
  const { impl } = fakeFetch({ ok: false, status: 404, json: async () => ({}) });
  const api = createApiClient({ baseUrl: 'http://api', fetchImpl: impl });
  await assert.rejects(api.get('/x'), (error) => error instanceof ApiError && error.status === 404);
});

test('network failures and timeouts become 502', async () => {
  const { impl } = fakeFetch(new TypeError('connection refused'));
  const api = createApiClient({ baseUrl: 'http://api', fetchImpl: impl });
  await assert.rejects(api.get('/x'), (error) => error.status === 502 && /unreachable/.test(error.message));
});
