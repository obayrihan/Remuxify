'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const bytes = n => { if (n == null) return '—'; const a = Math.abs(n); return (n < 0 ? '−' : '') + (a >= 1024**3 ? (a/1024**3).toFixed(1)+' GiB' : a >= 1024**2 ? (a/1024**2).toFixed(0)+' MiB' : (a/1024).toFixed(0)+' KiB'); };
const duration = n => { if (n == null || !Number.isFinite(n)) return 'Calculating…'; if(n < 60) return Math.ceil(n)+'s'; if(n < 3600) return Math.ceil(n/60)+'m'; return Math.floor(n/3600)+'h '+Math.ceil(n%3600/60)+'m'; };
const formatPercent = value => typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 100 ? 'N/A' : value > 0 && value < 0.1 ? '<0.1%' : value.toFixed(1)+'%';
const activeStates = ['queued','waiting','encoding','validating','committing'];
let currentPage = 'overview', state = null, jobs = [], mediaItems = [], selected = new Set(), offset = 0, total = 0;
let signedIn = false, polling = false, settingsLoaded = false, rulesLoaded = false, lastMedia = 0, toastTimer, mediaRequest = 0;
const pluginHost = window.parent !== window && window.parent.RemuxifyHost;

async function api(path, options = {}) {
  if (pluginHost) {
    try { return await pluginHost.request(path, options); }
    catch (error) {
      if (error.status === 401 || error.status === 403) showLogin();
      const detail = typeof error.json === 'function' ? await error.json().catch(()=>({})) : {};
      throw new Error(detail.detail || error.message || 'Jellyfin plugin request failed. Check the worker connection.');
    }
  }
  const response = await fetch('/forge/api/' + path, {credentials:'same-origin', ...options, headers:{'Content-Type':'application/json','X-Forge-Request':'1',...options.headers}});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') showLogin();
    throw new Error(typeof result.detail === 'string' ? result.detail : `Request failed (${response.status})`);
  }
  return result;
}
function toast(message, error = false) {
  const el = $('#toast'); el.textContent = message; el.hidden = false; el.classList.toggle('danger',error);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.hidden = true, error ? 12000 : 5500);
}
function showLogin() { signedIn = false; $('#details-dialog').close(); selected.clear(); $('#shell').hidden = true; $('#login-screen').hidden = !!pluginHost; if(pluginHost)pluginHost.expired(); }
function showDashboard(name) {
  signedIn = true; $('#shell').hidden = false; $('#login-screen').hidden = true; $('#user-name').textContent = name;
  if (pluginHost) { $('#logout').hidden = true; $('#jellyfin-link').href=pluginHost.home(); $('#jellyfin-link').target='_top'; navigate(); poll(); return; }
  $('#jellyfin-link').href = location.port === '5056' ? 'http://localhost:8096/web/' : '/web/';
  navigate(); poll();
}
function navigate() {
  const page = location.hash.slice(1) || 'overview';
  currentPage = ['overview','library','queue','rules','settings'].includes(page) ? page : 'overview';
  $$('.view').forEach(v => v.hidden = v.id !== currentPage);
  $$('[data-page]').forEach(a => a.classList.toggle('active',a.dataset.page === currentPage));
  $('#page-label').textContent = currentPage.toUpperCase();
  if (!signedIn) return;
  if (currentPage === 'library') loadMedia().catch(e=>toast(e.message,true));
  if (currentPage === 'rules' && !rulesLoaded) loadRules().catch(e=>toast(e.message,true));
  if (currentPage === 'settings' && !settingsLoaded) loadSettings().catch(e=>toast(e.message,true));
}

