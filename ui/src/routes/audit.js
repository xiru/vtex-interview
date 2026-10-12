// Audit pages: imports, merges and undos. Read-only: undo/merge commands are only *displayed*
// as text for an operator to run with the CLI.
import { apiPaging, pageFrom, pageLinks } from '../lib/pagination.js';
import { baseLocals } from '../lib/view.js';
import { idParam } from './catalog.js';

const IMPORT_STATUSES = ['completed', 'running', 'failed', 'undone', 'partially_undone'];

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

export function registerAuditRoutes(app, api) {
  app.get('/imports', async (req, res) => {
    const page = pageFrom(req.query);
    const filters = {
      seller: text(req.query.seller),
      md5: text(req.query.md5),
      status: IMPORT_STATUSES.includes(req.query.status) ? req.query.status : '',
      since: text(req.query.since),
      until: text(req.query.until),
    };
    const data = await api.get('/imports', { ...filters, ...apiPaging(page) });
    res.render('pages/imports', {
      ...baseLocals(req, 'Imports'),
      filters,
      statuses: IMPORT_STATUSES,
      imports: data.items,
      pager: pageLinks('/imports', filters, data.total, page),
    });
  });

  app.get('/imports/:id', async (req, res) => {
    const item = await api.get(`/imports/${idParam(req.params.id)}`);
    res.render('pages/import', { ...baseLocals(req, `Import ${item.id}`), item });
  });

  app.get('/merges', async (req, res) => {
    const page = pageFrom(req.query);
    const data = await api.get('/merges', apiPaging(page));
    res.render('pages/merges', {
      ...baseLocals(req, 'Merges'),
      merges: data.items,
      pager: pageLinks('/merges', {}, data.total, page),
    });
  });

  app.get('/undos', async (req, res) => {
    const page = pageFrom(req.query);
    const data = await api.get('/undos', apiPaging(page));
    res.render('pages/undos', {
      ...baseLocals(req, 'Undo history'),
      undos: data.items,
      pager: pageLinks('/undos', {}, data.total, page),
    });
  });
}
