/* OrderFlow Studio — no build step, third-party JS, or external browser API required.
 * Uses SSE from the bundled FastAPI server. See README for LIVE market feed wiring. */
'use strict';

const state = {
  snapshot: null, selection: 'ALL', lane: 'both', watchOnly: false,
  watchlist: new Set(JSON.parse(localStorage.getItem('of-watchlist') || '[]')),
  query: '', searchOpen: false, drawer: null, detail: null,
  showVwap: true, showOB: true, showLevels: true,
  timeframe: '5m', detailRequestId: 0,
  frozen: false, lastError: null, streamConnected: false, pending: null,
  modal: null, clockTimer: null,
};

const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const fmt = (v, n=2) => Number(v || 0).toLocaleString('en-IN', {minimumFractionDigits:n, maximumFractionDigits:n});
const price = v => '₹' + fmt(v);
const change = x => x==null ? 'N/A' : `${x >= 0 ? '+' : '−'}${fmt(Math.abs(x))}%`;
const cls = x => x==null ? 'neutral' : (x >= 0 ? 'positive' : 'negative');
const rvolText = r => r==null ? 'N/A' : fmt(r,2)+'×';
const istTime = timestamp => timestamp ? new Date(timestamp).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',day:'2-digit',month:'short'})+' IST' : 'No quotes yet';
const compact = n => {if (n == null) return '—'; const a=Math.abs(n);return a>=1e7?fmt(n/1e7,2)+' Cr':a>=1e5?fmt(n/1e5,2)+' L':a>=1e3?fmt(n/1e3,1)+' K':fmt(n,0);};
const icon = (name, c='ico') => {
  const paths = {
    grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    activity:'<path d="M3 12h4l3-7 4 14 3-7h4"/>',
    layers:'<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
    search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    down:'<path d="m6 9 6 6 6-6"/>', up:'<path d="m7 14 5-5 5 5"/>',
    arrowUp:'<path d="m6 17 12-12m-11 0h11v11"/>', arrowDown:'<path d="m6 7 12 12m-1-11v11H6"/>',
    help:'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.7 2.7 0 0 1 5.2 1c0 2-2.7 2-2.7 4M12 17.5h.01"/>',
    bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    settings:'<circle cx="12" cy="12" r="3"/><path d="M18.5 13.5 20 15l-2 3-2-1-2 1-.5 2h-3l-.5-2-2-1-2 1-2-3 1.5-1.5-.3-3L3.5 9 5 6l2 1 2-1 .5-2h3L13 6l2 1 2-1 2 3-1.5 1.5z"/>',
    bookmark:'<path d="M5 4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v18l-7-4-7 4V4Z"/>',
    eye:'<path d="M2 12c3-5 6-7 10-7s7 2 10 7c-3 5-6 7-10 7S5 17 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    pause:'<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    play:'<path d="m7 4 13 8-13 8V4Z"/>',
    x:'<path d="m5 5 14 14M19 5 5 19"/>',
    chevronRight:'<path d="m9 5 7 7-7 7"/>',chevronLeft:'<path d="m15 5-7 7 7 7"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    shield:'<path d="m12 2 8 3v6c0 5-4 8-8 11-4-3-8-6-8-11V5l8-3Z"/><path d="m9 12 2 2 4-4"/>',
    refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 9a7 7 0 0 1 12-2l2 5M4 12l2 5a7 7 0 0 0 12-2"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    filter:'<path d="M4 7h16M7 12h10M10 17h4"/>',
    bar:'<path d="M3 20V9h4v11M10 20V4h4v16M17 20v-7h4v7"/>',
    bolt:'<path d="m13 2-9 11h7l-1 9 10-12h-7V2Z"/>',
    arrowRight:'<path d="M4 12h16m-7-7 7 7-7 7"/>',
  };
  return `<span class="${c}" aria-hidden="true"><svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round">${paths[name] || paths.activity}</svg></span>`;
};

