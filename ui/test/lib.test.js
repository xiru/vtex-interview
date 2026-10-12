import assert from 'node:assert/strict';
import { test } from 'node:test';

import { formatBytes, formatDate, sellerPath, statusClass } from '../src/lib/format.js';
import { apiPaging, buildQuery, pageFrom, pageLinks } from '../src/lib/pagination.js';

test('pagination math', () => {
  assert.equal(pageFrom({}), 1);
  assert.equal(pageFrom({ page: '3' }), 3);
  assert.equal(pageFrom({ page: '0' }), 1);
  assert.equal(pageFrom({ page: 'x' }), 1);
  assert.deepEqual(apiPaging(3), { limit: 50, offset: 100 });
  assert.equal(buildQuery({}), '');
  assert.equal(buildQuery({ a: 'x y', b: '', c: undefined, d: 2 }), '?a=x+y&d=2');
  const links = pageLinks('/p', { q: 'a' }, 120, 2);
  assert.deepEqual(links, { page: 2, pages: 3, total: 120, prev: '/p?q=a&page=1', next: '/p?q=a&page=3' });
  assert.deepEqual(pageLinks('/p', {}, 0, 1), { page: 1, pages: 1, total: 0, prev: null, next: null });
});

test('formatting', () => {
  assert.equal(formatDate('2026-10-12T16:10:11.316Z'), '2026-10-12 16:10:11 UTC');
  assert.equal(formatDate(null), '');
  assert.equal(formatDate('not a date'), 'not a date');
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(48938), '47.8 KiB');
  assert.equal(formatBytes(3 * 1024 * 1024), '3.0 MiB');
  assert.equal(statusClass('failed'), 'danger');
  assert.equal(statusClass('???'), 'light');
  assert.equal(sellerPath('A/B C'), '/sellers/A%2FB%20C');
});
