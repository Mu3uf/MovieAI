/* CineMind frontend. No secrets here: the Supabase ANON key is public by design (protected by RLS);
   TMDB / LLM / service-role keys exist only on the server. */
(() => {
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const isMobile = () => window.matchMedia('(max-width: 900px)').matches;

  let sb = null, entered = false, convId = null, busy = false, lastText = '';
  let homeData = null, view = { kind: 'home' }, authMode = 'login';
  const marks = new Set(); // "tmdbId:type"

  /* ------------------------------------------------------------ helpers */
  function toast(msg) {
    const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden');
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add('hidden'), 3200);
  }
  async function api(path, opts = {}, auth = true) {
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    if (auth) {
      const { data } = await sb.auth.getSession();
      if (!data.session) throw new Error('Please log in again.');
      headers.Authorization = 'Bearer ' + data.session.access_token;
    }
    const res = await fetch(path, { ...opts, headers });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { const e = new Error(body.error || `Request failed (${res.status})`); e.status = res.status; throw e; }
    return body;
  }

  /* ------------------------------------------------------------ auth */
  function setAuthMode(mode) {
    authMode = mode;
    $('#authSubmit').textContent = mode === 'login' ? 'Log in' : 'Create account';
    $('#authToggle').textContent = mode === 'login' ? 'No account? Sign up' : 'Have an account? Log in';
    $('#password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
    $('#authMsg').textContent = '';
  }
  async function submitAuth(ev) {
    ev.preventDefault();
    const email = $('#email').value.trim(), password = $('#password').value;
    const msg = $('#authMsg'), btn = $('#authSubmit');
    msg.className = 'auth-msg'; msg.textContent = ''; btn.disabled = true;
    try {
      if (authMode === 'login') {
        const { error } = await sb.auth.signInWithPassword({ email, password });
        if (error) throw error;
      } else {
        const { data, error } = await sb.auth.signUp({ email, password });
        if (error) throw error;
        if (!data.session) { msg.className = 'auth-msg ok'; msg.textContent = 'Account created. Check your email to confirm, then log in.'; setAuthMode('login'); }
      }
    } catch (e) { msg.textContent = e.message || 'Something went wrong.'; }
    btn.disabled = false;
  }

  async function enterApp(session) {
    if (entered) return; entered = true;
    $('#auth').classList.add('hidden'); $('#app').classList.remove('hidden');
    $('#userEmail').textContent = session.user.email || '';
    resetChat();
    loadInteractions();
    loadHome();
  }
  function leaveApp() {
    entered = false; convId = null; marks.clear();
    $('#app').classList.add('hidden'); $('#auth').classList.remove('hidden');
    $('#password').value = '';
  }

  /* ------------------------------------------------------------ movie cards */
  const ACTIONS = [['liked', '\u2665', 'Like'], ['disliked', '\u2715', 'Dislike'], ['watched', '\u2713', 'Watched'], ['saved', '\u2605', 'Save']];

  function refreshMarks() {
    document.querySelectorAll('.act').forEach((b) => b.classList.toggle('on', marks.has(`${b.dataset.id}:${b.dataset.type}`)));
  }
  async function loadInteractions() {
    try { const r = await api('/api/interactions'); r.interactions.forEach((i) => marks.add(`${i.tmdb_id}:${i.interaction_type}`)); refreshMarks(); } catch (_) {}
  }
  async function toggle(m, type, btn) {
    btn.disabled = true;
    try {
      const r = await api('/api/interactions', { method: 'POST', body: JSON.stringify({ tmdb_id: m.id, interaction_type: type, title: m.title, poster_path: m.poster_path }) });
      const key = `${m.id}:${type}`;
      if (r.active) {
        marks.add(key);
        if (type === 'liked') marks.delete(`${m.id}:disliked`);
        if (type === 'disliked') marks.delete(`${m.id}:liked`);
      } else marks.delete(key);
      refreshMarks();
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  }
  function cardEl(m, i) {
    const c = el('article', 'card'); c.style.setProperty('--i', i);
    const p = el('div', 'poster');
    if (m.poster_url) { const img = el('img'); img.src = m.poster_url; img.alt = m.title + ' poster'; img.loading = 'lazy'; p.append(img); }
    else p.append(el('div', 'noposter', 'No poster'));
    p.append(el('span', 'badge', '\u2605 ' + (m.rating ? m.rating : 'n/a')));
    const b = el('div', 'card-body');
    b.append(el('h3', null, m.title));
    b.append(el('div', 'meta', [m.release_date || (m.year ? String(m.year) : 'Unknown date'), m.vote_count ? `${m.vote_count.toLocaleString()} votes` : ''].filter(Boolean).join('  \u00b7  ')));
    if (m.genres && m.genres.length) { const g = el('div', 'chips'); m.genres.slice(0, 3).forEach((x) => g.append(el('span', 'chip', x))); b.append(g); }
    if (m.because) b.append(el('div', 'because', 'Similar to \u201c' + m.because + '\u201d from your list'));
    if (m.overview) b.append(el('p', 'overview', m.overview));
    const a = el('div', 'actions');
    ACTIONS.forEach(([type, icon, label]) => {
      const btn = el('button', 'act', `${icon} ${label}`); btn.dataset.id = m.id; btn.dataset.type = type; btn.title = label;
      btn.onclick = () => toggle(m, type, btn); a.append(btn);
    });
    b.append(a); c.append(p, b); return c;
  }
  function section(root, title, movies, offset = 0) {
    root.append(el('h2', 'section-title', title));
    const g = el('div', 'grid'); movies.forEach((m, i) => g.append(cardEl(m, i + offset))); root.append(g);
  }

  /* ------------------------------------------------------------ results panel (with transitions) */
  async function swap(build) {
    const v = $('#resultsView'); v.classList.add('leaving'); await sleep(280);
    v.replaceChildren(); build(v); refreshMarks();
    $('#resultsPanel').scrollTop = 0; v.classList.remove('leaving');
  }
  function renderHome() {
    return swap((v) => {
      if (!homeData) { v.append(el('div', 'empty', 'Loading movies...')); return; }
      if (homeData.trending.length) section(v, 'Trending now', homeData.trending);
      if (homeData.new.length) section(v, 'New movies', homeData.new);
      if (!homeData.trending.length && !homeData.new.length) v.append(el('div', 'empty', 'No movies cached yet. Run the sync job (see README).'));
    });
  }
  async function loadHome() {
    try { homeData = await api('/api/home', {}, false); } catch (e) { homeData = { trending: [], new: [] }; toast(e.message); }
    if (view.kind === 'home') await renderHome();
  }
  function renderLoading() {
    return swap((v) => {
      const t = el('div', 'thinking'); const d = el('span', 'typing'); d.append(el('i'), el('i'), el('i'));
      t.append(d, el('span', null, 'Finding movies for you...')); v.append(t);
      const g = el('div', 'grid'); for (let i = 0; i < 5; i++) { const s = el('div', 'card skeleton'); s.style.setProperty('--i', i); g.append(s); } v.append(g);
    });
  }
  function renderCurrent() {
    if (view.kind === 'movies') return swap((v) => section(v, view.heading || 'Results', view.movies));
    if (view.kind === 'empty') return swap((v) => v.append(el('div', 'empty', view.message || 'No movies matched.')));
    return renderHome();
  }

  /* ------------------------------------------------------------ chat */
  const box = () => $('#messages');
  function scrollDown() { const m = box(); m.scrollTop = m.scrollHeight; }
  function addMsg(role, text) { const d = el('div', 'msg ' + role, text); box().append(d); scrollDown(); return d; }
  function addTyping() { const d = el('div', 'msg assistant'); const t = el('span', 'typing'); t.append(el('i'), el('i'), el('i')); d.append(t); box().append(d); scrollDown(); return d; }
  function addError(text) {
    const d = el('div', 'msg error', text); const b = el('button', 'btn small', 'Retry');
    b.onclick = () => { d.remove(); send(lastText, true); }; d.append(b); box().append(d); scrollDown();
  }
  function resetChat() {
    convId = null; box().replaceChildren();
    const w = addMsg('assistant', 'Hi! Ask me for movies in plain language. I can recommend, look up a film, show what\u2019s trending, or list a whole series. Tell me what you like or hate and I\u2019ll remember.');
    const s = el('div', 'suggest');
    ['What movies are trending right now?', 'Give me 5 highly rated action movies', 'Movies similar to The Dark Knight', 'All The Godfather movies in order'].forEach((q) => {
      const b = el('button', null, q); b.onclick = () => send(q); s.append(b);
    });
    w.append(s); view = { kind: 'home' }; if (homeData) renderHome();
  }
  function setBusy(v) { busy = v; $('#send').disabled = v; $('#input').disabled = v; if (!v) $('#input').focus({ preventScroll: true }); }

  async function send(text, isRetry = false) {
    text = (text || '').trim(); if (!text || busy) return;
    lastText = text; setBusy(true);
    if (!isRetry) addMsg('user', text);
    $('#input').value = ''; autoGrow();
    const typing = addTyping(); renderLoading();
    try {
      const r = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message: text, conversation_id: convId }) });
      convId = r.conversation_id; typing.remove(); addMsg('assistant', r.reply);
      if (r.movies && r.movies.length) { view = { kind: 'movies', movies: r.movies, heading: r.heading }; if (isMobile()) $('#pip').classList.remove('hidden'); }
      else if (r.tool && r.notice) view = { kind: 'empty', message: r.notice };
      await renderCurrent();
      if (r.timings) console.debug('timings', r.timings);
    } catch (e) {
      typing.remove(); addError(e.message || 'Something went wrong.'); await renderCurrent();
      if (e.status === 401) toast('Session expired - please log in again.');
    }
    setBusy(false);
  }
  function autoGrow() { const t = $('#input'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 140) + 'px'; }

  async function toggleHistory() {
    const h = $('#history');
    if (!h.classList.contains('hidden')) { h.classList.add('hidden'); return; }
    h.replaceChildren(el('div', 'muted small', 'Loading...')); h.classList.remove('hidden');
    try {
      const r = await api('/api/conversations'); h.replaceChildren();
      if (!r.conversations.length) h.append(el('div', 'muted small', 'No conversations yet.'));
      r.conversations.forEach((c) => { const b = el('button', null, c.title || 'Untitled'); b.onclick = () => openConversation(c.id); h.append(b); });
    } catch (e) { h.replaceChildren(el('div', 'muted small', e.message)); }
  }
  async function openConversation(id) {
    $('#history').classList.add('hidden');
    try {
      const r = await api('/api/conversations/' + id);
      convId = id; box().replaceChildren(); let last = null;
      r.messages.forEach((m) => { addMsg(m.role, m.content); const mv = (m.metadata || {}).movies; if (m.role === 'assistant' && mv && mv.length) last = { movies: mv, heading: m.metadata.heading }; });
      view = last ? { kind: 'movies', ...last } : { kind: 'home' }; await renderCurrent();
    } catch (e) { toast(e.message); }
  }
  async function showForYou() {
    if (isMobile()) {
      $('#app').dataset.tab = 'results';
      document.querySelectorAll('[data-tab-btn]').forEach((x) => x.classList.toggle('on', x.dataset.tabBtn === 'results'));
      $('#pip').classList.add('hidden');
    }
    await renderLoading();
    try {
      const r = await api('/api/for-you');
      view = r.movies.length ? { kind: 'movies', movies: r.movies, heading: 'For You' }
                             : { kind: 'empty', message: r.message };
    } catch (e) { view = { kind: 'empty', message: e.message }; }
    await renderCurrent();
  }
  /* ------------------------------------------------------------ boot */
  async function boot() {
    let cfg = {};
    try { cfg = await (await fetch('/api/config')).json(); } catch (_) {}
    if (!cfg.supabase_url || !cfg.supabase_anon_key) { $('#authMsg').textContent = 'Server is missing the Supabase settings (.env).'; return; }
    sb = window.supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key);

    $('#authForm').addEventListener('submit', submitAuth);
    $('#authToggle').onclick = () => setAuthMode(authMode === 'login' ? 'signup' : 'login');
    $('#logout').onclick = () => sb.auth.signOut();
    $('#navForYou').onclick = showForYou;
    $('#navHome').onclick = () => { view = { kind: 'home' }; renderCurrent(); };
    $('#send').onclick = () => send($('#input').value);
    $('#newChat').onclick = () => { $('#history').classList.add('hidden'); resetChat(); };
    $('#historyBtn').onclick = toggleHistory;
    $('#input').addEventListener('input', autoGrow);
    $('#input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($('#input').value); } });
    document.querySelectorAll('[data-tab-btn]').forEach((b) => b.onclick = () => {
      $('#app').dataset.tab = b.dataset.tabBtn;
      document.querySelectorAll('[data-tab-btn]').forEach((x) => x.classList.toggle('on', x === b));
      if (b.dataset.tabBtn === 'results') $('#pip').classList.add('hidden'); else scrollDown();
    });

    sb.auth.onAuthStateChange((_evt, session) => { if (session) enterApp(session); else if (entered) leaveApp(); });
    const { data } = await sb.auth.getSession();
    if (data.session) enterApp(data.session);
  }
  boot();
})();
