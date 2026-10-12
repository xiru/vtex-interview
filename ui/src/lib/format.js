// Formatting helpers available in every template.

export function formatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

const STATUS_STYLES = {
  completed: 'success',
  running: 'info',
  failed: 'danger',
  undone: 'secondary',
  partially_undone: 'warning',
  reverted: 'secondary',
  partially_reverted: 'warning',
};

export function statusClass(status) {
  return STATUS_STYLES[status] ?? 'light';
}

export function sellerPath(name) {
  return `/sellers/${encodeURIComponent(name)}`;
}
