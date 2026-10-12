// Page number <-> API limit/offset, and links that keep the current filters.

export const PAGE_SIZE = 50;

export function pageFrom(query) {
  const page = Number.parseInt(query.page ?? '1', 10);
  return Number.isFinite(page) && page > 0 ? page : 1;
}

export function apiPaging(page, size = PAGE_SIZE) {
  return { limit: size, offset: (page - 1) * size };
}

export function buildQuery(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

export function pageLinks(path, query, total, page, size = PAGE_SIZE) {
  const pages = Math.max(1, Math.ceil(total / size));
  const link = (target) => path + buildQuery({ ...query, page: target });
  return {
    page,
    pages,
    total,
    prev: page > 1 ? link(page - 1) : null,
    next: page < pages ? link(page + 1) : null,
  };
}