function sparkSVG(points, color='currentColor', w=84, h=28) {
  if (!points || points.length<2) return '';
  const low=Math.min(...points), high=Math.max(...points), rg=Math.max(.01,high-low);
  const xy=points.map((p,i)=>`${(i/(points.length-1)*w).toFixed(1)},${(h-3-(p-low)/rg*(h-7)).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${xy}" stroke="${color}" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

function metric(label,value,meta,theme='none',symbol='activity',progress=null){
  return `<div class="metric"><div class="metric-label"><span class="metric-icon">${icon(symbol)}</span>${esc(label)}</div>
    <div class="metric-num ${theme}">${value}</div><div class="metric-meta">${meta}</div>
    ${progress!==null?`<div class="metric-track"><span style="width:${Math.max(0,Math.min(100,progress))}%;background:${theme==='negative'?'#d6727d':'#35b97d'}"></span></div>`:''}</div>`;
}

function renderShell(){
  // Kiosk-style market map: precisely one viewport, with no page header/sidebar.
  $('#app').innerHTML = `<main class="market-screen" aria-label="OrderFlow Studio full-screen scanner">
    <section class="workspace">
      <section class="panel flow-panel" aria-label="Sector strength, flowing breadth and ranked stock scan">
        <div class="flow-board">
          <div class="sectors-col" id="sectors"></div>
          <div class="flow-visual" id="flow-visual"></div>
          <div class="stock-lanes" id="stock-lanes"></div>
        </div>
      </section>
      <aside class="panel picks-panel" aria-label="Three strongest bullish and three strongest bearish picks">
        <div class="picks-body" id="picks"></div>
        <div class="scanner-credit" title="Strength-ranked stock momentum; not actual order-book depth">
          <span class="scanner-brand">ORDERFLOW<span>.</span>STUDIO</span>
          <button type="button" class="compact-help" data-action="methodology" aria-label="About this scanner">${icon('help','mini-ico')}</button>
        </div>
      </aside>
    </section>
  </main>`;
}

function visibleStocks(direction){
  const rows=state.snapshot?.[direction]||[];
  return rows.filter(r=>(state.selection==='ALL'||r.sector===state.selection||(state.selection==='NIFTY_50'&&state.nifty?.has(r.symbol)))&&(!state.watchOnly||state.watchlist.has(r.symbol)));
}
function renderMetrics(){
  // Source identity remains visible in the wires panel in both demo and Kite modes.
  const s=state.snapshot, pill=$('#feed-pill');
  if(!s||!pill)return;
  const kite=s.mode==='kite', connected=s.feedStatus==='connected';
  pill.classList.toggle('live',s.mode==='live'||(kite&&connected));
  pill.classList.toggle('feed-warning',kite&&!connected);
  pill.querySelector('span:last-child').textContent = kite
    ? `KITE ${connected?'CONNECTED':(s.feedStatus||'STARTING').toUpperCase()}`
    : (s.mode==='live'?'EXTERNAL LIVE':'DEMO DATA');
  pill.title=kite?`${s.feedError||'Zerodha Kite Connect'} · Last quote: ${istTime(s.lastQuoteAt)}`
    :(s.mode==='demo'?'Synthetic sample data — no live orders':'External feed adapter');
}
function sectorBar(s){
  if(!s.total)return '<span style="background:#edf1ed"></span>';
  // 20 tiny vertical squares illustrate sector breadth, not volume.
  const greens=Math.round(s.bullish/s.total*20);
  return Array.from({length:20},(_,i)=>`<span class="${i>=greens?'red':''}"></span>`).join('');
}
function renderSectors(){
  const sectors=state.snapshot?.sectors||[];
  $('#sectors').innerHTML=`<div class="column-heading"><span>SECTOR STRENGTH</span><span>CHANGE</span></div>${sectors.map(s=>`<button class="sector-row ${state.selection===s.name?'selected':''}" data-action="sector" data-sector="${esc(s.name)}" aria-pressed="${state.selection===s.name}">
  <div class="sector-meta"><span class="sector-name" title="${esc(s.name)}">${esc(s.name)}</span><span class="sector-change ${cls(s.change)}">${change(s.change)}</span></div><div class="sector-bars">${sectorBar(s)}</div></button>`).join('')}
    <button class="sect-all" data-action="sector" data-sector="ALL">${state.selection==='ALL'?'✓ ':''}Show all sectors</button>
    <div class="sector-bottom">${icon('layers','mini-ico')} ${sectors.length} SECTORS TRACKED</div>`;
}
function hash(s){let x=0;for(let i=0;i<s.length;i++)x=(x*31+s.charCodeAt(i))>>>0;return x;}
function flowSVG(){
  const sectors=state.snapshot?.sectors||[];
  // These animated strands represent directional momentum, NOT exchange order-book liquidity.
  // Geometry is aligned to the ACTUAL rendered sector buttons (including resized viewports).
  const w=510,H=720,visual=$('#flow-visual'), rect=visual.getBoundingClientRect();
  const scale=rect.height?H/rect.height:1;
  const sectorRows=$$('#sectors .sector-row');
  let base='',traces='',particles='',terminals='';
  sectors.forEach((sector,si)=>{
    const row=sectorRows[si]?.getBoundingClientRect();
    const startY=row?Math.max(10,Math.min(H-10, (row.top+row.height/2-rect.top)*scale)):(43+si*43);
    const selected=state.selection==='ALL'||state.selection===sector.name||state.selection==='NIFTY_50';
    const seed=hash(sector.name);
    const count=12;
    const greens=Math.round(count*sector.bullish/Math.max(1,sector.total));
    for(let k=0;k<count;k++){
      const bullish=k<greens;
      const active=selected&&(state.lane==='both'||state.lane===(bullish?'bullish':'bearish'));
      // Route all flows to the correct lane and distribute them across its 5 rows.
      const bucket=(si*19+k*13+seed%31)%275;
      const target=bullish?48+bucket:402+bucket;
      const swing=55+((si*19+k*31)%115);
      const path=`M 0 ${startY.toFixed(2)} C ${(55+swing*.23).toFixed(2)} ${startY.toFixed(2)}, ${(145+swing).toFixed(2)} ${(startY*.55+target*.45).toFixed(2)}, 260 ${(startY*.22+target*.78).toFixed(2)} S 430 ${target.toFixed(2)}, 509 ${target.toFixed(2)}`;
      const pigment=bullish?'#24b974':'#e66c83', bright=bullish?'#078e59':'#d84460';
      const opacity=active?(0.29+(k%4)*.075):0.028;
      base+=`<path class="flow-wire-base" d="${path}" stroke="${pigment}" stroke-width="${1.05+(k%3)*.3}" opacity="${opacity.toFixed(2)}"/>`;
      if(active && ((k+si)%3===0)){
        const duration=(4.5+(si%5)*.7+(k%4)*.43).toFixed(2);
        traces+=`<path class="flow-wire-tracer" d="${path}" stroke="${bright}" stroke-width="${(1.6+(k%2)*.6).toFixed(1)}" style="--wire-duration:${duration}s;--wire-delay:-${((si*7+k)%6)*1.13}s"/>`;
      }
      if(active&&k===Math.min(2,Math.max(0,greens-1))){
        const duration=(5.4+((si*3)%7)*.43).toFixed(2);
        particles+=`<circle r="2.5" fill="${bright}" class="flow-particle" opacity=".86"><animateMotion dur="${duration}s" repeatCount="indefinite" begin="-${(si%6)*.6}s" path="${path}"/></circle>`;
      }
      if(active&&k%3===0)terminals+=`<circle cx="509" cy="${target}" r="2.5" fill="${bright}" opacity=".73"/>`;
    }
  });
  const grid=Array.from({length:11},(_,i)=>`<line x1="0" x2="510" y1="${i*72}" y2="${i*72}" stroke="#d6e8df" stroke-width=".75" stroke-dasharray="2 7" opacity=".6"/>`).join('');
  const bars=Array.from({length:90},(_,i)=>{
    const bull=i<45,local=i%45;
    const y=(bull?36:397)+local*6.6;
    const dx=24+(hash('terminal'+i)%135);
    return `<line x1="${509-dx}" x2="509" y1="${y}" y2="${y}" stroke="${bull?'#33c884':'#e87790'}" stroke-width="${i%4===0?2.6:1.55}" opacity="${.13+(i%5)*.055}"/>`;
  }).join('');
  return `<svg class="flow-svg" viewBox="0 0 ${w} ${H}" preserveAspectRatio="none" role="img" aria-label="Animated green and red momentum connections flowing from sectors toward bullish and bearish stock lists">
    <defs>
      <linearGradient id="flow-wash-green" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#c5f5db" stop-opacity=".52"/></linearGradient>
      <linearGradient id="flow-wash-red" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#ffd5de" stop-opacity=".48"/></linearGradient>
    </defs>
    <rect x="0" y="0" width="510" height="355" fill="url(#flow-wash-green)"/>
    <rect x="0" y="356" width="510" height="364" fill="url(#flow-wash-red)"/>
    ${grid}<line x1="0" y1="358" x2="510" y2="358" stroke="#c8dfd4" stroke-width="1.7"/>
    <g class="wire-bases">${base}</g><g class="wire-tracers">${traces}</g>
    <g class="wire-particles">${particles}</g><g class="wire-bars">${bars}</g>${terminals}
    <path d="M509 25V349" stroke="#53c790" opacity=".62" stroke-width="3"/>
    <path d="M509 374V709" stroke="#e97d92" opacity=".6" stroke-width="3"/>
    <text x="449" y="33" fill="#218d5f" font-family="monospace" font-size="11" font-weight="700">BUY ↑</text>
  </svg>`;
}
function renderFlow(force=false){
  const visual=$('#flow-visual');if(!visual)return;
  const sectors=state.snapshot?.sectors||[];
  const key=[state.selection,state.lane,visual.clientWidth,visual.clientHeight,
    ...sectors.map(s=>s.name+':'+s.bullish+':'+s.total)].join('|');
  if(force||visual.dataset.flowKey!==key){
    visual.dataset.flowKey=key;
    visual.innerHTML=`<div class="flow-scene-title"><i class="living"></i> DIRECTIONAL FLOW <span class="slash">/</span> LIVE MAP</div>
     ${flowSVG()}
     <div class="flow-center-label"><strong id="breadth-count"></strong><small>STOCK UNIVERSE</small></div>
     <div class="flow-source"><span class="market-pill" id="feed-pill"><span class="dot"></span><span>LOADING</span></span><span class="flow-explain">MOMENTUM · NOT ORDER-BOOK DEPTH</span></div>`;
  }
  const count=$('#breadth-count');
  if(count)count.innerHTML=`${state.snapshot?.bullishCount||0}<span class="breadth-slash"> / </span><em>${state.snapshot?.bearishCount||0}</em>`;
  renderMetrics();
}
function renderTile(r,dir){
  const bearish=dir==='bearish';
  return `<button class="stock-tile ${bearish?'red':''}" data-stock="${esc(r.symbol)}" aria-label="Open ${esc(r.symbol)} chart, ${change(r.change)}, ${esc(r.sector)}" title="${esc(r.symbol)} · ${esc(r.sector)} · ${change(r.change)}">
    <span class="tile-top"><span class="tile-sym">${esc(r.symbol)}</span><span class="tile-rank">${String(r.rank).padStart(2,'0')}</span></span>
    <span class="tile-bottom"><span class="tile-pct">${change(r.change)}</span><span class="tile-spark">${sparkSVG(r.spark)}</span></span>
    <span class="tile-meta"><span>${esc(r.sector)}</span><span>RVOL ${rvolText(r.rvol)}</span></span>
  </button>`;
}
function renderLane(dir){
  const rows=visibleStocks(dir); const bull=dir==='bullish',show=state.lane==='both'||state.lane===dir;
  if(!show) return '';
  return `<section class="lane ${bull?'bullish-lane':'bearish-lane'}" aria-label="Top 10 ${dir} momentum leaders">
    <div class="lane-top"><div class="lane-label ${bull?'':'red'}"><span class="little-arrow">${icon(bull?'arrowUp':'arrowDown','mini-ico')}</span><span class="lane-heading-text"><b>TOP 10 ${bull?'BULLISH':'BEARISH'}</b><span class="lane-sub"> / MOMENTUM LEADERS</span></span></div><span class="lane-num ${bull?'':'red'}">${String(rows.length).padStart(2,'0')} <span class="lane-total">/ 10</span></span></div>
    <div class="stock-grid">${rows.length?rows.map(r=>renderTile(r,dir)).join(''):`<div class="empty-panel">No ${dir} stocks in this filter.</div>`}</div></section>`;
}
function renderLanes(){
  $('#stock-lanes').innerHTML=renderLane('bullish')+renderLane('bearish');
  const only=state.lane!=='both';$('#stock-lanes').classList.toggle('single-lane',only);
  for(const l of ['all','bullish','bearish']){
    const node=$(`#filter-${l}`);if(node)node.classList.toggle('active',(l==='all'?'both':l)===state.lane);
  }
}
function renderPick(r,idx,dir){
  const bull=dir==='bullish';
  return `<button class="pick-card ${bull?'':'red'}" data-stock="${esc(r.symbol)}" title="Open ${esc(r.symbol)} chart · 5m / 15m / 30m / 1h">
     <div class="pick-row"><span class="pick-ticker">${esc(r.symbol)}</span><span class="pick-badge">#${idx+1} PICK ${bull?'↑':'↓'}</span></div>
     <div class="pick-big">${change(r.change)}</div><div class="pick-meta"><span>${esc(r.sector)}</span><span>RVOL ${rvolText(r.rvol)}</span></div>
     <div class="pick-spark">${sparkSVG(r.spark)}</div>
  </button>`;
}
function renderPicks(){
  const side=dir=>{
    let rows=visibleStocks(dir);
    if(state.selection==='ALL'&&!state.watchOnly){
      const picks=state.snapshot?.[dir+'Picks']||[];
      rows=picks;
    }
    // Active filter: best three within the visible sector/watchlist.
    const selected=rows.slice(0,3);
    return `<div class="pick-group"><div class="pick-heading"><span class="${dir==='bullish'?'positive':'negative'}">${dir==='bullish'?'↑ BULLISH PICKS':'↓ BEARISH PICKS'}</span><b>${selected.length} / 3</b></div>
    ${selected.length?selected.map((r,i)=>renderPick(r,i,dir)).join(''):`<div class="empty-panel">No matching stocks</div>`}</div>`;
  };
  $('#picks').innerHTML=side('bullish')+side('bearish');
}
function renderData(){if(!state.snapshot)return;renderMetrics();renderSectors();renderFlow();renderLanes();renderPicks();}

