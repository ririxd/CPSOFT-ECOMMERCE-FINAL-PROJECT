const amount = cents => (Number(cents || 0) / 100).toFixed(2);
const timestamp = value => {
  if (!value) return '';
  const date = new Date(Number(value));
  return Number.isNaN(date.getTime()) ? '' : date.toISOString();
};

// Quote every field, including embedded quotes/newlines. Treat spreadsheet formulas as text.
export function serializeCsv(rows) {
  const field = value => {
    let text = String(value ?? '');
    if (typeof value === 'string' && /^[\s\x00-\x1f]*[=+\-@]/u.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  return '\uFEFF' + rows.map(row => row.map(field).join(',')).join('\r\n') + '\r\n';
}

export function buildSellerReport(data, type) {
  const orders = [...(data.orders || []), ...(data.completed_orders || [])];
  if (type === 'inventory') {
    return [
      ['Artwork ID', 'Title', 'Status', 'Unit price (PHP)', 'Stock', 'Low-stock threshold', 'Dimensions', 'Created at (UTC)'],
      ...(data.products || []).map(item => [item.id, item.title, item.status, amount(item.price_cents), item.inventory, item.low_stock_threshold, item.dimensions || '', timestamp(item.created_at)])
    ];
  }
  if (type !== 'orders' && type !== 'sales') throw new Error('Choose a valid report type.');
  return [
    ['Order ID', 'Created at (UTC)', 'Paid at (UTC)', 'Payment status', 'Delivery status', 'Payment method', 'Artwork ID', 'Artwork', 'Quantity', 'Unit price (PHP)', 'Line total (PHP)'],
    ...orders.filter(order => type !== 'sales' || order.status === 'paid').flatMap(order =>
      (order.items || []).map(item => [order.id, timestamp(order.created_at), timestamp(order.paid_at), order.status, order.fulfillment_status, order.payment_method || '', item.item_id, item.title, item.quantity, amount(item.unit_price_cents), amount(Number(item.quantity) * Number(item.unit_price_cents))]))
  ];
}