function renderState(s) {
  state = s;
  const c = s.counts, r = s.resources;
  for(const key of ['indexed','flagged','queued','completed']) $('#stat-'+key).textContent = c[key].toLocaleString();
  $('#nav-flagged').textContent = c.flagged; $('#nav-queued').textContent = c.queued;
  $('#stat-saved').textContent = c.completed ? (c.bytes_saved >= 0 ? bytes(c.bytes_saved)+' reclaimed' : bytes(-c.bytes_saved)+' additional storage used') : 'Originals protected until verified';
  $('#scan-status').textContent = s.inventory.scanning ? 'Scanning library folders…' : s.inventory.last_scan ? 'Updated '+new Date(s.inventory.last_scan*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})+(s.inventory.pending ? ' · '+s.inventory.pending+' settling' : '') : 'Discovering library folders…';
  $('#queue-mode').textContent = s.queue_paused ? 'Queue paused' : s.auto_queue ? 'Automatic matching enabled' : 'Manual selection · one job at a time';
  $$('.pause-toggle').forEach(b=> b.textContent = s.queue_paused ? 'Resume queue' : 'Pause queue');
  $('#engine-label').textContent = !s.encoder.tested ? 'Testing GPU…' : s.encoder.available ? (s.encoder.nvdec_available ? 'NVDEC + NVENC ready' : 'NVENC ready · CPU decode') : 'NVENC unavailable';
  $('#engine-dot').classList.toggle('off',!s.encoder.available);
  $('#folder-count').textContent = s.roots.length+' FOLDERS';
  const notices = [];
  if(s.encoder.tested && !s.encoder.available) notices.push('NVENC is unavailable. '+(s.encoder.error || 'Run the encoder test in Settings.'));
  if(s.encoder.available && s.encoder.decoder_error) notices.push('NVDEC test failed; Auto uses CPU decoding. '+s.encoder.decoder_error);
  if(s.inventory.error) notices.push('Inventory: '+s.inventory.error);
  if(c.failed) notices.push(c.failed+' job(s) need attention. Open Queue & history for failure details.');
  if(c.unreadable) notices.push(c.unreadable+' indexed file(s) could not be probed. They cannot be queued until readable.');
  if(s.queue_paused) notices.push('Queue is paused. Running work finishes safely; waiting jobs will not start.');
  if(r.playback_error) notices.push(r.playback_error);
  $('#notices').innerHTML = notices.map(n=>'<div class="notice">'+esc(n)+'</div>').join('');
  const sampleAge=Math.max(0,(s.now||Date.now()/1000)-(r.sampled||0));
  $('#resource-freshness').textContent=!r.ready?'SAMPLING':sampleAge>20?'STALE / '+Math.floor(sampleAge)+'s':'LIVE / ~5s';
  $('#resource-freshness').className='badge'+(r.ready&&sampleAge>20?' danger':'');
  const busiest=Object.entries(r.disk_activity_by_device||{}).sort((a,b)=>b[1]-a[1])[0];
  $('#disk-stats-note').textContent=!r.ready?'Waiting for disk counters.':r.disk_busy==null?'Disk active time unavailable; throughput is shown separately.':r.disk_estimated?'Disk activity is estimated; throughput is shown separately.':'Disk shows active time on the busiest physical disk'+(busiest?' ('+busiest[0]+')':'')+'.';
  const gauge=(label,value,right)=>`<div class="resource-row"><span class="muted">${esc(label)}</span><progress max="100" value="${Number(value)||0}" aria-label="${esc(label)}" aria-valuetext="${esc(right)}"></progress><span class="mono">${esc(right)}</span></div>`;
  $('#resources').innerHTML = gauge('CPU',r.cpu,formatPercent(r.cpu)) + gauge('GPU',r.gpu?.utilization,r.gpu ? r.gpu.utilization.toFixed(0)+'%' : 'N/A') + gauge('NVENC',r.gpu?.encoder,r.gpu ? r.gpu.encoder.toFixed(0)+'%' : 'N/A') + gauge('NVDEC',r.gpu?.decoder,r.gpu?.decoder == null ? 'N/A' : r.gpu.decoder.toFixed(0)+'%') + gauge(r.disk_estimated?'Disk ≈':'Disk',r.disk_busy,formatPercent(r.disk_busy)) + `<div class="resource-sub">${esc(r.gpu?.name || 'Waiting for NVIDIA telemetry')}<br>${(r.ram_free_gb||0).toFixed(1)} GiB RAM free · ${r.gpu ? (r.gpu.free_mb/1024).toFixed(1)+' GiB VRAM free' : 'VRAM unavailable'} · ${(r.disk_mbps||0).toFixed(1)} MB/s disk I/O</div>`;
  $('#storage').innerHTML = r.disks.length ? r.disks.map(d=>`<div class="storage-row"><div><strong>${esc(d.path)}</strong><span class="muted">${d.online ? bytes(d.free)+' free / '+bytes(d.total) : 'OFFLINE'}</span></div><progress max="100" value="${d.total ? (1-d.free/d.total)*100 : 0}" aria-label="${esc(d.path)} storage used"></progress></div>`).join('') : '<p class="small muted">Discovering library drives…</p>';
  $('#watched-folders').innerHTML = s.roots.map(r=>`<div class="folder-row"><div><small>${esc(r.library)}</small>${esc(r.path)}</div><span class="badge ${r.online ? 'good':'danger'}">${r.online ? 'WATCHED':'OFFLINE'}</span></div>`).join('');
  $('#auto-state').textContent = s.auto_queue ? 'AUTO QUEUE ON' : 'AUTO QUEUE OFF';
  const libraryFilter = $('#library-filter'), existing = libraryFilter.value;
  const libraries = [...new Set(s.roots.map(r=>r.library))].sort();
  if(JSON.stringify(libraries) !== libraryFilter.dataset.libraries){
    libraryFilter.innerHTML='<option value="">All libraries</option>'+libraries.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
    libraryFilter.value = existing; libraryFilter.dataset.libraries=JSON.stringify(libraries);
  }
}