async function connect(){
  try{
    const resp=await fetch('/api/state');if(!resp.ok)throw new Error(`Server responded ${resp.status}`);
    state.snapshot=await resp.json();renderShell();renderData();await getUniverse();
  }catch(err){$('#app').innerHTML=`<div class="boot-screen"><div class="boot-logo">OF<span>.</span></div><h2 style="margin:0">Cannot reach the market server</h2><p class="boot-copy">${esc(err.message)} · Launch with <code>python server.py</code></p><button class="btn btn-primary" onclick="location.reload()">Try again</button></div>`;return;}
  if(typeof EventSource==='undefined'){fallbackPoll();return;}
  const stream=new EventSource('/api/events');
  stream.onmessage=e=>{
    state.streamConnected=true;
    try{const snapshot=JSON.parse(e.data);if(state.frozen){state.pending=snapshot;return;}state.snapshot=snapshot;renderData();}
    catch(err){console.warn('Invalid stream event',err)}
  };
  stream.onerror=()=>{state.streamConnected=false};
  window.addEventListener('beforeunload',()=>stream.close());
}
async function fallbackPoll(){setInterval(async()=>{if(state.frozen)return;try{const r=await fetch('/api/state');state.snapshot=await r.json();renderData();}catch(e){}},4500)}
async function getUniverse(){try{const r=await fetch('/api/universe');const d=await r.json();state.universe=d.symbols;state.nifty=new Set(d.sectors.NIFTY_50||[]);}catch(e){state.universe=[];state.nifty=new Set();}}

