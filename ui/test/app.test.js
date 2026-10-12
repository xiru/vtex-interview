import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import request from 'supertest';

import { ApiError } from '../src/api.js';
import { createApp } from '../src/app.js';
import { appWith } from './helpers.js';

describe('pages', () => {
  const pages = [
    ['/', ['977', 'ProductEntry.json']],
    ['/products', ['iPhone 15 Pro', 'href="/brands/30"', 'href="/sellers/Tech%20World%2FStore"', 'Page 1 of 3']],
    ['/products/1', ['01a130df-fe70-7b65-86cc-d47866a4c0a9', 'iPhone 15 PRO', 'apple']],
    ['/brands', ['Apple', '/brands/30']],
    ['/brands/30', ['Sellers of this brand', 'Tech World/Store', 'iPhone 15 Pro']],
    ['/categories', ['electronics']],
    ['/categories/15', ['Sellers in this category', 'No sellers.']],
    ['/sellers', ['Tech World/Store', '13']],
    ['/sellers/Tech%20World%2FStore', ['iPhone 15 PRO', 'Phones', 'href="/products/1"']],
    ['/imports', ['33c35774dac13280c348cbc2b8365b23', 'completed']],
    [
      '/imports/4',
      ['catalog import undo --md5 33c35774dac13280c348cbc2b8365b23', 'SQL_INJECTION', 'catalog undo revert 11'],
    ],
    ['/merges', ['spelling "Amazon Basics"', 'catalog merge undo 9', 'not undoable']],
    ['/undos', ['catalog undo revert 11', 'import_undo']],
  ];
  for (const [path, expected] of pages) {
    test(`GET ${path}`, async () => {
      const { app } = appWith();
      const res = await request(app).get(path).expect(200);
      for (const text of expected) assert.ok(res.text.includes(text), `${path} should contain ${text}`);
    });
  }
});

describe('query handling', () => {
  test('filters and paging are passed to the API', async () => {
    const { app, api } = appWith();
    await request(app).get('/products?q=phone&brand=Apple&category=x&seller=y&page=2&show_inactive=1').expect(200);
    assert.deepEqual(api.calls[0], {
      path: '/products',
      params: { q: 'phone', brand: 'Apple', category: 'x', seller: 'y', limit: 50, offset: 50, show_inactive: true },
    });
  });

  test('pagination links keep filters', async () => {
    const { app } = appWith();
    const res = await request(app).get('/products?q=phone&page=2').expect(200);
    assert.ok(res.text.includes('href="/products?q=phone&amp;page=1"'));
    assert.ok(res.text.includes('href="/products?q=phone&amp;page=3"'));
  });

  test('invalid page numbers fall back to page 1', async () => {
    const { app, api } = appWith();
    await request(app).get('/brands?page=-3').expect(200);
    assert.equal(api.calls[0].params.offset, 0);
  });

  test('show inactive toggle', async () => {
    const { app } = appWith();
    const off = await request(app).get('/products?q=a').expect(200);
    assert.ok(off.text.includes('href="/products?q=a&amp;show_inactive=1"'));
    const on = await request(app).get('/products?q=a&show_inactive=1').expect(200);
    assert.ok(on.text.includes('Showing inactive'));
    assert.ok(on.text.includes('href="/products?q=a"'));
  });

  test('brand/category detail asks for that brand or category', async () => {
    const { app, api } = appWith();
    await request(app).get('/categories/15').expect(200);
    assert.deepEqual(
      api.calls.map((c) => c.path),
      ['/categories/15', '/products', '/categories/15/sellers'],
    );
    assert.equal(api.calls[1].params.category_id, 15);
  });

  test('import filters only accept known statuses', async () => {
    const { app, api } = appWith();
    await request(app).get('/imports?status=evil&seller=x&md5=abc').expect(200);
    assert.equal(api.calls[0].params.status, '');
    await request(app).get('/imports?status=failed').expect(200);
    assert.equal(api.calls[1].params.status, 'failed');
  });
});

describe('errors', () => {
  test('non numeric ids are 404 without calling the API', async () => {
    const { app, api } = appWith();
    await request(app).get('/products/abc').expect(404);
    await request(app).get('/brands/1e3').expect(404);
    assert.equal(api.calls.length, 0);
  });

  test('API 404 -> 404 page', async () => {
    const { app } = appWith();
    const res = await request(app).get('/products/999').expect(404);
    assert.ok(res.text.includes('show inactive'));
  });

  test('API 422 -> 400 page', async () => {
    const { app } = appWith({ '/products': new ApiError(422, 'bad') });
    await request(app).get('/products').expect(400);
  });

  test('API down -> 502 page', async () => {
    const { app } = appWith({ '/stats': new ApiError(502, 'down') });
    const res = await request(app).get('/').expect(502);
    assert.ok(res.text.includes('unavailable'));
    assert.ok(!res.text.includes('ApiError'));
  });

  test('unexpected errors -> 500 without stack traces', async () => {
    const original = console.error;
    console.error = () => {};
    try {
      const { app } = appWith({ '/stats': new TypeError('boom') });
      const res = await request(app).get('/').expect(500);
      assert.ok(!res.text.includes('boom'));
    } finally {
      console.error = original;
    }
  });

  test('unknown pages -> 404', async () => {
    const { app } = appWith();
    await request(app).get('/nope').expect(404);
  });
});

describe('read-only and security', () => {
  for (const method of ['post', 'put', 'patch', 'delete']) {
    for (const path of ['/', '/products', '/imports/4']) {
      test(`${method.toUpperCase()} ${path} -> 405`, async () => {
        const { app, api } = appWith();
        const res = await request(app)[method](path).expect(405);
        assert.equal(res.headers.allow, 'GET, HEAD');
        assert.equal(api.calls.length, 0);
      });
    }
  }

  test('HEAD is allowed', async () => {
    const { app } = appWith();
    await request(app).head('/').expect(200);
  });

  test('values from the API are HTML-escaped', async () => {
    const evil = '<script>alert(1)</script>';
    const { app } = appWith({
      '/brands': {
        items: [{ id: 1, name: evil, active: true, merged_into_id: null, product_count: 0 }],
        total: 1,
        limit: 50,
        offset: 0,
      },
    });
    const res = await request(app).get('/brands').expect(200);
    assert.ok(!res.text.includes(evil));
    assert.ok(res.text.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  });

  test('query values echoed into forms are escaped', async () => {
    const { app } = appWith();
    const res = await request(app).get('/products?q=%22%3E%3Cscript%3E').expect(200);
    assert.ok(!res.text.includes('"><script>'));
  });

  test('CSP forbids scripts and the powered-by header is hidden', async () => {
    const { app } = appWith();
    const res = await request(app).get('/').expect(200);
    const csp = res.headers['content-security-policy'];
    assert.match(csp, /script-src 'none'/);
    assert.match(csp, /frame-ancestors 'none'/);
    assert.equal(res.headers['x-powered-by'], undefined);
    assert.equal(res.headers['x-content-type-options'], 'nosniff');
  });

  test('static assets are served', async () => {
    const { app } = appWith();
    await request(app).get('/static/bootstrap.min.css').expect(200).expect('content-type', /css/);
    await request(app).get('/static/css/app.css').expect(200);
  });

  test('request logging can be enabled', async () => {
    const app = createApp({ api: { get: async () => ({}) }, logRequests: true });
    assert.equal(typeof app, 'function');
  });
});
