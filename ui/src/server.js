// Entry point: node src/server.js (configuration from environment variables).
import { createApiClient } from './api.js';
import { createApp } from './app.js';

const port = Number.parseInt(process.env.PORT ?? '8080', 10);
const api = createApiClient({
  baseUrl: process.env.API_BASE_URL ?? 'http://localhost:8000/api/v1',
  timeoutMs: Number.parseInt(process.env.API_TIMEOUT_MS ?? '5000', 10),
});

const server = createApp({ api }).listen(port, () => {
  console.log(`catalog UI listening on :${port}`);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    server.close();
    server.closeAllConnections();
  });
}
