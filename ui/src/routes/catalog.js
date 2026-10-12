// Catalog pages: home, products, brands, categories, sellers.
import { apiPaging, pageFrom, pageLinks } from '../lib/pagination.js';
import { baseLocals, showInactive } from '../lib/view.js';

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function idParam(value) {
  const id = Number.parseInt(value, 10);
  if (!Number.isSafeInteger(id) || id <= 0 || String(id) !== value) {
    const error = new Error('not found');
    error.status = 404;
    throw error;
  }
  return id;
}

export function registerCatalogRoutes(app, api) {
  app.get('/', async (req, res) => {
    const stats = await api.get('/stats');
    res.render('pages/home', { ...baseLocals(req, 'Catalog'), stats });
  });

  app.get('/products', async (req, res) => {
    const page = pageFrom(req.query);
    const filters = {
      q: text(req.query.q),
      brand: text(req.query.brand),
      category: text(req.query.category),
      seller: text(req.query.seller),
    };
    const data = await api.get('/products', {
      ...filters,
      ...apiPaging(page),
      show_inactive: showInactive(req.query),
    });
    res.render('pages/products', {
      ...baseLocals(req, 'Products'),
      filters,
      products: data.items,
      pager: pageLinks('/products', { ...filters, show_inactive: req.query.show_inactive }, data.total, page),
    });
  });

  app.get('/products/:id', async (req, res) => {
    const product = await api.get(`/products/${idParam(req.params.id)}`, {
      show_inactive: showInactive(req.query),
    });
    res.render('pages/product', { ...baseLocals(req, product.name), product });
  });

  for (const kind of ['brands', 'categories']) {
    const label = kind === 'brands' ? 'Brands' : 'Categories';
    const filterKey = kind === 'brands' ? 'brand_id' : 'category_id';

    app.get(`/${kind}`, async (req, res) => {
      const page = pageFrom(req.query);
      const q = text(req.query.q);
      const data = await api.get(`/${kind}`, {
        q,
        ...apiPaging(page),
        show_inactive: showInactive(req.query),
      });
      res.render('pages/named-list', {
        ...baseLocals(req, label),
        kind,
        q,
        items: data.items,
        pager: pageLinks(`/${kind}`, { q, show_inactive: req.query.show_inactive }, data.total, page),
      });
    });

    app.get(`/${kind}/:id`, async (req, res) => {
      const id = idParam(req.params.id);
      const page = pageFrom(req.query);
      const inactive = showInactive(req.query);
      const [entity, products, sellers] = await Promise.all([
        api.get(`/${kind}/${id}`, { show_inactive: inactive }),
        api.get('/products', { [filterKey]: id, ...apiPaging(page), show_inactive: inactive }),
        api.get(`/${kind}/${id}/sellers`, { limit: 1000, show_inactive: inactive }),
      ]);
      res.render('pages/named-detail', {
        ...baseLocals(req, entity.name),
        kind,
        entity,
        products: products.items,
        sellers: sellers.items,
        pager: pageLinks(`/${kind}/${id}`, { show_inactive: req.query.show_inactive }, products.total, page),
      });
    });
  }

  app.get('/sellers', async (req, res) => {
    const page = pageFrom(req.query);
    const q = text(req.query.q);
    const data = await api.get('/sellers', {
      q,
      ...apiPaging(page),
      show_inactive: showInactive(req.query),
    });
    res.render('pages/sellers', {
      ...baseLocals(req, 'Sellers'),
      q,
      sellers: data.items,
      pager: pageLinks('/sellers', { q, show_inactive: req.query.show_inactive }, data.total, page),
    });
  });

  app.get('/sellers/:name', async (req, res) => {
    const name = req.params.name;
    const page = pageFrom(req.query);
    const inactive = showInactive(req.query);
    const encoded = encodeURIComponent(name);
    const [seller, offers] = await Promise.all([
      api.get(`/sellers/${encoded}`, { show_inactive: inactive }),
      api.get(`/sellers/${encoded}/products`, { ...apiPaging(page), show_inactive: inactive }),
    ]);
    res.render('pages/seller', {
      ...baseLocals(req, seller.name),
      seller,
      offers: offers.items,
      pager: pageLinks(`/sellers/${encoded}`, { show_inactive: req.query.show_inactive }, offers.total, page),
    });
  });
}

export { idParam };