function jobCard(j) {
  const live = ['encoding','validating','committing'].includes(j.state), failed = j.state === 'failed';
  const color = j.state === 'completed' ? 'good' : failed ? 'danger' : activeStates.includes(j.state) ? 'warning' : '';
  const canCancel = ['queued','waiting','encoding','validating'].includes(j.state);
  return `<article class="job-card ${live ? 'live-job':''}"><div class="job-top"><div><div class="job-title">${esc(j.name)}</div><div class="job-sub">${esc(j.options.output_container.toUpperCase())} / HEVC ${esc(j.options.output_bit_depth)}-BIT / ${j.options.audio_mode === 'copy' ? 'AUDIO COPY':'AAC'} · ATTEMPT ${j.attempts} · ${new Date(j.created*1000).toLocaleString()}</div></div><div class="job-actions"><span class="badge ${color}">${esc(j.state.toUpperCase())}</span>${canCancel ? `<button class="secondary small-button job-action" data-job="${j.id}" data-action="cancel">Cancel</button>` : ''}${['failed','cancelled'].includes(j.state) ? `<button class="secondary small-button job-action" data-job="${j.id}" data-action="retry">Retry</button>`:''}<button class="text-button job-log" data-job="${j.id}">Log ↗</button></div></div>${activeStates.includes(j.state) ? `<div class="job-progress"><div class="progress-label"><span>${esc(j.phase || 'Waiting')}</span><span class="mono">${live ? (j.progress||0).toFixed(1)+'%' : 'WAITING'}</span></div><progress max="100" value="${live ? j.progress : 0}" aria-label="Job progress"></progress>${live ? `<div class="job-numbers"><span><b>${(j.fps||0).toFixed(1)}</b> FPS</span><span><b>${(j.speed||0).toFixed(2)}×</b> SPEED</span><span><b>${duration(j.eta)}</b> PHASE REMAINING</span></div>`:''}</div>` : ''}${j.error ? `<div class="job-error">${esc(j.error)}</div>` : ''}${j.state === 'completed' ? `<div class="job-receipt">✓ Stream checks · full decode · SHA-256 verified · ${bytes(j.output_size)} output</div><div class="job-sub">${esc(j.target)}</div>` : ''}</article>`;
}
function renderJobs(items) {
  jobs = items;
  const running = items.find(j=>['encoding','validating','committing'].includes(j.state)) || items.find(j=>['queued','waiting'].includes(j.state));
  $('#active-job').innerHTML = running ? jobCard(running) : '<div class="empty-worker"><div class="empty-icon">✓</div><div><h3>Ready when you are.</h3><p>No replacements are running. Review your library to start a job.</p></div><a href="#library" class="text-link">Review library ↗</a></div>';
  $('#queue-jobs').innerHTML = items.length ? items.map(jobCard).join('') : '<div class="empty-worker"><div class="empty-icon">⇄</div><div><h3>Your queue is clear.</h3><p>Queued jobs and validation history will appear here.</p></div></div>';
}
function renderEvents(items) {
  $('#events').innerHTML = items.length ? items.slice(0,6).map(e=>`<div class="event-row"><span class="dot ${e.level==='error' ? 'off':''}"></span><span>${esc(e.message)}</span><time>${new Date(e.time*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time></div>`).join('') : '<p class="muted small">No events yet.</p>';
}

