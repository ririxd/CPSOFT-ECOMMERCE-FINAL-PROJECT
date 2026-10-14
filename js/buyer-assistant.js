const currency = cents => '₱' + (Number(cents || 0) / 100).toLocaleString('en-PH', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const ignoredWords = new Set('a an the is are do does you your i my me can could would what which of to from and it there tell please want looking look for find search show recommend art artwork artworks product products painting paintings print prints available stock price prices cost how much buy purchase in on have any some'.split(' '));

// Replies use the existing authenticated store APIs; no messages leave this site.
export async function getAssistantReply(message, request) {
  const text = message.trim().toLowerCase();
  if (!text || /^(hi|hello|hey|thanks|thank you)[!.\s]*$/.test(text)) {
    return 'Hi! I can help you find art, check your cart or orders, and explain payments. What would you like to know?';
  }
  if (/\b(refunds?|returns?|cancel|cancellations?|support|human|agent|complaints?)\b/.test(text)) {
    return 'For cancellations, returns, refunds, or help from the team, choose “Contact support” below and send a request. The team can review your situation. For questions about a specific artwork, use “Chat with a seller” on this page.';
  }
  if (/\b(checkout|check out|purchase)\b/.test(text) || /how.*\b(buy|order)\b/.test(text)) {
    return 'To buy artwork, add an available piece to your cart and check out with products from one seller at a time. Choose cash on delivery or online payment. For online payment, use the seller’s payment details in your cart and enter your payment reference. The seller confirms the order.';
  }
  if (/\b(pay|payment|payments|cod|cash|bank|wallet|gcash)\b/.test(text)) {
    return 'Art House supports cash on delivery and online payment. Select a method at checkout. For online payment, follow the seller’s account or wallet instructions shown in your cart and enter a payment reference. If no payment details are listed, contact the seller before paying.';
  }
  if (/\b(order|orders|track|tracking|delivery|delivered|shipping|shipped|dispatch|dispatched|packed)\b/.test(text)) {
    const data = await request('/api/buyer/cart');
    let orders = [...(data.orders || []), ...(data.completed_orders || [])]
      .sort((a, b) => Number(b.created_at) - Number(a.created_at) || Number(b.id) - Number(a.id));
    const requested = text.match(/(?:\border\s*#?\s*|#)(\d+)\b/);
    if (requested) {
      orders = orders.filter(order => String(order.id) === String(Number(requested[1])));
      if (!orders.length) return `I couldn’t find order #${requested[1]} in your account. Check the number in “Your orders” or contact support.`;
    }
    if (!orders.length) return 'You have no orders yet. Add an artwork to your cart and check out to place your first order.';
    return 'Here are your ' + (requested ? 'order details' : 'latest orders') + ':\n\n' + orders.slice(0, 3).map(order => `Order #${order.id}: ${order.status}; delivery ${order.fulfillment_status}. Total ${currency(order.total_cents)}.`).join('\n') + '\n\nFor delivery dates or changes, contact the seller. I can show recorded statuses, but cannot confirm an arrival date.';
  }
  if (/\b(cart|bag|basket)\b/.test(text)) {
    const data = await request('/api/buyer/cart');
    const cart = data.cart || [];
    if (!cart.length) return 'Your cart is empty. Browse the collection and choose “Add to cart” on an available artwork.';
    const total = cart.reduce((sum, item) => sum + Number(item.price_cents) * Number(item.quantity), 0);
    return 'Your cart:\n\n' + cart.slice(0, 5).map(item => `${item.title} × ${item.quantity}: ${currency(Number(item.price_cents) * Number(item.quantity))}`).join('\n') + (cart.length > 5 ? `\n…and ${cart.length - 5} more items.` : '') + `\n\nTotal: ${currency(total)}. You can adjust quantities or check out in the cart on this page.`;
  }
  if (/\b(seller|artist|message|chat)\b/.test(text)) {
    return 'Use “Chat with a seller” below the collection. Search for the artist, choose their account, and send a message. You can reopen previous chats from “Recent conversations”.';
  }
  const terms = (text.match(/[\p{L}\p{N}]+/gu) || []).filter(word => !ignoredWords.has(word));
  const asksForArt = /\b(art|artwork|artworks|product|products|painting|paintings|print|prints|stock|available|price|cost|find|search)\b/.test(text);
  if (asksForArt) {
    const data = await request('/api/catalog');
    const products = (data.products || []).filter(product => product.status === 'active');
    const matches = products.filter(product => {
      const description = [product.title, product.description, product.display_name, product.dimensions].join(' ').toLowerCase();
      return terms.every(term => description.includes(term));
    });
    if (!matches.length) return 'I couldn’t find matching active artwork. Try an artwork title or artist name, or browse the collection on this page.';
    return 'Here are matching artworks:\n\n' + matches.slice(0, 3).map(product => `${product.title} by ${product.display_name || 'Art House'} — ${currency(product.price_cents)}; ${Number(product.inventory) > 0 ? product.inventory + ' in stock' : 'out of stock'}.`).join('\n') + '\n\nOpen the artwork in the collection to see its details.';
  }
  return 'I can help with finding art, prices and stock, your cart, order status, checkout, and payments. Try “Find available art” or “Track my orders”. For anything else, choose “Contact support”.';
}

function initializeAssistant() {
  const panel = document.getElementById('buyer-assistant');
  if (!panel) return;
  const launcher = document.getElementById('assistant-launcher');
  const form = document.getElementById('assistant-form');
  const input = document.getElementById('assistant-input');
  const messages = document.getElementById('assistant-messages');
  const status = document.getElementById('assistant-status');
  const send = form.querySelector('button');
  const suggestions = [...panel.querySelectorAll('[data-assistant-prompt]')];
  let busy = false;
  function setOpen(open) {
    panel.hidden = !open;
    launcher.setAttribute('aria-expanded', String(open));
    if (open) input.focus(); else launcher.focus();
  }
  function appendMessage(text, mine = false) {
    const message = document.createElement('p');
    message.className = 'assistant-message' + (mine ? ' mine' : '');
    const label = document.createElement('span');
    label.className = 'assistant-message-label';
    label.textContent = mine ? 'You' : 'Art House assistant';
    message.append(label, document.createTextNode(text));
    messages.append(message);
    // Bound the on-page conversation without storing private replies in browser storage.
    if (messages.children.length > 60) messages.firstElementChild.remove();
    messages.scrollTop = messages.scrollHeight;
  }
  async function request(path) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store'});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error('Request failed');
    return data;
  }
  async function ask(question) {
    question = question.trim();
    if (!question || busy) return;
    busy = true;
    send.disabled = true;
    suggestions.forEach(button => { button.disabled = true; });
    input.value = '';
    appendMessage(question, true);
    status.textContent = 'Checking…';
    try { appendMessage(await getAssistantReply(question, request)); }
    catch { appendMessage('I couldn’t load that information right now. Please try again, check that you’re signed in, or contact support.'); }
    finally {
      busy = false; send.disabled = false;
      suggestions.forEach(button => { button.disabled = false; });
      status.textContent = '';
      if (!panel.hidden) input.focus();
    }
  }
  launcher.addEventListener('click', () => setOpen(panel.hidden));
  document.getElementById('assistant-close').addEventListener('click', () => setOpen(false));
  panel.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); } });
  form.addEventListener('submit', event => { event.preventDefault(); ask(input.value); });
  suggestions.forEach(button => button.addEventListener('click', () => ask(button.dataset.assistantPrompt)));
  document.getElementById('assistant-support').addEventListener('click', () => { setOpen(false); document.getElementById('support-button').click(); });
}
if (typeof document !== 'undefined') initializeAssistant();
