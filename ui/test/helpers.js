import { ApiError } from '../src/api.js';
import { createApp } from '../src/app.js';
import { responses } from './fixtures.js';

export function stubApi(overrides = {}) {
  const calls = [];
  const table = { ...responses, ...overrides };
  return {
    calls,
    async get(path, params = {}) {
      calls.push({ path, params });
      const value = table[path];
      if (value instanceof Error) throw value;
      if (value === undefined) throw new ApiError(404, `no fixture for ${path}`);
      return structuredClone(value);
    },
  };
}

export function appWith(overrides) {
  const api = stubApi(overrides);
  return { api, app: createApp({ api, logRequests: false }) };
}
