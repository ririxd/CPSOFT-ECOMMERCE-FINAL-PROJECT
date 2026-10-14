import test from 'node:test';
import assert from 'node:assert/strict';
import {buildSellerReport, serializeCsv} from '../js/seller-reports.js';

const item = {item_id:9, title:'Green, "study"\n油画', quantity:2, unit_price_cents:125050};
const order = (id, status, fulfillment_status) => ({
  id, status, fulfillment_status, payment_method:'cod', total_cents:9999999,
  created_at:1767225600000, paid_at:status === 'paid' ? 1767312000000 : null,
  items:[item]
});
const data = {
  orders:[order(1,'paid','packed'),order(2,'pending','packed'),order(3,'cancelled','packed'),order(4,'refunded','packed')],
  completed_orders:[order(5,'paid','delivered')],
  products:[{id:9,title:item.title,status:'active',price_cents:125050,inventory:0,low_stock_threshold:3,dimensions:'20 × 30 cm',created_at:1767225600000}]
};
test('sales include paid and delivered orders and only the seller line amounts', () => {
  const rows = buildSellerReport(data,'sales');
  assert.deepEqual(rows.slice(1).map(row=>row[0]),[1,5]);
  assert.equal(rows[1][9],'1250.50');
  assert.equal(rows[1][10],'2501.00');
  assert.equal(rows[2][4],'delivered');
  assert.equal(rows[1][1],'2026-01-01T00:00:00.000Z');
  assert.equal(rows[1][2],'2026-01-02T00:00:00.000Z');
  assert.doesNotMatch(serializeCsv(rows),/99999\.99/);
});
test('orders include all statuses, every line, and blank unpaid timestamps', () => {
  const rows = buildSellerReport({...data,orders:[...data.orders,{...order(6,'paid','dispatched'),items:[item,{...item,item_id:10,quantity:1}]}]},'orders');
  assert.equal(rows.length,8);
  assert.deepEqual(rows.slice(1).map(row=>row[3]),['paid','pending','cancelled','refunded','paid','paid','paid']);
  assert.equal(rows[2][2],'');
  assert.equal(rows[6][6],10);
});
test('inventory includes zero stock and drafts with exact price, dimensions, threshold', () => {
  const rows=buildSellerReport({...data,products:[...data.products,{...data.products[0],id:10,status:'draft',inventory:5,price_cents:1}]},'inventory');
  assert.deepEqual(rows[1],[9,item.title,'active','1250.50',0,3,'20 × 30 cm','2026-01-01T00:00:00.000Z']);
  assert.equal(rows[2][2],'draft');
  assert.equal(rows[2][3],'0.01');
});
test('empty exports contain headers; unknown types are rejected', () => {
  for(const type of ['sales','orders','inventory']) assert.equal(buildSellerReport({},type).length,1);
  assert.throws(()=>buildSellerReport({},'credentials'),/valid report/);
});
test('CSV supports UTF-8, quoted commas, embedded quotes and line breaks', () => {
  assert.equal(serializeCsv([['Artwork','Stock'],[item.title,0]]),'\uFEFF"Artwork","Stock"\r\n"Green, ""study""\n油画","0"\r\n');
});
test('untrusted spreadsheet formulas are exported as text, including whitespace prefixes', () => {
  for(const text of ['=HYPERLINK("https://example.test")','+cmd','-cmd','@SUM(A1)',' \t=1+1','\r\n+cmd']) {
    const result=serializeCsv([[text]]);
    assert.ok(result.startsWith('\uFEFF"\''),JSON.stringify(text));
  }
  assert.equal(serializeCsv([['Normal - title',-3,null]]),'\uFEFF"Normal - title","-3",""\r\n');
});
