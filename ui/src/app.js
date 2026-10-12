// Express application factory: read-only, server-side rendered UI for the catalog API.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

import ejs from 'ejs';
import express from 'express';
import helmet from 'helmet';
import morgan from 'morgan';

import { formatBytes, formatDate, sellerPath, statusClass } from './lib/format.js';
import { registerAuditRoutes } from './routes/audit.js';
import { registerCatalogRoutes } from './routes/catalog.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const bootstrapCss = require.resolve('bootstrap/dist/css/bootstrap.min.css');

export function createApp({ api, logRequests = true }) {
  const app = express();
  app.disable('x-powered-by');
  app.set('views', path.join(here, '..', 'views'));
  app.engine('ejs', ejs.__express);
  app.set('view engine', 'ejs');
  app.locals.formatDate = formatDate;
  app.locals.formatBytes = formatBytes;
  app.locals.statusClass = statusClass;
  app.locals.sellerPath = sellerPath;

  app.use(
    helmet({
      contentSecurityPolicy: {
        useDefaults: false,
        directives: {
          defaultSrc: ["'self'"],
          scriptSrc: ["'none'"],
          styleSrc: ["'self'"],
          imgSrc: ["'self'", 'data:'],
          formAction: ["'self'"],
          frameAncestors: ["'none'"],
          baseUri: ["'none'"],
          objectSrc: ["'none'"],
        },
      },
    }),
  );
  if (logRequests) app.use(morgan('combined'));

  // Read-only: anything but GET/HEAD is rejected before reaching a route.
  app.use((req, res, next) => {
    if (req.method === 'GET' || req.method === 'HEAD') return next();
    res.set('Allow', 'GET, HEAD');
    return res.status(405).type('text/plain').send('Method Not Allowed: this UI is read-only');
  });

  app.get('/static/bootstrap.min.css', (req, res) => res.sendFile(bootstrapCss));
  app.use('/static', express.static(path.join(here, '..', 'public'), { index: false }));

  registerCatalogRoutes(app, api);
  registerAuditRoutes(app, api);

  app.use((req, res) => {
    res.status(404).render('pages/error', {
      title: 'Not found',
      path: req.path,
      showInactive: false,
      toggleInactiveHref: null,
      status: 404,
      message: 'This page does not exist.',
    });
  });

  // eslint-disable-next-line no-unused-vars
  app.use((error, req, res, next) => {
    const upstream = error.status;
    let status = 500;
    let message = 'Unexpected error.';
    if (upstream === 404) {
      status = 404;
      message = 'Not found (it may be inactive: try "show inactive").';
    } else if (upstream === 422 || upstream === 400) {
      status = 400;
      message = 'Invalid request parameters.';
    } else if (upstream === 502 || (upstream && upstream >= 500)) {
      status = 502;
      message = 'The catalog API is unavailable. Try again shortly.';
    }
    if (status === 500) console.error(error);
    res.status(status).render('pages/error', {
      title: 'Error',
      path: req.path,
      showInactive: false,
      toggleInactiveHref: null,
      status,
      message,
    });
  });
  return app;
}