function toast(message){const root=$('#toast-root');root.innerHTML=`<div class="toast">${icon('info')}${esc(message)}</div>`;clearTimeout(toast.timer);toast.timer=setTimeout(()=>root.innerHTML='',3200)}
function updateSearch(){
  const root=$('#search-results');if(!root)return;
  const query=state.query.trim().toUpperCase();
  if(!state.searchOpen||!query){root.innerHTML='';return;}
  const all=state.universe||Object.values(state.snapshot||{}).flatMap(Array.isArray?x=>x:()=>[]).map(x=>x.symbol).filter(Boolean);
  const hits=all.filter(s=>s.includes(query)).slice(0,9);
  const lookup=new Map([...(state.snapshot?.bullish||[]),...(state.snapshot?.bearish||[])].map(r=>[r.symbol,r]));
  root.innerHTML=`<div class="search-popover">${hits.length?hits.map(s=>`<button class="search-row" data-stock="${esc(s)}"><div><strong>${esc(s)}</strong> <span>${esc(lookup.get(s)?.sector||'NSE')}</span></div><span class="${cls(lookup.get(s)?.change||0)}">${lookup.has(s)?change(lookup.get(s).change):'View chart →'}</span></button>`).join(''):`<div class="search-empty">No symbols found.</div>`}</div>`;
}

const TIMEFRAMES = Object.freeze({'5m':'5m','15m':'15m','30m':'30m','1h':'1h'});
const tfDescription = tf => ({'5m':'5-minute','15m':'15-minute','30m':'30-minute','1h':'1-hour'}[tf]||'5-minute');

