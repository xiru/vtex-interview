// Canned API responses for the UI tests.
export const product = {
  id: 1,
  uuid: '01a130df-fe70-7b65-86cc-d47866a4c0a9',
  name: 'iPhone 15 Pro',
  active: true,
  merged_into_id: null,
  brand: { id: 30, name: 'Apple' },
  categories: [{ id: 15, name: 'electronics' }],
  sellers: [
    { id: 1, name: 'VTEX' },
    { id: 2, name: 'Tech World/Store' },
  ],
  created: '2026-10-12T16:09:08Z',
};

export const productDetail = {
  ...product,
  offers: [
    {
      id: 10,
      seller: { id: 2, name: 'Tech World/Store' },
      seller_product_id: 'a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d',
      name: 'iPhone 15 PRO',
      brand_name: 'apple',
      category_name: null,
      active: true,
      created: '2026-10-12T16:10:00Z',
      updated: '2026-10-12T16:10:00Z',
    },
  ],
};

export const importItem = {
  id: 4,
  status: 'completed',
  file_name: 'ProductEntry.json',
  md5: '33c35774dac13280c348cbc2b8365b23',
  file_size: 48938,
  id_validation: 'lenient;versions=4,7',
  started_at: '2026-10-12T16:10:11Z',
  finished_at: '2026-10-12T16:10:12Z',
  error_message: null,
  total_records: 269,
  invalid_records: 3,
  skipped_records: 11,
  sellers_created: 20,
  products_created: 2,
  products_updated: 0,
  duplicate_products: 253,
  offers_created: 254,
  offers_updated: 1,
  unchanged: 0,
};

const page = (items, total = items.length) => ({ items, total, limit: 50, offset: 0 });

export const responses = {
  '/stats': { products: 977, brands: 635, categories: 43, sellers: 21, offers: 1229, last_import: importItem },
  '/products': page(
    [product, { ...product, id: 2, name: 'Inactive thing', active: false, brand: null, merged_into_id: 1 }],
    120,
  ),
  '/products/1': productDetail,
  '/brands': page([{ id: 30, name: 'Apple', active: true, merged_into_id: null, product_count: 6 }]),
  '/brands/30': { id: 30, name: 'Apple', active: true, merged_into_id: null, product_count: 6 },
  '/brands/30/sellers': page([{ id: 2, name: 'Tech World/Store', product_count: 1 }]),
  '/categories': page([{ id: 15, name: 'electronics', active: true, merged_into_id: null, product_count: 25 }]),
  '/categories/15': { id: 15, name: 'electronics', active: true, merged_into_id: null, product_count: 25 },
  '/categories/15/sellers': page([]),
  '/sellers': page([{ id: 2, name: 'Tech World/Store', active: true, offer_count: 13 }]),
  '/sellers/Tech%20World%2FStore': { id: 2, name: 'Tech World/Store', active: true, offer_count: 13 },
  '/sellers/Tech%20World%2FStore/products': page([
    {
      id: 10,
      seller_product_id: 'a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d',
      name: 'iPhone 15 PRO',
      brand_name: null,
      category_name: 'Phones',
      active: true,
      product: { id: 1, name: 'iPhone 15 Pro' },
      product_uuid: product.uuid,
      brand: { id: 30, name: 'Apple' },
    },
  ]),
  '/imports': page([importItem]),
  '/imports/4': {
    ...importItem,
    per_seller: { 'Tech World/Store': { offers_created: 13, invalid_records: 1 } },
    rejections: [{ index: 180, code: 'SQL_INJECTION', field: 'Brand' }],
    undos: [11],
  },
  '/merges': page([
    {
      operation_id: 1,
      kind: 'legacy_load',
      status: 'completed',
      entity_type: 'brand',
      kept_id: 24,
      merged_id: null,
      merged_name: 'Amazon Basics',
      origin: 'legacy_load',
      created: '2026-10-12T16:09:08Z',
    },
    {
      operation_id: 9,
      kind: 'merge',
      status: 'undone',
      entity_type: 'product',
      kept_id: 18,
      merged_id: 19,
      merged_name: null,
      origin: 'manual',
      created: '2026-10-12T16:12:21Z',
    },
  ]),
  '/undos': page([
    {
      operation_id: 11,
      status: 'completed',
      undo_kind: 'import_undo',
      criteria: {},
      changes_reverted: 281,
      conflicts_skipped: 0,
      targets: [4],
      created: '2026-10-12T16:12:21Z',
    },
  ]),
};
