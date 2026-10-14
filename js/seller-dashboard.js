import {buildSellerReport, serializeCsv} from './seller-reports.js';
const tabs = [...document.querySelectorAll('[data-seller-tab]')];
const views = new Set(tabs.map(tab => tab.dataset.sellerTab));
const panels = [...document.querySelectorAll('.seller-panel')];
let currentView = 'overview';
const viewFromHash = () => ({dashboard:'overview', 'my-art':'art', 'fulfill-orders':'orders'}[location.hash.slice(1)] || location.hash.slice(1));
function activateView(view, {history = true, focus = false} = {}) {
  if (!views.has(view)) view = 'overview';
  currentView = view;
  tabs.forEach(tab => {
    const selected = tab.dataset.sellerTab === view;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  panels.forEach(panel => { panel.hidden = panel.id !== 'panel-' + view; });
  document.getElementById('chat-panel').hidden = view !== 'inbox';
  document.body.classList.toggle('chat-open', view === 'inbox');
  if (history) {
    const url = new URL(location.href);
    url.hash = view;
    if (view !== 'inbox') url.searchParams.delete('chat');
    if (url.href !== location.href) window.history.pushState(null, '', url);
  }
  const selected = tabs.find(tab => tab.dataset.sellerTab === view);
  if (focus) selected.focus({preventScroll:true});
  if (focus || history) selected.scrollIntoView({block:'nearest', inline:'nearest', behavior:'auto'});
}
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateView(tab.dataset.sellerTab));
  tab.addEventListener('keydown', event => {
    const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length
      : event.key === 'ArrowLeft' ? (index - 1 + tabs.length) % tabs.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    activateView(tabs[next].dataset.sellerTab, {focus:true});
  });
});
document.querySelectorAll('[data-seller-view]').forEach(button => button.addEventListener('click', () => activateView(button.dataset.sellerView, {focus:true})));
document.addEventListener('community:chat-visibility', event => {
  if (event.detail.open) activateView('inbox', {focus:currentView !== 'inbox'});
  else if (currentView === 'inbox') activateView('overview', {focus:true});
});
window.addEventListener('hashchange', () => activateView(viewFromHash(), {history:false}));
activateView(new URLSearchParams(location.search).has('chat') ? 'inbox' : viewFromHash(), {history:false});