async function poll() {
  if(!signedIn || polling) return;
  polling = true;
  try {
    const [s,j,e] = await Promise.all([api('state'),api('jobs'),api('events')]);
    renderState(s); renderJobs(j); renderEvents(e);
    $('#connection').textContent='● Live'; $('#connection').className='badge good';
    if(currentPage==='library' && Date.now()-lastMedia>15000) await loadMedia();
  } catch(e) {
    $('#connection').textContent='● Reconnecting'; $('#connection').className='badge danger';
    $('#engine-label').textContent=e.message;
  } finally { polling=false; }
}

async function loadMedia() {
  const request = ++mediaRequest;
  const params = new URLSearchParams({q:$('#search').value,library:$('#library-filter').value,flagged:$('#flagged-only').checked,offset,limit:50});
  const result = await api('media?'+params);
  if(request !== mediaRequest) return;
  mediaItems = result.items; total = result.total; lastMedia=Date.now();
  $('#media-body').innerHTML=mediaItems.length ? mediaItems.map(m=>{
    const i = m.info, busy = m.job && activeStates.includes(m.job.state), blocked = m.blocked || m.error;
    return `<tr><td><input type="checkbox" class="media-check" data-id="${m.id}" aria-label="Select ${esc(m.name)}" ${selected.has(m.id)?'checked':''} ${blocked || busy ? 'disabled':''}></td><td class="media-name"><strong>${esc(m.name)}</strong><small>${esc(m.library)}${busy ? ' · '+esc(m.job.state) : ''}</small></td><td class="media-codec">${i ? esc(i.codec.toUpperCase()+' / '+i.bit_depth+'-BIT')+'<br><small>'+esc(i.profile+' · '+i.container.toUpperCase()+' · '+i.codec_tag)+'</small>' : '<span class="danger">Unreadable</span>'}</td><td class="mono nowrap">${bytes(m.size)}</td><td>${blocked ? `<span class="badge danger" title="${esc(blocked)}">Profile blocked</span>` : m.rules.length ? '<span class="badge warning">Rule match</span>' : '<span class="badge">No rule match</span>'}</td><td><button class="secondary small-button media-details" data-id="${m.id}">Inspect ↗</button></td></tr>`;
  }).join('') : '<tr><td colspan="6" class="empty-table">'+(state?.inventory.scanning ? 'Scanning your libraries. Results appear as files are indexed.' : $('#flagged-only').checked ? 'No files match the current rules and filters. Disable “Rule matches only” to browse all media.' : 'No media matches these filters.')+'</td></tr>';
  $('#media-total').textContent=total ? `${offset+1}–${Math.min(offset+50,total)} of ${total.toLocaleString()} files` : '0 files';
  $('#previous').disabled=offset===0; $('#next').disabled=offset+50>=total;
  updateSelection();
}
function updateSelection(){
  $('#selected-count').textContent=selected.size;
  $('#queue-selected').disabled=selected.size===0;
  const available=$$('.media-check:not(:disabled)');
  $('#select-all').checked=available.length>0 && available.every(e=>e.checked);
}
async function queueMedia(ids){
  const result={results:[]};
  for(let first=0; first<ids.length; first+=200){
    const batch=await api('jobs',{method:'POST',body:JSON.stringify({media_ids:ids.slice(first,first+200)})});
    result.results.push(...batch.results);
  }
  const errors=result.results.filter(r=>r.error), success=result.results.length-errors.length;
  toast(`${success} replacement(s) queued.`+(errors.length ? ' '+errors.map(r=>r.error).join(' · '):''),errors.length>0);
  for(const item of result.results) if(!item.error) selected.delete(item.media_id);
  await loadMedia(); await poll();
}
async function inspect(id){
  const m=await api('media/'+id), i=m.info;
  $('#details-content').innerHTML=`<h3 class="details-name">${esc(m.path.split(/[\\/]/).pop())}</h3><div class="details-path">${esc(m.path)}</div>${i ? `<div class="detail-grid"><div><small>VIDEO</small>${esc(i.codec.toUpperCase()+' / '+i.profile)}</div><div><small>PIXEL FORMAT</small>${esc(i.pixel_format)}</div><div><small>CONTAINER / TAG</small>${esc(i.container.toUpperCase()+' / '+i.codec_tag)}</div><div><small>RESOLUTION</small>${i.width} × ${i.height}</div><div><small>DURATION</small>${duration(i.duration)}</div><div><small>AUDIO / SUBTITLES</small>${esc(i.audio.join(', '))} / ${i.subtitles}</div></div>` : `<p class="error">${esc(m.error)}</p>`}<div class="info-strip">Replacement uses the selected NVENC preset, followed by stream and duration checks, a full video/audio decode, and a verified output hash. The original stays in place until all checks pass.</div><button class="primary detail-queue" data-id="${m.id}" ${m.error ? 'disabled':''}>Queue replacement ↗</button><details><summary class="small muted">Full FFprobe metadata</summary><pre>${esc(JSON.stringify(m.probe,null,2))}</pre></details>`;
  $('#details-dialog').showModal();
}
async function loadRules(){
  const c=await api('settings'); $('#rules-json').value=JSON.stringify(c.rules,null,2); rulesLoaded=true;
  $('#rules-list').innerHTML=c.rules.length ? c.rules.map(r=>`<article class="rule-card"><div><h3>${esc(r.name)}</h3><span class="badge ${r.enabled ? 'good':''}">${r.enabled ? 'ENABLED':'DISABLED'}</span></div><section class="rule-conditions">${Object.entries(r.conditions).map(([k,v])=>`<span class="badge">${esc(k)} = ${esc(Array.isArray(v) ? v.join(' | ') : v)}</span>`).join('')}</section></article>`).join('') : '<div class="notice">No detection rules. Media can still be selected manually.</div>';
}
async function loadSettings(){
  const c=await api('settings');
  for(const el of $('#settings-form').elements){if(!el.name || !(el.name in c)) continue; if(el.type==='checkbox')el.checked=c[el.name];else el.value=c[el.name];}
  settingsLoaded=true;
  await loadTorrentSettings();
}

