import test from 'node:test';
import assert from 'node:assert/strict';
import {getAssistantReply} from '../js/buyer-assistant.js';

const noRequest = () => { throw new Error('This answer must not request private data.'); };

test('greetings, payment and checkout help need no data request', async () => {
  assert.match(await getAssistantReply('Hello!', noRequest), /find art/);
  assert.match(await getAssistantReply('What payment methods are supported?', noRequest), /cash on delivery and online payment/);
  assert.match(await getAssistantReply('How do I buy art?', noRequest), /one seller at a time/);
});
test('art search uses the live catalog, filters drafts and reports actual stock', async () => {
  const response = await getAssistantReply('Find green art', async path => {
    assert.equal(path, '/api/catalog');
    return {products: [
      {title:'Green study', status:'active', description:'Green landscape', inventory:2, price_cents:125050, display_name:'Test artist'},
      {title:'Green draft', status:'draft', inventory:4, price_cents:100},
      {title:'Blue study', status:'active', inventory:0, price_cents:100}
    ]};
  });
  assert.match(response, /Green study.*₱1,250\.50; 2 in stock/);
  assert.doesNotMatch(response, /Green draft|Blue study/);
  assert.match(await getAssistantReply('What is the price of the green painting?', async () => ({products:[
    {title:'Green study', status:'active', inventory:2, price_cents:125050}
  ]})), /Green study.*₱1,250\.50/);

  assert.match(await getAssistantReply('Find available art', async () => ({products:[]})), /couldn’t find matching/);
});
test('order lookup uses authenticated buyer data and includes delivered orders', async () => {
  const request = async path => {
    assert.equal(path, '/api/buyer/cart');
    return {orders:[{id:12, status:'pending', fulfillment_status:'packed', total_cents:2000, created_at:200}], completed_orders:[{id:11,status:'paid',fulfillment_status:'delivered',total_cents:3500,created_at:100}]};
  };
  const specific = await getAssistantReply('Track order #11', request);
  assert.match(specific, /Order #11: paid; delivery delivered. Total ₱35.00/);
  assert.doesNotMatch(specific, /Order #12/);
  assert.match(await getAssistantReply('Track order #999', request), /couldn’t find order #999 in your account/);
  assert.match(await getAssistantReply('Track my orders', async () => ({orders:[], completed_orders:[]})), /no orders yet/);
});
test('cart totals include quantities and empty carts have an actionable reply', async () => {
  const response = await getAssistantReply('Show my cart', async path => {
    assert.equal(path, '/api/buyer/cart');
    return {cart:[{title:'Small study', price_cents:2500, quantity:2},{title:'Print',price_cents:1000,quantity:1}]};
  });
  assert.match(response, /Small study × 2: ₱50.00/);
  assert.match(response, /Total: ₱60.00/);
  assert.match(await getAssistantReply('My cart', async () => ({cart:[]})), /cart is empty/);
});
test('refunds and unsupported questions route to support without inventing policies', async () => {
  assert.match(await getAssistantReply('Can I get a refund?', noRequest), /team can review/);
  assert.match(await getAssistantReply('What is the weather?', noRequest), /Contact support/);
});
test('failed data requests propagate so the widget can show its retry message', async () => {
  await assert.rejects(getAssistantReply('Track my orders', async () => { throw new Error('Not signed in'); }), /Not signed in/);
});