let latestDashboard = null;
const reportType = document.getElementById('report-type');
function updateReportSummary() {
  if (!latestDashboard) return;
  const count = buildSellerReport(latestDashboard, reportType.value).length - 1;
  document.getElementById('report-summary').textContent = `${count} ${count === 1 ? 'row' : 'rows'} in the current report. Export always uses your latest store data.`;
}
reportType.addEventListener('change', () => { document.getElementById('report-status').textContent = ''; updateReportSummary(); });
document.getElementById('report-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.getElementById('export-csv');
  const status = document.getElementById('report-status');
  if (button.disabled) return;
  const type = reportType.value;
  button.disabled = true; reportType.disabled = true;
  status.textContent = 'Preparing your report…';
  try {
    const data = await api('/api/seller/dashboard');
    latestDashboard = data; updateReportSummary();
    const rows = buildSellerReport(data, type);
    const url = URL.createObjectURL(new Blob([serializeCsv(rows)], {type:'text/csv;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url; link.download = `art-house-${type}-${new Date().toISOString().slice(0,10)}.csv`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = `${type.charAt(0).toUpperCase() + type.slice(1)} report exported: ${rows.length - 1} ${rows.length === 2 ? 'row' : 'rows'}.`;
  } catch (error) { status.textContent = 'Unable to export: ' + error.message + ' Please try again.'; }
  finally { button.disabled = false; reportType.disabled = false; }
});

const notice = document.getElementById('notice');
const money = cents => '₱' + (Number(cents || 0) / 100).toFixed(2);
const date = value => value ? new Date(Number(value)).toLocaleDateString() : '—';
function showError(message) { notice.textContent = message; notice.classList.add('show'); }
function clearError() { notice.textContent = ''; notice.classList.remove('show'); }
async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Unable to complete that request.');
    return data;
}
const artworkTotal = order => order.items.reduce((sum, item) => sum + Number(item.quantity) * Number(item.unit_price_cents), 0);
function orderMarkup(order, completed = false) {
    const status = completed ? 'Delivered' : order.status === 'pending' ? 'Awaiting payment' : order.fulfillment_status === 'dispatched' ? 'Shipped' : 'Packed';
    const action = order.status === 'pending'
        ? `<button type="button" data-verify="${order.id}">Confirm payment</button>`
        : order.status === 'paid' && !completed
            ? `<button type="button" data-order="${order.id}" data-fulfill="${order.fulfillment_status === 'dispatched' ? 'delivered' : 'dispatched'}">${order.fulfillment_status === 'dispatched' ? 'Mark delivered' : 'Ship order'}</button>` : '';
    return `<div class="order"><div class="order-head"><strong>Order #${order.id}</strong><span class="pill">${status}</span></div><p>${order.items.map(item => `${escapeHtml(item.title)} × ${item.quantity}`).join(', ')}</p><p class="muted">${money(artworkTotal(order))} · ${date(order.created_at)}</p><p class="muted">Payment: ${escapeHtml(order.payment_method === 'cod' ? 'Cash on delivery' : order.payment_method || 'online')}${order.payment_reference ? ` · ${escapeHtml(order.payment_reference)}` : ''}</p>${action ? `<div class="order-actions">${action}</div>` : ''}</div>`;
}
function render(data) {
    latestDashboard = data;
    updateReportSummary();
    const products = data.products || [], completedOrders = data.completed_orders || [];
    const allOrders = [...(data.orders || []), ...completedOrders];
    const orders = (data.orders || []).filter(order => ['pending','paid'].includes(order.status));
    const paid = allOrders.filter(order => order.status === 'paid');
    const active = products.filter(item => item.status === 'active').length;
    document.getElementById('product-count').textContent = active;
    document.getElementById('stock-count').textContent = products.reduce((sum, item) => sum + Number(item.inventory), 0);
    document.getElementById('pending-count').textContent = orders.length;
    document.getElementById('sales-total').textContent = money(paid.reduce((sum, order) => sum + artworkTotal(order), 0));
    document.getElementById('activity-orders').textContent = orders.length;
    document.getElementById('activity-listings').textContent = active;
    document.getElementById('product-summary').textContent = products.length + (products.length === 1 ? ' artwork' : ' artworks') + ' in your collection';
    const start = new Date(); start.setHours(0,0,0,0); start.setDate(start.getDate() - 6);
    const days = Array.from({length:7}, (_, index) => { const d = new Date(start); d.setDate(d.getDate() + index); return d; });
    const amounts = days.map(day => paid.filter(order => new Date(Number(order.paid_at)).toDateString() === day.toDateString()).reduce((sum, order) => sum + artworkTotal(order), 0));
    const highest = Math.max(...amounts, 1);
    document.getElementById('earnings-trend').setAttribute('points', amounts.map((amount, index) => `${2 + index * 11},${28 - amount / highest * 24}`).join(' '));
    document.getElementById('products').innerHTML = products.length ? products.map(item => `<article class="product">${item.images && item.images[0] ? `<img class="product-image" src="${escapeHtml(item.images[0])}" alt="${escapeHtml(item.title)}" loading="lazy">` : '<div class="product-image" aria-label="No artwork image"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="1"/><circle cx="8" cy="8" r="2"/><path d="m3 17 6-6 4 4 3-3 5 5"/></svg></div>'}<h3>${escapeHtml(item.title)}</h3><p>${Number(item.inventory)} in stock</p><p class="product-price">${money(item.price_cents)}</p>${item.dimensions ? `<p>${escapeHtml(item.dimensions)}</p>` : ''}<div class="product-bottom"><span class="availability ${Number(item.inventory) <= Number(item.low_stock_threshold) ? 'low' : ''}">${item.status !== 'active' ? 'Unlisted' : Number(item.inventory) > 0 ? 'Available' : 'Sold out'}</span><button type="button" class="small-button" data-stock="${item.id}" aria-label="Adjust stock for ${escapeHtml(item.title)}">Stock</button></div></article>`).join('') : '<p class="product-empty">Your next collection starts here. Add your first artwork.</p>';
    document.getElementById('orders').innerHTML = orders.length ? orders.map(order => orderMarkup(order)).join('') : '<p class="muted">You’re all caught up. New orders will appear here.</p>';
    document.getElementById('completed-orders').innerHTML = completedOrders.length ? completedOrders.map(order => orderMarkup(order, true)).join('') : '<p class="muted">Delivered orders will appear here.</p>';
    if (data.seller) document.getElementById('payment-details').value = data.seller.payment_details || '';
}
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char])); }
async function load() { try { clearError(); render(await api('/api/seller/dashboard')); } catch (error) { showError(error.message); } }
const searchInput = document.querySelector('#market-search input'), searchDropdown = document.getElementById('search-dropdown');
function renderSearch(result) { const products=(result.products||[]).slice(0,4), profiles=(result.profiles||[]).slice(0,4); const rows=[...profiles.map(p=>`<a class="search-result" href="/profile.html?id=${p.id}"><span class="suggested-avatar"></span><span><strong>${escapeHtml(p.display_name||p.nickname||p.username)}</strong><small>${escapeHtml(p.persona||'Art House member')} · Profile</small></span></a>`),...products.map(p=>`<a class="search-result" href="/profile.html?id=${p.seller_id}"><span class="suggested-avatar"></span><span><strong>${escapeHtml(p.title)}</strong><small>${money(p.price_cents)} · ${escapeHtml(p.display_name||'Art House')}</small></span></a>` )]; searchDropdown.innerHTML=rows.length?rows.join(''):'<p class="search-result"><small>No matching products or profiles.</small></p>'; searchDropdown.hidden=false; }
let searchTimer; searchInput.addEventListener('input',()=>{clearTimeout(searchTimer);const q=searchInput.value.trim();if(!q){searchDropdown.hidden=true;return}searchTimer=setTimeout(async()=>{try{renderSearch(await api('/api/search?q='+encodeURIComponent(q)))}catch(error){searchDropdown.hidden=true}},220)}); document.getElementById('market-search').addEventListener('submit',event=>{event.preventDefault();searchInput.dispatchEvent(new Event('input'))}); document.addEventListener('click',event=>{if(!event.target.closest('.search-wrap'))searchDropdown.hidden=true});
async function loadSuggested(){try{const result=await api('/api/suggested-profiles');document.getElementById('suggested-accounts').innerHTML=result.profiles.length?result.profiles.map(p=>`<div class="suggested-person"><a class="suggested-account" href="/profile.html?id=${p.id}"><span class="suggested-avatar"></span><span><strong>${escapeHtml(p.display_name||p.nickname||p.username)}</strong><small>${escapeHtml(p.persona||'Art House member')}</small></span></a><button type="button" class="follow-button" data-follow-account="${p.id}" aria-pressed="false">Follow</button></div>`).join(''):'<p class="muted">No suggestions yet.</p>';document.dispatchEvent(new Event('community:rendered'))}catch(error){document.getElementById('suggested-accounts').innerHTML='<p class="muted">Suggestions unavailable.</p>'}}
const supportModal=document.getElementById('support-modal');function closeSupport(){supportModal.hidden=true}document.getElementById('support-button').addEventListener('click',()=>{supportModal.hidden=false;document.querySelector('#support-form input').focus()});document.getElementById('support-close').addEventListener('click',closeSupport);document.getElementById('support-cancel').addEventListener('click',closeSupport);supportModal.addEventListener('click',event=>{if(event.target===supportModal)closeSupport()});document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!supportModal.hidden)closeSupport()});document.getElementById('support-form').addEventListener('submit',async event=>{event.preventDefault();const status=document.getElementById('support-status');try{await api('/api/support',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});status.textContent='Support request sent.';event.target.reset();setTimeout(closeSupport,900)}catch(error){status.textContent=error.message}});
const artworkDialog = document.getElementById('artwork-dialog');
document.querySelector('[data-avatar-initial]').textContent = document.querySelector('.hub-profile-name').textContent.trim().charAt(0).toUpperCase();
document.querySelectorAll('[data-add-artwork]').forEach(button => button.addEventListener('click', () => { document.getElementById('artwork-status').textContent = ''; artworkDialog.showModal(); }));
document.getElementById('artwork-close').addEventListener('click', () => artworkDialog.close());
const imageInput = document.getElementById('product-images');
imageInput.addEventListener('change', () => {
    const files = [...imageInput.files];
    if (files.length > 5 || files.some(file => file.size > 1500000)) {
        imageInput.value = '';
        document.getElementById('artwork-status').textContent = 'Choose no more than five images, each 1.5 MB or smaller.';
        document.getElementById('image-preview').innerHTML = '';
        return;
    }
    document.getElementById('image-preview').innerHTML = files.map(file => `<img src="${URL.createObjectURL(file)}" alt="${escapeHtml(file.name)}">`).join('');
});
const readImage = file => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('Unable to read one of the selected images.'));
    reader.readAsDataURL(file);
});
document.getElementById('product-form').addEventListener('submit', async event => {
    event.preventDefault();
    const submit = event.target.querySelector('button[type="submit"]');
    submit.disabled = true;
    document.getElementById('artwork-status').textContent = '';
    try {
        const form = new FormData(event.target);
        const images = await Promise.all([...imageInput.files].map(readImage));
        const product = Object.fromEntries(form);
        delete product.images;
        product.images = images;
        await api('/api/seller/products', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(product) });
        event.target.reset();
        document.getElementById('image-preview').innerHTML = '';
        artworkDialog.close();
        await load();
    } catch (error) { document.getElementById('artwork-status').textContent = error.message; }
    finally { submit.disabled = false; }
});
const stockModal = document.getElementById('stock-modal');
const stockForm = document.getElementById('stock-form');
const stockQuantity = document.getElementById('stock-quantity');
const stockModalStatus = document.getElementById('stock-modal-status');
let stockProductId = '';
function closeStockModal() {
    stockModal.hidden = true;
    stockProductId = '';
    stockForm.reset();
    stockModalStatus.textContent = '';
}
function openStockModal(productId) {
    stockProductId = productId;
    stockModal.hidden = false;
    stockQuantity.focus();
}
document.getElementById('stock-modal-close').addEventListener('click', closeStockModal);
document.getElementById('stock-modal-cancel').addEventListener('click', closeStockModal);
stockModal.addEventListener('click', event => { if (event.target === stockModal) closeStockModal(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape' && !stockModal.hidden) closeStockModal(); });
stockForm.addEventListener('submit', async event => {
    event.preventDefault();
    stockModalStatus.textContent = '';
    const quantity = stockQuantity.value.trim();
    if (!/^-?\d+$/.test(quantity)) {
        stockModalStatus.textContent = 'Enter a whole number.';
        stockQuantity.focus();
        return;
    }
    try {
        await api('/api/seller/products/' + stockProductId + '/stock', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quantity_delta: quantity, note: 'Dashboard adjustment' }) });
        closeStockModal();
        await load();
    } catch (error) {
        stockModalStatus.textContent = error.message;
    }
});
document.getElementById('products').addEventListener('click', async event => {
    const button = event.target.closest('[data-stock]');
    if (!button) return;
    openStockModal(button.dataset.stock);
});
document.getElementById('orders').addEventListener('click', async event => {
    const button = event.target.closest('[data-verify]');
    if (!button) return;
    try { await api('/api/seller/orders/' + button.dataset.verify + '/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmed: true }) }); await load(); } catch (error) { showError(error.message); }
});
document.getElementById('orders').addEventListener('click', async event => {
    const button = event.target.closest('[data-fulfill]');
    if (!button || button.disabled) return;
    button.disabled = true;
    try { await api('/api/seller/orders/' + button.dataset.order + '/status', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fulfillment_status: button.dataset.fulfill }) }); await load(); } catch (error) { showError(error.message); }
    finally { button.disabled = false; }
});
document.getElementById('payment-form').addEventListener('submit', async event => {
    event.preventDefault();
    try { await api('/api/seller/profile/payment-details', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payment_details: document.getElementById('payment-details').value }) }); clearError(); document.getElementById('payment-status').textContent = 'Payment details saved.'; } catch (error) { document.getElementById('payment-status').textContent = error.message; }
});
load(); loadSuggested();