async function loadSymbolDetail(symbol){
  const timeframe=state.timeframe, requestId=++state.detailRequestId;
  try{
    const url='/api/symbol/'+encodeURIComponent(symbol)+'?timeframe='+encodeURIComponent(timeframe);
    const r=await fetch(url,{cache:'no-store'});
    if(!r.ok)throw new Error(`Server returned ${r.status}`);
    const d=await r.json();
    if(requestId!==state.detailRequestId||state.drawer!==symbol||state.timeframe!==timeframe)return;
    state.detail=d;renderDrawer();
  }catch(err){
    if(requestId!==state.detailRequestId||state.drawer!==symbol)return;
    toast(`Cannot load ${symbol} (${timeframe}): ${err.message}`);
    closeOverlay();
  }
}
async function openSymbol(symbol){
  state.searchOpen=false;state.query='';if($('#search')){$('#search').value='';updateSearch()}
  state.drawer=symbol;state.detail=null;showDrawerLoading(symbol);
  await loadSymbolDetail(symbol);
}
function switchTimeframe(tf){
  if(!TIMEFRAMES[tf]||tf===state.timeframe||!state.drawer)return;
  state.timeframe=tf;state.detail=null;
  $$('#overlay-root [data-action="timeframe"]').forEach(button=>{
    const active=button.dataset.tf===tf;
    button.classList.toggle('active',active);
    button.setAttribute('aria-pressed',String(active));
  });
  const chart=$('#chart-wrap');
  if(chart)chart.innerHTML=`<div class="skeleton" role="status">Loading ${tfDescription(tf)} candles…</div>`;
  const meta=$('.chart-meta');if(meta)meta.textContent=`${state.drawer} · loading ${tfDescription(tf)} candles…`;
  loadSymbolDetail(state.drawer);
}
function closeOverlay(){state.detailRequestId++;state.drawer=null;state.detail=null;state.modal=null;$('#overlay-root').innerHTML='';document.body.style.overflow='';}
function showDrawerLoading(symbol){
  document.body.style.overflow='hidden';
  $('#overlay-root').innerHTML=`<div class="overlay-scrim" data-action="close-overlay"></div><aside class="detail-drawer" role="dialog" aria-modal="true" aria-label="${esc(symbol)} stock details"><div class="detail-head"><div class="breadcrumb">MARKET / <b>${esc(symbol)}</b></div></div><div class="skeleton">Loading ${tfDescription(state.timeframe)} chart…</div></aside>`;
}
function detailNav(dir){
  const r=state.detail;if(!r)return null;
  const rows=[...(state.snapshot?.bullish||[]),...(state.snapshot?.bearish||[])];
  const idx=rows.findIndex(x=>x.symbol===r.symbol);if(idx<0)return null;
  return rows[(idx+dir+rows.length)%rows.length].symbol;
}
function labelOrDash(v){return v==null?'—':fmt(v)}
function renderDrawer(){
  const d=state.detail;if(!d)return;
  if(d.unavailable){$('#overlay-root').innerHTML=`<div class="overlay-scrim" data-action="close-overlay"></div><aside class="detail-drawer" role="dialog" aria-modal="true"><div class="detail-head"><div class="detail-headbar"><b>${esc(d.symbol)} · ${esc(d.sector)}</b><button class="icon-btn" data-action="close-overlay">${icon('x')}</button></div></div><div class="skeleton">No live quotes received yet for this symbol.</div></aside>`;return;}
  const bull=d.change>=0;const watched=state.watchlist.has(d.symbol);
  $('#overlay-root').innerHTML=`<div class="overlay-scrim" data-action="close-overlay"></div>
  <aside class="detail-drawer" role="dialog" aria-modal="true" aria-label="${esc(d.symbol)} price and order block detail">
    <div class="detail-head"><div class="detail-headbar"><div class="breadcrumb">NSE / ${esc(d.sector)} ${icon('chevronRight','mini-ico')} <b>${esc(d.symbol)}</b> <span>· ${esc(state.timeframe.toUpperCase())}</span></div>
      <div class="detail-head-actions"><button class="icon-btn" data-action="watch" data-symbol="${esc(d.symbol)}" title="${watched?'Remove from':'Add to'} watchlist" style="color:${watched?'#138957':'#7c9388'}">${icon('bookmark')}</button>
      <button class="icon-btn" data-action="previous" title="Previous stock">${icon('chevronLeft')}</button><button class="icon-btn" data-action="next" title="Next stock">${icon('chevronRight')}</button><button class="icon-btn" data-action="close-overlay" title="Close details">${icon('x')}</button></div></div>
    <div class="detail-symbol-row"><div class="detail-company"><div class="detail-icon">${esc(d.symbol.slice(0,2))}</div><div><h2>${esc(d.symbol)}</h2><small>${esc(d.sector)} · NSE CASH / F&O WATCHLIST</small></div></div>
      <div class="detail-price"><b>${price(d.price)}</b><span class="${cls(d.change)}">${change(d.change)} ${bull?'↗':'↘'}</span></div></div></div>
    <div class="detail-content">
     <div class="signal-strip">
       <div class="signal-metric"><small>SCAN RANK</small><strong>#${d.rank}<span style="color:#9bb1a3;font-size:10px"> / ${d.of}</span></strong><small class="explain">${bull?'BULLISH':'BEARISH'} POOL</small></div>
       <div class="signal-metric"><small>${d.oiSource?.startsWith('NFO')?'FUTURES OI Δ':'OPEN INTEREST Δ'}</small><strong class="${cls(d.oiChange)}">${change(d.oiChange)}</strong><small class="explain">${d.oiSource?.startsWith('NFO')?'NFO FUT · SINCE CONNECT':(d.oi==null?'NO FUTURES OI':'OI REFERENCE')}</small></div>
       <div class="signal-metric"><small>PRICE ACTION</small><strong style="font-size:11px;margin-top:7px">${esc(d.buildup)}</strong><small class="explain">OI + PRICE</small></div>
       <div class="signal-metric"><small>RELATIVE VOLUME</small><strong>${rvolText(d.rvol)}</strong><small class="explain">${d.rvol==null?'BASELINE NOT LOADED':'PARTICIPATION'}</small></div>
     </div>
     <section class="detail-card chart-panel"><div class="chart-top"><div><div class="chart-title">${icon('activity')} Price action & order blocks</div><div class="chart-meta">${esc(d.symbol)} · ${tfDescription(d.timeframe)} candles · ${state.snapshot?.mode==='demo'?'SIMULATED':(state.snapshot?.mode==='kite'?'ZERODHA KITE':'EXTERNAL INGEST')} DATA</div></div><div class="chart-actions" role="group" aria-label="Candlestick chart timeframe">${Object.keys(TIMEFRAMES).map(tf=>`<button type="button" class="timeframe ${state.timeframe===tf?'active':''}" data-action="timeframe" data-tf="${tf}" aria-pressed="${state.timeframe===tf}" title="View ${tfDescription(tf)} candles">${tf}</button>`).join('')}</div></div>
       <div class="chart-wrap" id="chart-wrap">${chartSVG(d)}</div>
       <div class="chart-legend"><span><i class="squarekey"></i> Candidate OB</span><span><i class="linekey"></i> ${indicatorButton('vwap','VWAP',state.showVwap)}</span><span><i class="linekey green"></i> ${indicatorButton('levels','S/R levels',state.showLevels)}</span><span><i class="linekey pink"></i> ${indicatorButton('ob','OB zones',state.showOB)}</span></div>
     </section>
     <div class="detail-bottom"><section class="detail-card structure"><h3>Signal breakdown</h3>
        <div class="structure-row"><span>Momentum score</span><b>${fmt(d.score,1)}</b></div><div class="structure-row"><span>Intraday change</span><b class="${cls(d.change)}">${change(d.change)}</b></div><div class="structure-row"><span>Relative volume</span><b>${rvolText(d.rvol)}</b></div><div class="structure-row"><span>Above / below VWAP</span><b>${d.price>=d.vwap?'ABOVE ↑':'BELOW ↓'}</b></div></section>
       <section class="detail-card structure"><h3>Order-block lens</h3><div class="structure-row"><span>Timeframe</span><b>${esc(d.timeframe.toUpperCase())}</b></div><div class="structure-row"><span>Candidate zones</span><b>${d.blocks.length}</b></div>
       <div class="structure-row"><span>Latest OB</span><b>${d.blocks.length?d.blocks.at(-1).type.toUpperCase():'NONE'}</b></div><div class="structure-row"><span>VWAP source</span><b>CANDLE VOLUME</b></div></section></div>
     <div class="method-note">${icon('info')}<span>Educational scanner. OB rectangles are algorithmic price-action candidates, <strong>not</strong> verified institutional limit orders. S/R levels are calculated reference levels, not trade instructions. ${state.snapshot?.mode==='demo'?'Prices shown here are generated demo data, not live NSE market data.':(state.snapshot?.mode==='kite'?'NSE cash prices come from Kite; futures OI, when available, measures change since this app connected (not since previous close). The last quote timestamp may be from a previous session when the exchange is closed. The current candle can be incomplete.':'Prices reflect data supplied to your configured ingestion endpoint.')}</span></div>
    </div>
  </aside>`;
  wireChartHover();
}
function indicatorButton(key,label,active){return `<button data-action="indicator" data-key="${key}" style="border:0;padding:1px 2px;background:none;color:${active?'#597468':'#b8c5bc'};font:700 9px var(--mono);text-decoration:${active?'none':'line-through'}" title="Toggle ${label}">${label}</button>`}