async function loadTorrentSettings(){
  const c=await api('qbittorrent'), form=$('#torrent-form');
  form.elements.port.value=c.port;form.elements.enabled.checked=c.enabled;
  $('#torrent-status').textContent=c.enabled?'ENABLED':c.configured?'SAVED / DISABLED':'NOT CONNECTED';
}

$('#login-form').addEventListener('submit',async e=>{
  e.preventDefault(); const button=e.target.querySelector('button');button.disabled=true;$('#login-error').textContent='';
  const fields=new FormData(e.target);
  try{const result=await api('login',{method:'POST',body:JSON.stringify({username:fields.get('username'),password:fields.get('password')})});e.target.reset();showDashboard(result.name);}catch(err){$('#login-error').textContent=err.message;}finally{button.disabled=false;}
});
$('#logout').addEventListener('click',async()=>{try{await api('logout',{method:'POST'});showLogin();}catch(e){toast(e.message,true);}});
window.addEventListener('hashchange',navigate);
$('#rescan').addEventListener('click',async()=>{try{await api('scan',{method:'POST'});toast('Library scan requested. New files appear as they settle.');await poll();}catch(e){toast(e.message,true);}});
$$('.pause-toggle').forEach(b=>b.addEventListener('click',async()=>{try{await api('settings',{method:'PUT',body:JSON.stringify({queue_paused:!state.queue_paused})});await poll();}catch(e){toast(e.message,true);}}));
let searchTimer;
$('#search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{offset=0;loadMedia().catch(e=>toast(e.message,true));},350);});
for(const s of ['#library-filter','#flagged-only']) $(s).addEventListener('change',()=>{offset=0;selected.clear();loadMedia().catch(e=>toast(e.message,true));});
$('#previous').addEventListener('click',()=>{offset=Math.max(0,offset-50);loadMedia().catch(e=>toast(e.message,true));});
$('#next').addEventListener('click',()=>{offset+=50;loadMedia().catch(e=>toast(e.message,true));});
$('#media-body').addEventListener('change',e=>{if(e.target.matches('.media-check')){const id=Number(e.target.dataset.id);e.target.checked ? selected.add(id) : selected.delete(id);updateSelection();}});
$('#select-all').addEventListener('change',e=>{$$('.media-check:not(:disabled)').forEach(el=>{el.checked=e.target.checked;el.checked?selected.add(Number(el.dataset.id)):selected.delete(Number(el.dataset.id));});updateSelection();});
$('#queue-selected').addEventListener('click',async e=>{e.currentTarget.disabled=true;try{await queueMedia([...selected]);}catch(err){toast(err.message,true);}finally{updateSelection();}});
$('#close-details').addEventListener('click',()=>$('#details-dialog').close());
document.addEventListener('click',async e=>{
  const inspectButton=e.target.closest('.media-details'), action=e.target.closest('.job-action'), log=e.target.closest('.job-log'), enqueue=e.target.closest('.detail-queue');
  try{
    if(inspectButton)await inspect(inspectButton.dataset.id);
    if(action){action.disabled=true;await api('jobs/'+action.dataset.job+'/'+action.dataset.action,{method:'POST'});toast(action.dataset.action==='retry'?'Retry queued.':'Cancellation requested. Original retained.');await poll();}
    if(log){const logs=await api('jobs/'+log.dataset.job+'/log');$('#details-content').innerHTML='<h3>FFmpeg logs</h3>'+Object.entries(logs).map(([k,v])=>`<h4>${esc(k)}</h4><pre>${esc(v || 'No warnings or errors.')}</pre>`).join('')+(Object.keys(logs).length?'':'<p class="muted">This job has not started encoding.</p>');$('#details-dialog').showModal();}
    if(enqueue){enqueue.disabled=true;await queueMedia([Number(enqueue.dataset.id)]);$('#details-dialog').close();}
  }catch(err){toast(err.message,true);if(action)action.disabled=false;if(enqueue)enqueue.disabled=false;}
});
$('#save-rules').addEventListener('click',async()=>{try{const rules=JSON.parse($('#rules-json').value);await api('settings',{method:'PUT',body:JSON.stringify({rules})});await loadRules();toast('Detection rules saved.');await poll();}catch(e){toast(e.message,true);}});
$('#settings-form').addEventListener('submit',async e=>{
  e.preventDefault();const changes={};
  for(const el of e.target.elements){if(!el.name)continue;changes[el.name]=el.type==='checkbox'?el.checked:el.type==='number'?Number(el.value):el.value;}
  const button=e.target.querySelector('button[type=submit]');button.disabled=true;
  try{await api('settings',{method:'PUT',body:JSON.stringify(changes)});toast('Settings saved. Newly queued jobs use this preset.');await poll();}catch(err){toast(err.message,true);}finally{button.disabled=false;}
});
$('#test-encoder').addEventListener('click',async e=>{const b=e.currentTarget;b.disabled=true;try{const r=await api('encoder-test',{method:'POST'});toast(!r.available?r.error:r.nvdec_available?'NVDEC → NVENC pipeline test passed.':'NVENC passed; NVDEC unavailable. Auto uses CPU decoding.',!r.available||!r.nvdec_available);await poll();}catch(err){toast(err.message,true);}finally{b.disabled=false;}});
$('#torrent-form').addEventListener('submit',async e=>{
  e.preventDefault();const f=e.target,b=f.querySelector('button[type=submit]');b.disabled=true;
  const value={enabled:f.elements.enabled.checked,port:Number(f.elements.port.value)};
  if(f.elements.username.value)value.username=f.elements.username.value;
  if(f.elements.password.value)value.password=f.elements.password.value;
  try{await api('qbittorrent',{method:'PUT',body:JSON.stringify(value)});f.elements.username.value='';f.elements.password.value='';await loadTorrentSettings();toast(value.enabled?'qBittorrent connected. Matching torrents will remain stopped.':'qBittorrent coordination disabled.');}catch(err){toast(err.message,true);}finally{f.elements.password.value='';b.disabled=false;}
});
setInterval(()=>{if(!document.hidden)poll();},3000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
api('me').then(r=>showDashboard(r.name)).catch(()=>showLogin());
