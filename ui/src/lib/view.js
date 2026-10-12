// Shared request context passed (explicitly) to every template.

export function showInactive(query) {
  return query.show_inactive === '1';
}

export function baseLocals(req, title) {
  const inactive = showInactive(req.query);
  const toggle = new URLSearchParams();
  for (const [key, value] of Object.entries(req.query)) {
    if (key !== 'show_inactive' && key !== 'page' && typeof value === 'string') {
      toggle.set(key, value);
    }
  }
  if (!inactive) toggle.set('show_inactive', '1');
  const toggleQuery = toggle.toString();
  return {
    title,
    path: req.path,
    showInactive: inactive,
    toggleInactiveHref: req.path + (toggleQuery ? `?${toggleQuery}` : ''),
  };
}