function chartSVG(d){
  const candles=d.candles||[];if(!candles.length)return `<div class="skeleton">No ${tfDescription(d.timeframe)} candles received.</div>`;
  const W=1160,H=505,L=54,R=80,T=26,B=45,volH=70;
  const chartBottom=H-B-volH,innerW=W-L-R;
  const extra=state.showLevels?Object.values(d.levels):[];
  const lows=candles.map(c=>c.low).concat(state.showOB?d.blocks.map(b=>b.low):[],extra),highs=candles.map(c=>c.high).concat(state.showOB?d.blocks.map(b=>b.high):[],extra);
  let min=Math.min(...lows),max=Math.max(...highs);const pad=Math.max((max-min)*.09,d.price*.0025);min-=pad;max+=pad;
  const yp=p=>T+(max-p)/Math.max(.01,max-min)*(chartBottom-T);
  const dx=innerW/candles.length,cx=i=>L+(i+.5)*dx;
  const vmax=Math.max(1,...candles.map(c=>c.volume));
  const horizontal=Array.from({length:5},(_,i)=>{const value=max-(max-min)*i/4,y=yp(value);return `<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="#eaf0eb" stroke-width="1"/><text x="${W-R+9}" y="${y+4}" fill="#a0afa6" font-size="10" font-family="monospace">${fmt(value)}</text>`;}).join('');
  const multiDay=candles.length>1&&candles[0].time.slice(0,10)!==candles.at(-1).time.slice(0,10);
  const vertical=Array.from({length:7},(_,i)=>{const idx=Math.floor(i*(candles.length-1)/6),x=cx(idx),dt=new Date(candles[idx].time);const time=dt.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Kolkata',hour12:false});const label=multiDay?dt.toLocaleDateString('en-IN',{day:'2-digit',month:'short',timeZone:'Asia/Kolkata'})+' '+time:time;return `<line x1="${x}" x2="${x}" y1="${T}" y2="${H-B}" stroke="#f1f5f1" stroke-width="1"/><text x="${x}" y="${H-11}" fill="#9caea2" text-anchor="middle" font-family="monospace" font-size="9">${esc(label)}</text>`;}).join('');
  const ob=state.showOB?d.blocks.map((b,i)=>{const left=cx(b.fromIndex)-dx/2,right=cx(Math.min(b.toIndex,candles.length-1))+dx/2;const top=Math.max(T,yp(b.high)),bottom=Math.min(chartBottom,yp(b.low));const bull=b.type==='bullish';
    return `<rect x="${left}" y="${top}" width="${Math.max(1,right-left)}" height="${Math.max(4,bottom-top)}" fill="${bull?'#3cb97c':'#e78695'}" fill-opacity=".10" stroke="${bull?'#3fbf83':'#e58a97'}" stroke-opacity=".6" stroke-dasharray="4 4" rx="1"/><text x="${Math.min(right-3,W-R-2)}" y="${Math.max(14,top-4)}" text-anchor="end" font-family="monospace" font-size="9" fill="${bull?'#2b9f6b':'#d07886'}">${bull?'BULLISH OB':'BEARISH OB'}</text>`;}).join(''):'';
  const candlesSVG=candles.map((c,i)=>{const x=cx(i),bull=c.close>=c.open,fill=bull?'#27ac75':'#df7886';
    const yo=yp(c.open),yc=yp(c.close),yhi=yp(c.high),ylo=yp(c.low),bodyY=Math.min(yo,yc),bodyH=Math.max(1.6,Math.abs(yc-yo));
    return `<line x1="${x}" x2="${x}" y1="${yhi}" y2="${ylo}" stroke="${fill}" stroke-width="1.1"/><rect x="${x-Math.max(1.5,dx*.27)}" y="${bodyY}" width="${Math.max(3,dx*.54)}" height="${bodyH}" fill="${fill}" rx=".4"/>
      <rect x="${x-Math.max(1.4,dx*.22)}" y="${H-B-c.volume/vmax*(volH-5)}" width="${Math.max(3,dx*.44)}" height="${c.volume/vmax*(volH-5)}" fill="${fill}" opacity=".3"/>`;
  }).join('');
  const vwap=state.showVwap?`<path d="${candles.map((c,i)=>`${i?'L':'M'} ${cx(i)} ${yp(c.vwap)}`).join(' ')}" stroke="#667cdb" stroke-width="1.7" fill="none" stroke-linejoin="round" stroke-dasharray="5 3" opacity=".93"/>`:'';
  const labels=[['R2',d.levels.r2,'#5e99bf'],['R1',d.levels.r1,'#42a581'],['S1',d.levels.s1,'#e0a06c']];
  const levels=state.showLevels?labels.map(([label,value,color])=>{const y=yp(value);return `<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="${color}" stroke-dasharray="7 5" opacity=".45" stroke-width="1.3"/><text x="${L+5}" y="${y-4}" fill="${color}" font-family="monospace" font-size="9">${label} ${fmt(value)}</text>`}).join(''):'';
  const ly=yp(d.price);
  const liveLabel=`<line x1="${L}" x2="${W-R}" y1="${ly}" y2="${ly}" stroke="${d.change>=0?'#27b879':'#d77483'}" stroke-width="1.1" stroke-dasharray="2 3"/><rect x="${W-R}" y="${ly-11}" width="${R}" height="22" rx="3" fill="${d.change>=0?'#1c9864':'#ce6d7b'}"/><text x="${W-R+R/2}" y="${ly+4}" text-anchor="middle" fill="white" font-size="11" font-family="monospace" font-weight="bold">${fmt(d.price)}</text>`;
  return `<svg id="ohlc-chart" class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" aria-label="${esc(d.symbol)} ${tfDescription(d.timeframe)} OHLC candlestick chart with VWAP and candidate order blocks" role="img">
    <defs><clipPath id="plot-clip"><rect x="${L}" y="${T}" width="${innerW}" height="${H-T-B}"/></clipPath></defs>
    <rect width="${W}" height="${H}" fill="#fff"/><g>${horizontal}${vertical}</g>
    <line x1="${L}" x2="${W-R}" y1="${chartBottom}" y2="${chartBottom}" stroke="#dae7df" stroke-width="1"/>
    <g clip-path="url(#plot-clip)">${ob}${levels}${candlesSVG}${vwap}</g>${liveLabel}
    <line id="crosshair-v" x1="0" x2="0" y1="${T}" y2="${H-B}" stroke="#8ca29a" stroke-width="1" stroke-dasharray="3 3" style="display:none;pointer-events:none"/>
    <rect id="chart-hit" x="${L}" y="${T}" width="${innerW}" height="${H-T-B}" fill="transparent" data-candles="${candles.length}"/>
    <text x="${L+5}" y="${T+13}" fill="#91a59a" font-size="9" font-family="monospace">NSE · ${esc(d.timeframe)}</text>
    <text x="${L+5}" y="${chartBottom+14}" fill="#98a9a0" font-size="9" font-family="monospace">VOL</text>
  </svg><div id="chart-tooltip" class="chart-tooltip" style="display:none"></div>`;
}
function wireChartHover(){
  const svg=$('#ohlc-chart'),tooltip=$('#chart-tooltip'),line=$('#crosshair-v');if(!svg||!tooltip||!line)return;
  const candles=state.detail?.candles||[]; const L=54, R=80,W=1160;
  svg.addEventListener('pointermove', e=>{
    const rc=svg.getBoundingClientRect();
    const point=svg.createSVGPoint();point.x=e.clientX;point.y=e.clientY;
    const transform=svg.getScreenCTM();if(!transform)return;
    const x=point.matrixTransform(transform.inverse()).x;
    if(x<L||x>W-R){tooltip.style.display='none';line.style.display='none';return;}
    const idx=Math.max(0,Math.min(candles.length-1,Math.floor((x-L)/(W-R-L)*candles.length)));
    const c=candles[idx];const xx=L+(idx+.5)*(W-R-L)/candles.length;
    line.setAttribute('x1',xx);line.setAttribute('x2',xx);line.style.display='block';
    const dt=new Date(c.time).toLocaleString('en-IN',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Kolkata'});
    tooltip.style.display='block';tooltip.innerHTML=`<b>${esc(dt)}</b> &nbsp; O ${fmt(c.open)} &nbsp; H ${fmt(c.high)} &nbsp; L ${fmt(c.low)} &nbsp; C <span style="color:${c.close>=c.open?'#219a64':'#d17280'}">${fmt(c.close)}</span><br/>VOL ${compact(c.volume)} &nbsp; VWAP ${fmt(c.vwap)}`;
    tooltip.style.left=`${Math.max(10,Math.min(rc.width-318,rc.width*.14))}px`;
  });
  svg.addEventListener('pointerleave',()=>{tooltip.style.display='none';line.style.display='none'});
}

function showModal(type){
  state.modal=type;document.body.style.overflow='hidden';
  let content='';
  if(type==='methodology')content=`<p>This dashboard recreates the workflow shown in your screenshots, with a transparent demonstration engine that can be replaced by a licensed market-data feed.</p>
    <div class="modal-section"><strong>01 · Sector pulse & wires</strong><p>The left column aggregates constituents into sectors; wires illustrate breadth and price-direction clustering. They are <b>not</b> observed capital flows or exchange order-book liquidity.</p></div>
    <div class="modal-section"><strong>02 · Top 10 → Top 3</strong><p>Each side is sorted by momentum score = 12 × |day %| + 6 × capped relative volume (neutral when unavailable) + 0.3 × positive observed futures OI% (if available) + 5 when aligned with VWAP. Top three use different sectors where available. The score is deliberately documented and configurable in <code>server.py</code>.</p></div>
    <div class="modal-section"><strong>03 · Multiple-timeframe chart & order blocks</strong><p>Candles are built from 5-minute data and aggregated to the selected 5m / 15m / 30m / 1h interval (1h anchored at 09:15 IST). VWAP uses cumulative typical-price × volume of the source bars, and candidate order blocks are opposing candles ahead of a strong structural break. These are chart-derived zones, not verified institutional orders.</p></div>`;
  else content=`<p>The scanner is running in <b>${esc(state.snapshot?.mode==='kite'?'Zerodha Kite Connect':state.snapshot?.mode==='demo'?'demo simulation':'external-feed')}</b> mode.</p>
    <div class="modal-section"><strong>Connection status</strong><p>${esc(state.snapshot?.feedStatus||'unknown')} · Last market quote: ${esc(istTime(state.snapshot?.lastQuoteAt))}. ${esc(state.snapshot?.feedError||'')}</p></div>
    <div class="modal-section"><strong>Live setup</strong><p>For Kite, create a private <code>.env</code> from <code>.env.example</code> with <code>MARKET_MODE=kite</code>, <code>KITE_API_KEY</code> and today's <code>KITE_ACCESS_TOKEN</code>. Restart the server whenever the token expires. Never paste tokens into the browser.</p></div>
    <div class="modal-section"><strong>Market-data limitations</strong><p>NSE equity prices use Kite quotes; OI comes from the nearest listed NFO futures contract when one exists. OI changes are compared against the first value observed after connection. Relative volume is N/A without a verified prior-volume baseline. Rankings are price-direction momentum, not actual order-book depth.</p></div>
    <div class="modal-section"><strong>Tracked instruments</strong><p>${state.snapshot?.universeCount||198} configured symbols · ${state.snapshot?.trackedCount||0} loaded quotes · ${state.snapshot?.futuresMapped||0} matching futures · ${state.snapshot?.missingSymbols?.length||0} unmapped equities. Watchlist NIFTY_50 is not double-counted.</p></div>`;
  $('#overlay-root').innerHTML=`<div class="overlay-scrim" data-action="close-overlay"></div><div class="modal" role="dialog" aria-modal="true" aria-label="${type==='methodology'?'Scanner methodology':'Feed information'}">
    <div class="modal-header"><span class="eyebrow">OrderFlow Studio / Guide</span><button class="icon-btn" data-action="close-overlay" aria-label="Close">${icon('x')}</button></div>
    <h2>${type==='methodology'?'Behind the signals':'Data source & setup'}</h2>${content}<div class="modal-footer"><button class="btn btn-primary" data-action="close-overlay">Got it ${icon('arrowRight')}</button></div></div>`;
}

function handleAction(node){
  const action=node.dataset.action;
  if(action==='close-overlay'){closeOverlay();return;}
  if(action==='home'){state.watchOnly=false;state.selection='ALL';state.lane='both';renderData();return;}
  if(action==='focus-watchlist'){state.watchOnly=!state.watchOnly;renderData();toast(state.watchOnly?'Showing saved watchlist only':'Showing all ranked stocks');return;}
  if(action==='scroll-insights'){showModal('methodology');return;}
  if(action==='methodology'){showModal('methodology');return;}
  if(action==='feed-info'){showModal('feed');return;}
  if(action==='sector'){state.selection=node.dataset.sector;renderSectors();renderFlow();renderLanes();renderPicks();return;}
  if(action==='lane'){state.lane=node.dataset.lane;renderFlow();renderLanes();return;}
  if(action==='toggle-freeze'){state.frozen=!state.frozen;if(!state.frozen&&state.pending){state.snapshot=state.pending;state.pending=null;renderData();}renderMetrics();toast(state.frozen?'View paused — incoming updates buffered':'View resumed');return;}
  if(action==='watch'){
    const symbol=node.dataset.symbol;
    if(state.watchlist.has(symbol))state.watchlist.delete(symbol);else state.watchlist.add(symbol);
    localStorage.setItem('of-watchlist',JSON.stringify([...state.watchlist]));renderDrawer();if(state.watchOnly)renderData();toast(`${symbol} ${state.watchlist.has(symbol)?'added to':'removed from'} watchlist`);return;
  }
  if(action==='previous'||action==='next'){const next=detailNav(action==='previous'?-1:1);if(next)openSymbol(next);return;}
  if(action==='timeframe'){switchTimeframe(node.dataset.tf);return;}
  if(action==='indicator'){
    const k=node.dataset.key;if(k==='vwap')state.showVwap=!state.showVwap;
    if(k==='ob')state.showOB=!state.showOB;
    if(k==='levels')state.showLevels=!state.showLevels;
    renderDrawer();return;
  }
}

document.addEventListener('click',ev=>{
  const stock=ev.target.closest('[data-stock]');if(stock){openSymbol(stock.dataset.stock);return;}
  const action=ev.target.closest('[data-action]');if(action){handleAction(action);return;}
  if(!ev.target.closest('#top-search')&&state.searchOpen){state.searchOpen=false;updateSearch();}
});
document.addEventListener('input',ev=>{if(ev.target.id==='search'){state.query=ev.target.value;state.searchOpen=true;updateSearch()}});
document.addEventListener('focusin',ev=>{if(ev.target.id==='search'){state.searchOpen=true;updateSearch()}});
document.addEventListener('keydown',ev=>{
  if(ev.key==='Escape'){if(state.drawer||state.modal){closeOverlay();return;}state.searchOpen=false;updateSearch();document.activeElement.blur();return;}
  if(ev.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){ev.preventDefault();$('#search')?.focus();return;}
  if(ev.key==='Enter'&&document.activeElement.id==='search'){
    const first=$('#search-results [data-stock]');if(first){ev.preventDefault();openSymbol(first.dataset.stock)}
  }
});

connect();
window.addEventListener('resize',()=>{if(state.snapshot){renderFlow(true)}});

// Refresh the currently selected chart without touching credentials or creating a Kite connection.
setInterval(async()=>{
  if(state.snapshot?.mode!=='kite'||!state.drawer||state.frozen||state.modal||state.detailRefreshing)return;
  const symbol=state.drawer, timeframe=state.timeframe; state.detailRefreshing=true;
  try{
    const response=await fetch('/api/symbol/'+encodeURIComponent(symbol)+'?timeframe='+encodeURIComponent(timeframe),{cache:'no-store'});
    if(!response.ok) return;
    const detail=await response.json();
    if(state.drawer===symbol&&state.timeframe===timeframe&&!detail.unavailable){state.detail=detail;renderDrawer();}
  }catch(_e){}finally{state.detailRefreshing=false;}
},15000);
