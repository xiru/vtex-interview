// Static guarantees: raw template output only for includes, fetch only in the API client,
// no request objects passed straight into res.render (EJS hardening).
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');

function files(dir, extension) {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return files(full, extension);
    return full.endsWith(extension) ? [full] : [];
  });
}

test('templates only use unescaped output for include()', () => {
  for (const file of files(path.join(root, 'views'), '.ejs')) {
    const raw = readFileSync(file, 'utf8').match(/<%-[^%]*%>/g) ?? [];
    for (const tag of raw) assert.match(tag, /^<%-\s*include\(/, `${file}: ${tag}`);
  }
});

test('only src/api.js calls fetch', () => {
  for (const file of files(path.join(root, 'src'), '.js')) {
    if (file.endsWith(`${path.sep}api.js`)) continue;
    assert.ok(!/\bfetch\s*\(/.test(readFileSync(file, 'utf8')), `${file} calls fetch`);
  }
});

test('no route passes request objects directly to res.render', () => {
  for (const file of files(path.join(root, 'src'), '.js')) {
    const source = readFileSync(file, 'utf8');
    assert.ok(!/res\.render\([^)]*,\s*req\.(query|params|body)\s*\)/.test(source), file);
  }
});

test('the UI never sends non-GET requests', () => {
  const source = readFileSync(path.join(root, 'src', 'api.js'), 'utf8');
  assert.ok(!/method:\s*'(POST|PUT|PATCH|DELETE)'/i.test(source));
  for (const file of files(path.join(root, 'views'), '.ejs')) {
    assert.ok(!/method="post"/i.test(readFileSync(file, 'utf8')), `${file} has a POST form`);
  }
});
