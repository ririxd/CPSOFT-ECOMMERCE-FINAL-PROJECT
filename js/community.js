(() => {
  const accountId = Number(document.body.dataset.accountId || 0);
  const panel = document.getElementById('chat-panel');
  const socialDialog = document.getElementById('social-dialog');
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]));
  const name = profile => profile.display_name || profile.nickname || profile.username;
  const role = profile => profile.persona === 'artist' ? 'Seller' : 'Buyer';
  let following = new Set(), socialLoaded = false, partner = null, conversations = [];
  let listAccount = null, listKind = null, listOffset = 0, listLoading = false, listSequence = 0;
  let recentLoading = false, sending = false, searchSequence = 0, searchTimer;
  async function api(path, options = {}) {
    const response = await fetch(path, {credentials:'same-origin', ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Unable to complete that request.');
    return data;
  }
  function status(message) {
    const target = document.getElementById('community-status');
    if (target) target.textContent = message;
    if (panel) document.getElementById('chat-status').textContent = message;
    if (socialDialog.open) document.getElementById('social-list-status').textContent = message;
  }
  function updateCounts(id, data) {
    document.querySelectorAll(`[data-followers-count="${Number(id)}"]`).forEach(node => { node.textContent = data.followers_count; });
    document.querySelectorAll(`[data-following-count="${Number(id)}"]`).forEach(node => { node.textContent = data.following_count; });
  }
  function updateFollowButtons() {
    document.querySelectorAll('[data-follow-account]').forEach(button => {
      const id = Number(button.dataset.followAccount);
      button.hidden = id === accountId;
      if (socialLoaded || !accountId) {
        button.setAttribute('aria-pressed', String(following.has(id)));
        button.textContent = !accountId ? 'Sign in to follow' : following.has(id) ? 'Following' : 'Follow';
      }
    });
  }
  async function loadSocial() {
    if (!accountId) { updateFollowButtons(); return; }
    try {
      const data = await api('/api/social/me');
      following = new Set(data.following_ids.map(Number)); socialLoaded = true;
      updateCounts(accountId, data); updateFollowButtons();
    } catch (error) { status(error.message); }
  }
  document.addEventListener('community:rendered', updateFollowButtons);
  document.addEventListener('click', async event => {
    const followButton = event.target.closest('[data-follow-account]');
    if (followButton) {
      if (!accountId) { location.href = '/login.html'; return; }
      const id = Number(followButton.dataset.followAccount);
      if (followButton.disabled) return;
      followButton.disabled = true;
      try {
        if (!socialLoaded) await loadSocial();
        if (!socialLoaded) throw new Error('Please try again after your connections load.');
        const data = await api(`/api/profiles/${id}/follow`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({following:!following.has(id)})});
        if (data.is_following) following.add(id); else following.delete(id);
        updateCounts(id, data); await loadSocial(); updateFollowButtons(); status('');
        if (socialDialog.open && listAccount === accountId && listKind === 'following') loadList(true);
      } catch (error) { status(error.message); }
      finally { followButton.disabled = false; }
      return;
    }
    const listButton = event.target.closest('[data-social-list]');
    if (listButton) {
      listAccount = Number(listButton.dataset.socialAccount); listKind = listButton.dataset.socialList;
      listOffset = 0;
      document.getElementById('social-title').textContent = listKind === 'followers' ? 'Followers' : 'Following';
      document.getElementById('social-list').innerHTML = '';
      document.getElementById('social-list-status').textContent = '';
      if (!socialDialog.open) socialDialog.showModal();
      loadList(true);
      return;
    }
    const messageButton = event.target.closest('[data-message-account]');
    if (messageButton) {
      if (!accountId) { location.href = '/login.html'; return; }
      const id = Number(messageButton.dataset.messageAccount);
      if (!panel) { location.href = '/buyer-dashboard.html?chat=' + id; return; }
      if (socialDialog.open) socialDialog.close();
      try { choosePartner((await api('/api/profiles/' + id)).profile); setOpen(true); }
      catch (error) { status(error.message); }
    }
  });
  async function loadList(reset) {
    if (listLoading && !reset) return;
    const sequence = ++listSequence;
    listLoading = true;
    const target = listAccount, kind = listKind;
    const more = document.getElementById('social-more'); more.disabled = true;
    try {
      const data = await api(`/api/profiles/${target}/${kind}?offset=${reset ? 0 : listOffset}`);
      if (sequence !== listSequence || target !== listAccount || kind !== listKind) return;
      const rows = data.profiles.map(profile => `<div class="social-person"><a href="/profile.html?id=${profile.id}"><strong>${escape(name(profile))}</strong><small>@${escape(profile.username)} · ${role(profile)}</small></a>${Number(profile.id) !== accountId ? `<div class="social-person-actions"><button class="follow-button" type="button" data-follow-account="${profile.id}" aria-pressed="false">Follow</button><button class="social-message-button" type="button" data-message-account="${profile.id}">Chat</button></div>` : ''}</div>`).join('');
      if (reset) document.getElementById('social-list').innerHTML = rows || `<p class="muted">No ${kind} yet.</p>`;
      else document.getElementById('social-list').insertAdjacentHTML('beforeend', rows);
      listOffset = (reset ? 0 : listOffset) + data.profiles.length;
      more.hidden = !data.has_more; updateFollowButtons();
    } catch (error) { if (sequence === listSequence) document.getElementById('social-list-status').textContent = error.message; }
    finally { if (sequence === listSequence) { listLoading = false; more.disabled = false; } }
  }
  document.getElementById('social-close').addEventListener('click', () => socialDialog.close());
  document.getElementById('social-more').addEventListener('click', () => loadList(false));
  loadSocial();
  if (!panel) return;

  const toggle = document.getElementById('chat-toggle');
  const embedded = document.body.dataset.chatLayout === 'embedded';
  const desktop = window.matchMedia(embedded ? '(min-width:1000px)' : '(min-width:1280px)');
  const scrim = document.getElementById('community-scrim');
  const input = document.getElementById('chat-input');
  const compose = document.getElementById('chat-compose');
  const send = compose.querySelector('button');
  const messageLog = document.getElementById('chat-messages');
  const recent = document.getElementById('chat-conversations');
  const search = document.getElementById('chat-search');
  const results = document.getElementById('chat-results');
  let lastMessages = '', recentSnapshot = '';
  function setOpen(open) {
    panel.hidden = !open;
    document.body.classList.toggle('chat-open', open);
    toggle.setAttribute('aria-expanded', String(open)); toggle.setAttribute('aria-label', open ? 'Close messages' : 'Open messages');
    scrim.hidden = !open || desktop.matches;
    panel.setAttribute('role', desktop.matches ? 'complementary' : 'dialog');
    if (!desktop.matches && open) panel.setAttribute('aria-modal', 'true'); else panel.removeAttribute('aria-modal');
    const background = embedded ? 'body>header,main>.intro,main>.stats,#notice,.hub-workspace,.hub-sidebar' : 'body>header,body>main,#assistant-launcher,#buyer-assistant';
    document.querySelectorAll(background).forEach(node => { node.inert = !desktop.matches && open; });
    if (open && !desktop.matches) document.getElementById('chat-close').focus();
  }
  toggle.addEventListener('click', () => setOpen(panel.hidden));
  document.querySelectorAll('[data-open-inbox]').forEach(button => button.addEventListener('click', () => setOpen(true)));
  document.getElementById('chat-close').addEventListener('click', () => { setOpen(false); toggle.focus(); });
  scrim.addEventListener('click', () => { setOpen(false); toggle.focus(); });
  desktop.addEventListener('change', () => setOpen(desktop.matches));
  document.addEventListener('keydown', event => {
    if (panel.hidden || socialDialog.open) return;
    if (event.key === 'Escape' && (!desktop.matches || panel.contains(document.activeElement))) { setOpen(false); toggle.focus(); }
    if (event.key === 'Tab' && !desktop.matches) {
      const controls = [...panel.querySelectorAll('button,input,textarea,a[href]')].filter(node => !node.disabled && node.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (!controls.includes(document.activeElement)) { event.preventDefault(); first.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  setOpen(desktop.matches);
  function markPartner() {
    recent.querySelectorAll('[data-chat-account]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.chatAccount) === Number(partner?.id))));
  }
  function choosePartner(profile) {
    clearTimeout(searchTimer); ++searchSequence;
    partner = profile; lastMessages = '';
    search.value = ''; results.hidden = true;
    document.getElementById('chat-current').hidden = false;
    const title = document.getElementById('chat-name'); title.textContent = name(profile); title.href = '/profile.html?id=' + profile.id;
    document.getElementById('chat-role').textContent = role(profile);
    document.getElementById('chat-follow').dataset.followAccount = profile.id; updateFollowButtons();
    input.value = ''; input.disabled = false; send.disabled = sending;
    messageLog.innerHTML = '<p class="muted">Loading messages…</p>'; markPartner(); refreshMessages();
  }
  async function loadRecent() {
    if (recentLoading) return;
    recentLoading = true;
    try {
      const data = await api('/api/chat/conversations');
      const snapshot = JSON.stringify(data.conversations);
      if (snapshot !== recentSnapshot) {
        const focused = recent.contains(document.activeElement) ? document.activeElement.dataset.chatAccount : null;
        conversations = data.conversations; recentSnapshot = snapshot;
        recent.innerHTML = conversations.length ? conversations.map(profile => `<button type="button" class="community-conversation" data-chat-account="${profile.id}" aria-pressed="false"><span class="community-conversation-heading"><strong>${escape(name(profile))}</strong><time datetime="${new Date(Number(profile.last_message_at)).toISOString()}">${escape(new Date(Number(profile.last_message_at)).toLocaleDateString())}</time></span><span class="community-conversation-preview">${escape((Number(profile.sender_id) === accountId ? 'You: ' : '') + profile.last_message)}</span></button>`).join('') : '<p class="muted">No conversations yet. Search for someone to chat.</p>';
        markPartner(); if (focused) recent.querySelector(`[data-chat-account="${Number(focused)}"]`)?.focus();
      }
      document.getElementById('chat-recent-status').textContent = '';
    } catch { document.getElementById('chat-recent-status').textContent = 'Unable to load conversations. Retrying…'; }
    finally { recentLoading = false; }
  }
  recent.addEventListener('click', event => {
    const button = event.target.closest('[data-chat-account]'); if (!button) return;
    const profile = conversations.find(profile => Number(profile.id) === Number(button.dataset.chatAccount));
    if (profile) choosePartner(profile);
  });
  async function refreshMessages() {
    if (!partner) return;
    const selected = partner;
    try {
      const data = await api('/api/chat?account_id=' + selected.id);
      if (selected !== partner) return;
      const snapshot = JSON.stringify(data.messages);
      if (snapshot !== lastMessages) {
        const atBottom = messageLog.scrollHeight - messageLog.scrollTop - messageLog.clientHeight < 40 || !lastMessages;
        lastMessages = snapshot;
        messageLog.innerHTML = data.messages.length ? data.messages.map(message => `<p class="community-message${Number(message.sender_id) === accountId ? ' mine' : ''}">${escape(message.body)}<small>${escape(message.sender_name)}</small></p>`).join('') : '<p class="muted">No messages yet. Say hello!</p>';
        if (atBottom) messageLog.scrollTop = messageLog.scrollHeight;
      }
      document.getElementById('chat-status').textContent = '';
    } catch (error) { if (selected === partner) document.getElementById('chat-status').textContent = error.message; }
  }
  search.addEventListener('input', () => {
    clearTimeout(searchTimer); const sequence = ++searchSequence, query = search.value.trim();
    if (query.length < 2) { results.hidden = true; return; }
    searchTimer = setTimeout(async () => {
      try {
        const data = await api('/api/search?q=' + encodeURIComponent(query));
        if (sequence !== searchSequence) return;
        const profiles = data.profiles.filter(profile => Number(profile.id) !== accountId);
        results.innerHTML = profiles.length ? profiles.map(profile => `<button type="button" class="community-chat-result" data-search-account="${profile.id}"><strong>${escape(name(profile))}</strong><small>@${escape(profile.username)} · ${role(profile)}</small></button>`).join('') : '<p class="muted">No matching accounts.</p>';
        results.hidden = false;
        results.querySelectorAll('[data-search-account]').forEach(button => button.addEventListener('click', () => choosePartner(profiles.find(profile => Number(profile.id) === Number(button.dataset.searchAccount)))));
      } catch (error) { if (sequence === searchSequence) { results.hidden = true; status(error.message); } }
    }, 220);
  });
  compose.addEventListener('submit', async event => {
    event.preventDefault(); if (!partner || !input.value.trim() || sending) return;
    const selected = partner, text = input.value.trim(); sending = true; send.disabled = true;
    try {
      await api('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({account_id:selected.id, message:text})});
      if (selected === partner) { input.value = ''; await refreshMessages(); }
      await loadRecent();
    } catch (error) { if (selected === partner) status(error.message); }
    finally { sending = false; send.disabled = !partner; }
  });
  loadRecent();
  setInterval(() => { loadRecent(); if (!panel.hidden) refreshMessages(); }, 5000);
  setInterval(loadSocial, 15000);
  const requested = new URLSearchParams(location.search).get('chat');
  if (requested && /^\d+$/.test(requested) && Number(requested) !== accountId) {
    api('/api/profiles/' + requested).then(data => { choosePartner(data.profile); setOpen(true); }).catch(error => status(error.message));
  }
})();
