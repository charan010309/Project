/* Saved mobile history: account APIs are the only source of conversation data. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const node = (tag, text, cls = '') => { const e = document.createElement(tag); e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  let items = [], filter = 'all', sort = 'newest', managing = false, selected = new Set();
  let preview = null, previewData = null, version = 0, pendingDelete = [], deleting = false, modal = null, focusBack = null;
  const key = item => item.type + ':' + item.id;
  const endpoint = item => '/api/' + item.type + '/' + encodeURIComponent(item.id);
  const resume = item => '/m/' + (item.type === 'live' ? 'speakwithai' : 'home') + '?id=' + encodeURIComponent(item.id);
  const timestamp = item => { const value = item.updated_at || item.created_at; return value ? Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z') || 0 : 0; };
  const date = item => timestamp(item) ? new Date(timestamp(item)).toLocaleString([], {dateStyle:'medium', timeStyle:'short'}) : 'Date unavailable';
  function status(message = '') { $('history-status').textContent = message; }
  async function api(path, method = 'GET', data) {
    const response = await fetch(path, {method, credentials:'same-origin', headers:data ? {'Content-Type':'application/json'} : {}, body:data ? JSON.stringify(data) : undefined});
    if (response.status === 401) { location.assign('/m/login'); throw Error('Please sign in again.'); }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(typeof body.detail === 'string' ? body.detail : 'Request failed. Please try again.');
    return body;
  }
  const overlayIds = ['maximus-drawer','preview-sheet','sort-popover','delete-modal'];
  function show(id) {
    if (modal) close();
    focusBack = document.activeElement; modal = id;
    const box = $(id); box.inert = false;
    box.setAttribute('role','dialog'); box.setAttribute('aria-modal','true');
    box.setAttribute('aria-label', {'maximus-drawer':'Navigation','preview-sheet':'Conversation preview','sort-popover':'Sort conversations','delete-modal':'Confirm deletion'}[id]);
    box.classList.remove('hidden','-translate-x-full','translate-y-full'); box.classList.add('flex');
    const backdrop = $(id === 'maximus-drawer' ? 'maximus-drawer-backdrop' : id === 'preview-sheet' ? 'preview-sheet-backdrop' : '');
    backdrop?.classList.remove('opacity-0','pointer-events-none');
    document.querySelector('main').inert = false; // Sheets are nested inside main in the supplied design.
    document.querySelector('header').inert = true;
    for (const child of document.querySelector('main > div').children) if (!child.contains(box) && child !== box && child !== backdrop) child.inert = true;
    document.body.style.overflow = 'hidden';
    box.querySelector('button, a, input')?.focus();
    $('maximus-drawer-open').setAttribute('aria-expanded', String(id === 'maximus-drawer'));
  }
  function close() {
    if (!modal || deleting) return;
    const id = modal, box = $(id); box.inert = true; box.removeAttribute('aria-modal');
    if (id === 'maximus-drawer') box.classList.add('-translate-x-full');
    else if (id === 'preview-sheet') { box.classList.add('translate-y-full'); version++; }
    else { box.classList.add('hidden'); box.classList.remove('flex'); }
    for (const name of ['maximus-drawer-backdrop','preview-sheet-backdrop']) $(name).classList.add('opacity-0','pointer-events-none');
    document.querySelector('header').inert = false;
    for (const child of document.querySelector('main > div').children) child.inert = overlayIds.includes(child.id);
    document.body.style.overflow = ''; modal = null;
    $('maximus-drawer-open').setAttribute('aria-expanded','false');
    if (focusBack?.isConnected) focusBack.focus(); else $('history-search').focus();
  }
  function sorted(list) { return [...list].sort((a,b) => sort === 'messages' ? b.message_count-a.message_count || timestamp(b)-timestamp(a) : (timestamp(b)-timestamp(a)) * (sort === 'oldest' ? -1 : 1)); }
  function updateSelection() { $('selection-count').textContent = selected.size + ' selected'; $('delete-selected').disabled = !selected.size; }
  function render() {
    document.querySelectorAll('.session-card').forEach(e => e.remove());
    const query = $('history-search').value.trim().toLowerCase();
    const visible = sorted(items.filter(i => (filter === 'all' || (filter === 'pinned' ? i.pinned : i.type === filter)) && i.title.toLowerCase().includes(query)));
    $('stored-count').textContent = items.length + ' Stored Sessions';
    for (const button of document.querySelectorAll('[data-filter]')) {
      const type = button.dataset.filter, active = type === filter;
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('bg-primary',active); button.classList.toggle('text-on-primary',active);
      button.classList.toggle('bg-surface-container',!active); button.classList.toggle('text-on-surface-variant',!active);
      const count = button.querySelector('[data-count]');
      if (count) count.textContent = items.filter(i => type === 'all' || (type === 'pinned' ? i.pinned : i.type === type)).length;
    }
    for (const item of visible) {
      const card = node('article',undefined,'session-card relative bg-surface-container-lowest rounded-xl p-space-md shadow-sm');
      card.dataset.key = key(item);
      const top = node('div',undefined,'flex items-start justify-between gap-space-sm mb-space-xs');
      const info = node('div',undefined,'min-w-0');
      info.append(node('span',item.type === 'live' ? 'LIVE AI' : 'CHAT','history-badge'),node('span',date(item),'block font-code-block text-code-block text-on-surface-variant'));
      const actions = node('div',undefined,'flex shrink-0');
      if (managing) { const check = node('input'); check.type = 'checkbox'; check.checked = selected.has(key(item)); check.setAttribute('aria-label','Select ' + item.title); check.onchange = () => { check.checked ? selected.add(key(item)) : selected.delete(key(item)); updateSelection(); }; actions.append(check); }
      const pin = node('button',undefined,'pin-toggle history-icon'); pin.setAttribute('aria-label',(item.pinned ? 'Unpin ' : 'Pin ') + item.title); pin.setAttribute('aria-pressed',String(item.pinned)); pin.append(node('span','push_pin','material-symbols-outlined')); pin.onclick = () => togglePin(item,pin);
      const del = node('button',undefined,'delete-trigger history-icon'); del.setAttribute('aria-label','Delete ' + item.title); del.append(node('span','delete','material-symbols-outlined')); del.onclick = () => confirmDelete([item]);
      actions.append(pin,del); top.append(info,actions);
      const inspect = node('button',undefined,'history-inspect'); inspect.append(node('h3',item.title,'font-headline-sm text-headline-sm font-semibold'),node('p','Open saved conversation','text-on-surface-variant text-body-sm mt-1.5'));
      inspect.onclick = () => openPreview(item);
      const footer = node('div',undefined,'flex items-center justify-between mt-space-sm bg-surface-container-low rounded-lg p-space-sm');
      footer.append(node('span',item.message_count + ' messages','font-code-block text-code-block text-on-surface-variant'),node('span','Review →','text-secondary text-label-sm'));
      inspect.append(footer); card.append(top,inspect); $('history-items-container').insertBefore(card,$('empty-state'));
    }
    $('empty-state').classList.toggle('hidden',visible.length > 0); $('empty-state').classList.toggle('flex',!visible.length);
    for (const [id,list] of [['history-pinned',items.filter(i=>i.pinned)],['history-recent',[...items].sort((a,b)=>timestamp(b)-timestamp(a)).slice(0,8)]]) {
      $(id).replaceChildren();
      if (!list.length) $(id).append(node('p','No sessions yet','text-on-surface-variant text-body-sm'));
      for (const item of list) { const a = node('a',item.title,'block truncate py-2 text-body-sm'); a.href = resume(item); $(id).append(a); }
    }
    updateSelection();
  }
  async function load() {
    status('Loading saved conversations…'); $('retry-history').classList.add('hidden');
    try {
      // Sequential list requests avoid competing session-cookie updates.
      const chats = await api('/api/chat/list'), live = await api('/api/live/list');
      items = [...chats.map(i=>({...i,type:'chat'})),...live.map(i=>({...i,type:'live'}))];
      selected = new Set([...selected].filter(k=>items.some(i=>key(i)===k))); render(); status(); $('sync-status').textContent = '• Loaded';
    } catch(e) { status(e.message); $('sync-status').textContent = '• Unavailable'; $('retry-history').classList.remove('hidden'); }
  }
  async function togglePin(item,button) {
    button.disabled = true;
    try { await api(endpoint(item) + (item.pinned ? '/unpin' : '/pin'),'POST'); item.pinned = !item.pinned; render(); status(item.pinned ? 'Conversation pinned.' : 'Conversation unpinned.'); }
    catch(e) { status(e.message); button.disabled = false; }
  }
  async function openPreview(item) {
    preview = item; previewData = null; show('preview-sheet'); const token = ++version;
    $('sheet-title').textContent = item.title; $('sheet-timestamp').textContent = date(item); $('sheet-badge').textContent = item.type === 'live' ? 'LIVE AI' : 'CHAT';
    $('open-btn-label').textContent = item.type === 'live' ? 'Resume in Live AI' : 'Resume in Chat';
    $('export-session').disabled = true; $('open-full-conversation').disabled = true;
    $('preview-messages').replaceChildren(node('p','Loading conversation…'));
    try {
      const data = await api(endpoint(item)); if (token !== version) return;
      previewData = data; $('preview-messages').replaceChildren();
      for (const message of data.messages) { const row = node('article',undefined,'history-message ' + (message.role === 'user' ? 'history-user' : 'history-assistant')); row.append(node('div',message.role === 'user' ? 'YOU' : 'MAXIMUS','font-label-caps text-label-caps'),node('p',message.content)); $('preview-messages').append(row); }
      if (!data.messages.length) $('preview-messages').append(node('p','No messages in this session.'));
      $('export-session').disabled = false; $('open-full-conversation').disabled = false;
    } catch(e) { if(token === version) { const retry = node('button','Try again','history-retry'); retry.onclick = ()=>openPreview(item); $('preview-messages').replaceChildren(node('p',e.message),retry); } }
  }
  function download(data,name) { const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})); const a = node('a'); a.href = url; a.download = name; document.body.append(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url),30000); }
  function confirmDelete(list) { if (!list.length) return; pendingDelete = [...list]; show('delete-modal'); $('confirm-delete').textContent = 'Delete ' + list.length + (list.length === 1 ? ' conversation' : ' conversations'); $('cancel-delete').focus(); }
  $('confirm-delete').onclick = async () => {
    if(deleting) return; deleting = true; $('confirm-delete').disabled = true; $('cancel-delete').disabled = true;
    let error = '', removed = 0;
    for (const item of pendingDelete) { try { await api(endpoint(item),'DELETE'); items = items.filter(i=>key(i)!==key(item)); selected.delete(key(item)); removed++; } catch(e) { error = e.message; break; } }
    deleting = false; $('confirm-delete').disabled = false; $('cancel-delete').disabled = false; close(); render(); status((removed ? removed + ' conversation(s) deleted. ' : '') + error);
  };
  for (const id of overlayIds) $(id).inert = true;
  $('maximus-drawer-open').onclick = ()=>show('maximus-drawer');
  for (const id of ['maximus-drawer-close','maximus-drawer-backdrop','close-preview-sheet','preview-sheet-backdrop','sheet-handle','close-sort-btn','cancel-delete']) $(id).onclick = close;
  $('open-sort-menu').onclick = ()=>show('sort-popover');
  for (const button of document.querySelectorAll('.sort-option')) button.onclick = () => { sort=button.dataset.sort; $('current-sort-label').textContent=button.firstElementChild.textContent; document.querySelectorAll('.sort-option').forEach(b=>{b.setAttribute('aria-pressed',String(b===button)); b.lastElementChild.classList.toggle('opacity-0',b!==button);}); close(); render(); };
  for (const button of document.querySelectorAll('[data-filter]')) button.onclick = async () => {
    if(button.dataset.filter !== 'export') { filter=button.dataset.filter; render(); return; }
    button.disabled=true;
    try { const archive=[]; for(const item of items) archive.push({...await api(endpoint(item)),type:item.type,pinned:item.pinned}); download({exported_at:new Date().toISOString(),conversations:archive},'maximus-history.json'); status('Archive exported.'); } catch(e) { status('Export failed: '+e.message); } finally {button.disabled=false;}
  };
  $('history-search').oninput=render;
  $('reset-filters').onclick=()=>{filter='all';$('history-search').value='';render();};
  $('retry-history').onclick=load;
  $('batch-clear-btn').setAttribute('aria-label','Manage archive');
  $('batch-clear-btn').onclick=()=>{managing=!managing;selected.clear();$('manage-tools').classList.toggle('hidden',!managing);$('batch-clear-btn').setAttribute('aria-pressed',String(managing));render();};
  $('delete-selected').onclick=()=>confirmDelete(items.filter(i=>selected.has(key(i))));
  $('export-session').onclick=()=>{if(previewData)download({...previewData,type:preview.type},'maximus-'+preview.type+'-'+preview.id+'.json');};
  $('open-full-conversation').onclick=()=>{if(previewData)location.assign(resume(preview));};
  document.querySelector('[aria-label="Account Settings"]').onclick=()=>location.assign('/m/profile-settings');
  const avatar=document.querySelector('header .rounded-full.bg-primary'); avatar.setAttribute('role','link'); avatar.tabIndex=0; avatar.setAttribute('aria-label','Profile settings'); avatar.onclick=()=>location.assign('/m/profile-settings'); avatar.onkeydown=e=>{if(e.key==='Enter')avatar.click();};
  for (const button of document.querySelectorAll('button')) if (['New Session','New Conversation'].some(t=>button.textContent.trim().endsWith(t))) button.onclick=async()=>{button.disabled=true;try{const chat=await api('/api/chat/new','POST',{title:'New Chat',mode:'chat'});location.assign('/m/home?id='+chat.id);}catch(e){close();status(e.message);button.disabled=false;}};
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){close();return;}
    if(modal && e.key==='Tab'){const controls=[...$(modal).querySelectorAll('button:not(:disabled),a[href],input:not(:disabled)')].filter(el=>el.getClientRects().length);if(!controls.length)return;const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}return;}
    if(!modal && (e.key==='/' || ((e.metaKey||e.ctrlKey)&&e.key==='k')) && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();$('history-search').focus();}
  });
  async function start(){await load();try{const profile=await api('/api/user/profile');$('account-name').textContent=profile.name||'Account';$('account-email').textContent=profile.email||'Signed in';}catch(e){status(e.message);}}
  start();
})();
