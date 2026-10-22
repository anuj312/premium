/* OrderFlow Studio — no build step, third-party JS, or external browser API required.
 * Uses SSE from the bundled FastAPI server. See README for LIVE market feed wiring. */
'use strict';
// Score-header UI revision: each Top 6 card shows MOM + OB in place of 01–06.
document.documentElement.dataset.leaderCardUi = 'mom-ob-header-v2';

const state = {
  snapshot: null, selection: 'ALL', lane: 'both', watchOnly: false,
  watchlist: new Set(JSON.parse(localStorage.getItem('of-watchlist') || '[]')),
  query: '', searchOpen: false, drawer: null, detail: null,
  showVwap: true, showOB: true, showLevels: true,
  timeframe: '5m', detailRequestId: 0, chartView: null, chartFollowLive: true,
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
// Each TradingView shortcut goes straight to the NSE cash-equity symbol on 5-minute candles.
// The base URL is fixed; symbols are limited to configured ticker characters and URL-encoded.
function tradingViewUrl(symbol) {
  const ticker = String(symbol ?? '').trim().toUpperCase();
  if (!/^[A-Z0-9&._-]{1,32}$/.test(ticker)) return '';
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent('NSE:'+ticker)}&interval=5`;
}
function tradingViewLink(symbol, location) {
  const url = tradingViewUrl(symbol);
  if (!url) return '';
  return `<a class="tv-open-link tv-link-${esc(location)}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" data-tradingview="${esc(symbol)}" aria-label="Open ${esc(symbol)} in TradingView 5-minute chart in new tab" title="Open ${esc(symbol)} on TradingView · NSE · 5m"><span aria-hidden="true">TV ↗</span></a>`;
}

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
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
    moon:'<path d="M20.9 13.4A9 9 0 0 1 10.6 3.1 9 9 0 1 0 20.9 13.4Z"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',minus:'<path d="M5 12h14"/>',expand:'<path d="M4 9V4h5m6 0h5v5M20 15v5h-5M9 20H4v-5"/>',crosshair:'<path d="M12 2v5m0 10v5M2 12h5m10 0h5"/><circle cx="12" cy="12" r="4"/>',
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
  // Kiosk-style premium market map: slim status header, no page scrolling.
  $('#app').innerHTML = `<main class="market-screen" aria-label="OrderFlow Studio full-screen scanner">
    <header class="premium-topbar" aria-label="Market overview and connectivity">
      <div class="brand-section">
        <span class="brand-mark" aria-hidden="true"><span>O</span><span>F</span><i></i></span>
        <div class="brand-copy"><strong>ORDERFLOW<span> STUDIO</span></strong><small>20-SESSION MARKET INTELLIGENCE <b>·</b> NSE</small></div>
      </div>
      <div class="header-right">
        <div class="header-20d" id="header-history" title="Progress loading previous 20 completed trading sessions">20D <b>0 / 198</b></div>
        <div class="header-stat" title="NSE cash quote events during this trading session"><span class="header-label">TICKS</span><b id="header-ticks">0</b></div>
        <div class="header-status" id="header-market"><span class="status-led"></span><b>CONNECTING</b></div>
        <div class="header-user" id="header-user" title="Display mode, not account authentication"><span class="user-avatar">D</span><div><small>USER</small><b>DEMO</b></div></div>
        <button type="button" class="theme-toggle" id="theme-toggle" data-action="toggle-theme" aria-label="Switch to dark mode" aria-pressed="false" title="Switch to dark mode"><span class="icon-moon">${icon('moon')}</span><span class="icon-sun">${icon('sun')}</span></button>
      </div>
    </header>
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
  applyTheme(document.documentElement.dataset.theme === 'dark'?'dark':'light', false);
}

// The theme is purely client-side; no reload, server work, or new Kite subscription.
function applyTheme(theme, persist=true){
  const dark=theme==='dark';
  document.documentElement.dataset.theme=dark?'dark':'light';
  if(persist){try{localStorage.setItem('of-theme',dark?'dark':'light')}catch(_e){}}
  const button=$('#theme-toggle');
  if(button){
    button.setAttribute('aria-label',dark?'Switch to light mode':'Switch to dark mode');
    button.setAttribute('title',dark?'Switch to light mode':'Switch to dark mode');
    button.setAttribute('aria-pressed',String(dark));
  }
  const meta=document.querySelector('meta[name="theme-color"]');
  if(meta)meta.setAttribute('content',dark?'#0c141c':'#f7faf8');
}

function visibleStocks(direction){
  const rows=state.snapshot?.[direction]||[];
  return rows.filter(r=>(state.selection==='ALL'||r.sector===state.selection||(state.selection==='NIFTY_50'&&state.nifty?.has(r.symbol)))&&(!state.watchOnly||state.watchlist.has(r.symbol)));
}
function renderHeader(){
  const s=state.snapshot;if(!s)return;
  const market=$('#header-market');if(market){
    const status=s.marketStatus || (s.mode==='demo'?'DEMO':'WAITING');
    market.className='header-status '+(status==='LIVE'?'is-live':status==='CLOSED'?'is-closed':status==='DEMO'?'is-demo':'is-waiting');
    const label={LIVE:'MARKET LIVE',CLOSED:'MARKET CLOSED',WAITING:'WAITING FOR TICKS',STALE:'FEED STALE',DEMO:'DEMO MODE'}[status]||status;
    market.querySelector('b').textContent=label;
    market.title=`${s.feedStatus} · Last quote: ${istTime(s.lastQuoteAt)}`;
  }
  const counter=$('#header-ticks');if(counter)counter.textContent=compact(s.tickCount||0);
  const history=$('#header-history');if(history){
    const loaded=s.historyLoaded||0,total=s.historyTarget||s.universeCount||198;
    history.innerHTML=`<span>20D HISTORY</span><b>${loaded} <i>/</i> ${total}</b>`;
    history.classList.toggle('history-ready',loaded>=total&&!s.historyLoading);
    history.title=`${loaded}/${total} equity baselines · ${s.historyLoading?'Loading…':'Finished current round'}${s.historyFailed?` · ${s.historyFailed} failed`:''}`;
  }
  const user=$('#header-user');if(user){
    const demo=s.mode==='demo';
    user.querySelector('.user-avatar').textContent=demo?'D':'V';
    user.querySelector('b').textContent=demo?'DEMO':'VIEWER';
    user.title=demo?'Synthetic demonstration data':'Live data viewer — no authentication is enabled';
  }
}
function renderMetrics(){
  renderHeader();
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
  $('#sectors').innerHTML=`<div class="column-heading"><span>SECTOR STRENGTH</span><span class="sector-swipe-hint">SWIPE TO FILTER →</span><span class="sector-desktop-change">CHANGE</span></div><div class="sector-rail" aria-label="Swipeable sector filters">${sectors.map(s=>`<button class="sector-row ${state.selection===s.name?'selected':''}" data-action="sector" data-sector="${esc(s.name)}" aria-pressed="${state.selection===s.name}">
  <div class="sector-meta"><span class="sector-name" title="${esc(s.name)}">${esc(s.name)}</span><span class="sector-change ${cls(s.change)}">${change(s.change)}</span></div><div class="sector-bars">${sectorBar(s)}</div></button>`).join('')}
    <button class="sect-all" data-action="sector" data-sector="ALL">${state.selection==='ALL'?'✓ ':''}Show all sectors</button></div>
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
    const mobileFlow=window.matchMedia('(max-width: 960px)').matches;
    const startY=mobileFlow?(50+(si+.5)*(H-100)/Math.max(1,sectors.length))
      :(row?Math.max(10,Math.min(H-10,(row.top+row.height/2-rect.top)*scale)):(43+si*43));
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
  const grid=Array.from({length:11},(_,i)=>`<line x1="0" x2="510" y1="${i*72}" y2="${i*72}" stroke="var(--flow-grid, #d6e8df)" stroke-width=".75" stroke-dasharray="2 7" opacity=".6"/>`).join('');
  const bars=Array.from({length:90},(_,i)=>{
    const bull=i<45,local=i%45;
    const y=(bull?36:397)+local*6.6;
    const dx=24+(hash('terminal'+i)%135);
    return `<line x1="${509-dx}" x2="509" y1="${y}" y2="${y}" stroke="${bull?'#33c884':'#e87790'}" stroke-width="${i%4===0?2.6:1.55}" opacity="${.13+(i%5)*.055}"/>`;
  }).join('');
  return `<svg class="flow-svg" viewBox="0 0 ${w} ${H}" preserveAspectRatio="none" role="img" aria-label="Animated green and red momentum connections flowing from sectors toward bullish and bearish stock lists">
    <defs>
      <linearGradient id="flow-wash-green" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="var(--flow-wash-start, #fff)" stop-opacity="0"/><stop offset="1" stop-color="var(--flow-wash-green, #c5f5db)" stop-opacity=".52"/></linearGradient>
      <linearGradient id="flow-wash-red" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="var(--flow-wash-start, #fff)" stop-opacity="0"/><stop offset="1" stop-color="var(--flow-wash-red, #ffd5de)" stop-opacity=".48"/></linearGradient>
    </defs>
    <rect x="0" y="0" width="510" height="355" fill="url(#flow-wash-green)"/>
    <rect x="0" y="356" width="510" height="364" fill="url(#flow-wash-red)"/>
    ${grid}<line x1="0" y1="358" x2="510" y2="358" stroke="var(--flow-middle, #c8dfd4)" stroke-width="1.7"/>
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
function freshShort(r){
  return ({ACTIVE:'ACTIVE',CONSOLIDATING:'SIDEWAYS',FADING:'FADING',WARMING_UP:'WARMUP',STALE:'STALE',DEMO:'DEMO'})[r.momentumState]||'WARMUP';
}
function freshBadge(r){
  const name=freshShort(r),kind=esc((r.momentumState||'WARMING_UP').toLowerCase());
  const reason=esc(r.freshReason||'Waiting for recent candles');
  const v20=r.rvol==null?'RVOL N/A':'RVOL '+rvolText(r.rvol);
  return `<span class="fresh-badge fresh-${kind}" title="${reason} · ${esc(v20)}">${name}</span>`;
}
function recentChange(v){return v==null?'—':change(v);}
/* Small volume-at-price *estimates* from OHLCV, NOT exchange depth or delta. */
function profileSVG(data, direction='bullish', width=140, height=95){
  const bars=Array.isArray(data?.bars)?data.bars:[];
  if(!bars.length)return `<span class="profile-unavailable">WAITING FOR 5M VOLUME</span>`;
  const bull=direction==='bullish';
  const color=bull?'#0da96f':'#d8506b',accent=bull?'#056d51':'#a92d4b';
  const pad=5, baseX=width-4, row=(height-11)/bars.length,maxW=width-15;
  const lines=bars.map((v,i)=>{
    const val=Math.min(1,Math.max(0,Number(v)||0));
    const y=pad+(bars.length-1-i)*row+row*.16;
    const w=Math.max(1.5,val*maxW);
    return `<rect x="${(baseX-w).toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(2,row*.58).toFixed(1)}" rx=".6" fill="${i===data.poc?accent:color}" opacity="${i===data.poc?.94:.25+.52*val}"/>`;
  }).join('');
  const poc=Number(data.poc);
  const pocY=Number.isInteger(poc)?pad+(bars.length-1-poc+.49)*row:null;
  return `<svg class="candle-profile-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Estimated traded-volume distribution from ${data.observations||0} five-minute candles; not order-book depth">
    <line x1="${baseX}" y1="2" x2="${baseX}" y2="${height-2}" stroke="${color}" opacity=".35" stroke-width="1"/>
    ${lines}${pocY!==null?`<line x1="4" x2="${baseX}" y1="${pocY.toFixed(1)}" y2="${pocY.toFixed(1)}" stroke="${accent}" stroke-width=".9" stroke-dasharray="3 3" opacity=".65"/>`:''}
  </svg>`;
}
function renderTile(r,dir){
  const bearish=dir==='bearish',build=String(r.buildup||'OI N/A').replace(' BUILD-UP',' BUILDUP');
  const source=state.snapshot?.mode==='demo'?'DEMO':freshShort(r);
  const sourceClass=source.toLowerCase().replace(/[^a-z]/g,'');
  const title=`${r.symbol} · ${r.sector} · ${change(r.change)} · ${build} · Momentum ${Number.isFinite(r.momentumScore100)?Math.round(r.momentumScore100):'N/A'} · OB Quality ${Number.isFinite(r.obQualityScore)?Math.round(r.obQualityScore):'N/A'} ${r.obState||'WAIT'} · 5m ${recentChange(r.recent5mPct)} · 15m ${recentChange(r.recent15mPct)} · 30m ${recentChange(r.recent30mPct)} · 1h ${recentChange(r.recent60mPct)}`;
  return `<div class="tv-card-wrap tv-leader-wrap"><button class="stock-tile terminal-stock-card leader-scored-card ${bearish?'red':''}" data-stock="${esc(r.symbol)}" aria-label="Open ${esc(r.symbol)} chart, ${change(r.change)}, Momentum ${Number.isFinite(r.momentumScore100)?Math.round(r.momentumScore100):'unavailable'}, Order Block quality ${Number.isFinite(r.obQualityScore)?Math.round(r.obQualityScore):'unavailable'}, ${esc(r.obState||'WAIT')}" title="${esc(title)}">
    <span class="terminal-tile-copy">
      <span class="tile-top"><span class="tile-sym">${esc(r.symbol)}</span><span class="leader-header-scores" aria-label="Momentum ${Number.isFinite(r.momentumScore100)?Math.round(r.momentumScore100):'not available'}; OB quality ${Number.isFinite(r.obQualityScore)?Math.round(r.obQualityScore):'not available'}; ${esc(r.obState||'WAIT')}">
        <span class="leader-head-score leader-head-mom" title="Momentum: rolling 5m + 15m + 30m + 1h, RVOL and VWAP"><small>MOM</small><b>${Number.isFinite(r.momentumScore100)?Math.round(r.momentumScore100):'—'}</b></span>
        <span class="leader-head-score leader-head-ob state-${esc((r.obState||'WAIT').toLowerCase())}" title="Order Block Quality: ${Number.isFinite(r.obQualityScore)?Math.round(r.obQualityScore):'not ready'} · ${esc(r.obState||'WAIT')}"><small>OB</small><b>${Number.isFinite(r.obQualityScore)?Math.round(r.obQualityScore):'—'}</b><i aria-hidden="true"></i></span>
      </span></span>
      <span class="tile-pct">${change(r.change)}</span>
      <span class="leader-tile-tags"><span class="terminal-buildup">${esc(build)}</span><span class="leader-momentum-state state-${esc(sourceClass)}" title="${esc(r.freshReason||'Momentum observation, not a trading instruction')}">${esc(source)}</span></span>
      <span class="leader-ob-caption" title="Order block state — a heuristic, not a trading signal">OB ${esc(r.obState||'WAIT')}</span>
      <span class="terminal-card-foot"><span title="Time-matched 20-day relative volume">RVOL ${rvolText(r.rvol)}</span><span title="Estimated volume-at-price from 5m candles">VOL ≈</span></span>
    </span>
    <span class="terminal-tile-profile" title="Estimated distribution of traded volume from recent 5-minute OHLCV candles; not actual depth">${profileSVG(r.candleProfile,dir,140,95)}</span>
  </button>${tradingViewLink(r.symbol,'leader')}</div>`;
}
function renderLane(dir){
  const rows=visibleStocks(dir); const bull=dir==='bullish',show=state.lane==='both'||state.lane===dir;
  if(!show) return '';
  return `<section class="lane ${bull?'bullish-lane':'bearish-lane'}" aria-label="Top 6 ${dir} momentum leaders">
    <div class="lane-top"><div class="lane-label ${bull?'':'red'}"><span class="little-arrow">${icon(bull?'arrowUp':'arrowDown','mini-ico')}</span><span class="lane-heading-text"><b>TOP 6 ${bull?'BULLISH':'BEARISH'}</b><span class="lane-sub"> / MOMENTUM LEADERS</span></span></div><span class="lane-num ${bull?'':'red'}">${String(rows.length).padStart(2,'0')} <span class="lane-total">/ 6</span></span></div>
    <div class="stock-grid">${rows.length?rows.map(r=>renderTile(r,dir)).join(''):`<div class="empty-panel">No ${dir} stocks in this filter.</div>`}</div></section>`;
}
function renderLanes(){
  $('#stock-lanes').innerHTML=renderLane('bullish')+renderLane('bearish');
  const only=state.lane!=='both';$('#stock-lanes').classList.toggle('single-lane',only);
  for(const l of ['all','bullish','bearish']){
    const node=$(`#filter-${l}`);if(node)node.classList.toggle('active',(l==='all'?'both':l)===state.lane);
  }
}
function momentumScoreBadge(r,compact=false){
  const number=Number.isFinite(r.momentumScore100) ? Math.round(r.momentumScore100) : null;
  const basis=r.momentumScoreBasis||'NOT_READY';
  const simulated=basis==='DEMO';
  const tag=compact?(simulated?'DEMO':'MOM'):(simulated?'SIMULATED':'MOMENTUM');
  const scoreClass=number===null?'unrated':number>=80?'high':number>=55?'medium':'low';
  const description=simulated?'Simulated showcase only — not live Kite market data':
    number===null?'Live momentum data unavailable or stale':
    `${number} momentum-strength points; ${basis==='LIVE_WATCH'?'watch candidate, NOT a confirmed setup':basis==='LIVE_WARMUP'?'provisional, waiting for enough same-session quotes':basis==='LIVE_20D'?'complete 20-session baseline':'partial 20-session baseline'}. Not a win probability`;
  return `<span class="momentum-rating score-${scoreClass}${simulated?' score-demo':''}" aria-label="${esc(description)}" title="${esc(description)}">
    <span class="momentum-rating-number">${number===null?'—':number}</span>
    <span class="momentum-rating-label">${tag}</span></span>`;
}
function obQualityBadge(r){
  const score=Number.isFinite(r.obQualityScore)?Math.round(r.obQualityScore):null;
  const status=['READY','WAIT','INVALID'].includes(r.obState)?r.obState:'WAIT';
  const desc=score===null?'No validated, completed-candle order block available':
    `${score} order-block quality points out of 100; ${status}. ${r.obReason||''} Not a success probability.`;
  return `<span class="ob-quality-chip state-${status.toLowerCase()}" title="${esc(desc)}" aria-label="${esc(desc)}"><strong>${score===null?'—':score}</strong><span>OB · ${status}</span></span>`;
}
function renderPick(r,idx,dir){
  const bull=dir==='bullish';
  const status=state.snapshot?.mode==='demo'?'DEMO':(r.pickStatus||(r.pickEligible?'ACTIVE':freshShort(r)));
  const ready=status==='ACTIVE'&&r.pickEligible&&state.snapshot?.marketStatus==='LIVE';
  const statusClass=ready?'active':status.startsWith('WATCH')?'watch':status.toLowerCase().replace(/[^a-z]/g,'');
  return `<div class="tv-card-wrap tv-pick-wrap"><button class="pick-card terminal-pick-card ${bull?'':'red'}" data-stock="${esc(r.symbol)}" title="Open ${esc(r.symbol)} chart · ${esc(r.buildup||'OI N/A')} · 5m / 15m / 30m / 1h">
     <div class="pick-row"><span class="pick-ticker">${esc(r.symbol)}</span><span class="pick-score-cluster">${momentumScoreBadge(r)}${obQualityBadge(r)}</span></div>
     <div class="terminal-pick-middle">
       <div class="terminal-pick-stat"><div class="pick-big">${change(r.change)}</div><span class="terminal-pick-buildup">${esc(r.buildup||'OI UNAVAILABLE')}</span><span class="terminal-pick-sector">${esc(r.sector)}</span><span class="candidate-status status-${esc(statusClass)}" title="${esc(r.freshReason||'Candidate, not automatic entry')}">${esc(status)}</span></div>
       <div class="terminal-pick-profile">${profileSVG(r.candleProfile,dir,132,95)}</div>
     </div>
     <div class="terminal-pick-footer"><span title="Current-session return from opening price">OPEN ${recentChange(r.sessionMovePct)}</span><span>RVOL ${rvolText(r.rvol)}</span><span>5m ${recentChange(r.recent5mPct)}</span><span>15m ${recentChange(r.recent15mPct)}</span><span>30m ${recentChange(r.recent30mPct)}</span><span>1h ${recentChange(r.recent60mPct)}</span></div>
  </button>${tradingViewLink(r.symbol,'pick')}</div>`;
}
function renderPicks(){
  // Backend provides sector-diversified candidates. In filter mode retain up to
  // three ranked stocks even if all are sideways, still warming up, or fading.
  // The badge conveys confirmation separately; do NOT turn WATCH into READY.
  const side=dir=>{
    const full=(state.selection==='ALL'&&!state.watchOnly);
    let selected;
    if(full){
      selected=(state.snapshot?.[dir+'Picks']||[]).slice(0,3);
    }else{
      const ranked=[...visibleStocks(dir)].sort((a,b)=>(b.pickEligible?1:0)-(a.pickEligible?1:0)||
        (b.adaptiveRankScore||0)-(a.adaptiveRankScore||0));
      selected=[];const used=new Set();
      for(const r of ranked){if(!used.has(r.sector)){selected.push(r);used.add(r.sector)}if(selected.length===3)break}
      for(const r of ranked){if(selected.length===3)break;if(!selected.some(x=>x.symbol===r.symbol))selected.push(r)}
    }
    return `<div class="pick-group"><div class="pick-heading"><span class="${dir==='bullish'?'positive':'negative'}">${dir==='bullish'?'↑ BULLISH PICKS':'↓ BEARISH PICKS'}</span><b>${selected.length} / 3 <small title="Ranked candidates, not necessarily confirmed entries">CANDIDATES</small></b></div>
    ${selected.length?selected.map((r,i)=>renderPick(r,i,dir)).join(''):`<div class="empty-panel">No ${dir} stock quotes available yet</div>`}</div>`;
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
  state.drawer=symbol;state.detail=null;state.chartView=null;state.chartFollowLive=true;showDrawerLoading(symbol);
  await loadSymbolDetail(symbol);
}
function switchTimeframe(tf){
  if(!TIMEFRAMES[tf]||tf===state.timeframe||!state.drawer)return;
  state.timeframe=tf;state.detail=null;state.chartView=null;state.chartFollowLive=true;
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
function closeOverlay(){state.detailRequestId++;state.drawer=null;state.detail=null;state.chartView=null;state.chartFollowLive=true;state.modal=null;$('#overlay-root').innerHTML='';document.body.style.overflow='';}
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
  <aside class="detail-drawer premium-chart-v3 ${bull?'direction-bullish':'direction-bearish'}" role="dialog" aria-modal="true" aria-label="${esc(d.symbol)} price and order block detail">
    <div class="detail-head"><div class="detail-headbar"><div class="breadcrumb"><span class="route-kicker">MARKET INTELLIGENCE</span><span class="route-divider">/</span> NSE / ${esc(d.sector)} ${icon('chevronRight','mini-ico')} <b>${esc(d.symbol)}</b> <span>· ${esc(state.timeframe.toUpperCase())}</span></div>
      <div class="detail-head-actions"><button class="icon-btn" data-action="watch" data-symbol="${esc(d.symbol)}" title="${watched?'Remove from':'Add to'} watchlist" style="color:${watched?'#138957':'#7c9388'}">${icon('bookmark')}</button>
      <button class="icon-btn" data-action="previous" title="Previous stock">${icon('chevronLeft')}</button><button class="icon-btn" data-action="next" title="Next stock">${icon('chevronRight')}</button><button class="icon-btn" data-action="close-overlay" title="Close details">${icon('x')}</button></div></div>
    <div class="detail-symbol-row"><div class="detail-company"><div class="detail-icon">${esc(d.symbol.slice(0,2))}</div><div><h2>${esc(d.symbol)}</h2><small>${esc(d.sector)} · NSE CASH / F&O WATCHLIST</small></div></div>
      <div class="detail-price"><b>${price(d.price)}</b><span class="${cls(d.change)}">${change(d.change)} ${bull?'↗':'↘'}</span></div></div></div>
    <div class="detail-content">
     <div class="signal-strip">
       <div class="signal-metric metric-rank"><small>SCAN RANK</small><strong>#${d.rank}<span style="color:#9bb1a3;font-size:10px"> / ${d.of}</span></strong><small class="explain">${bull?'BULLISH':'BEARISH'} POOL</small></div>
       <div class="signal-metric metric-oi"><small>${d.oiSource?.startsWith('NFO')?'FUTURES OI Δ':'OPEN INTEREST Δ'}</small><strong class="${cls(d.oiChange)}">${change(d.oiChange)}</strong><small class="explain">${d.oiSource?.startsWith('NFO')?'NFO FUT · SINCE CONNECT':(d.oi==null?'NO FUTURES OI':'OI REFERENCE')}</small></div>
       <div class="signal-metric metric-action"><small>PRICE ACTION</small><strong style="font-size:11px;margin-top:7px">${esc(d.buildup)}</strong><small class="explain">OI + PRICE</small></div>
       <div class="signal-metric metric-rvol"><small>RELATIVE VOLUME</small><strong>${rvolText(d.rvol)}</strong><small class="explain">${d.rvol==null?(state.snapshot?.mode==='demo'?'DEMO RVOL':'20D HISTORY PENDING'):'MATCHED TIME · 20D'}</small></div>
     </div>
     <div class="ob-intel-bar" role="status" aria-label="Order block setup evaluation">
       <div class="ob-score-large"><small>OB QUALITY</small><b>${d.obQualityScore==null?'—':Math.round(d.obQualityScore)}</b></div>
       <span class="ob-intel-state state-${esc((d.obState||'WAIT').toLowerCase())}">${esc(d.obState||'WAIT')}</span>
       <span class="ob-intel-reason">${esc(d.obReason||'Waiting for structure confirmation')}</span>
       <small class="ob-intel-basis">${d.obBasis==='SIMULATED'?'SIMULATED · ':''}CANDLE-DERIVED · NO TRUE DELTA</small>
     </div>
     <section class="detail-card chart-panel"><div class="chart-top"><div class="chart-heading"><div class="chart-kicker"><span class="chart-status-dot"></span> MARKET STRUCTURE <span class="chart-kicker-separator">/</span> ANALYTICS 01</div><div class="chart-title">${icon('activity')} Price action & order blocks</div><div class="chart-meta">${esc(d.symbol)} · ${tfDescription(d.timeframe)} candles · ${state.snapshot?.mode==='demo'?'SIMULATED':(state.snapshot?.mode==='kite'?'ZERODHA KITE':'EXTERNAL INGEST')} DATA${state.snapshot?.mode==='kite'?` · ${d.historySessions||0}/20 SESSIONS · ADR20 ${d.adr20==null?'—':fmt(d.adr20)+'%'} · ATR14 ${d.atr14==null?'—':price(d.atr14)} · 20D HIGH ${d.high20==null?'—':price(d.high20)} / LOW ${d.low20==null?'—':price(d.low20)}`:''}</div><span class="chart-source-badge ${state.snapshot?.mode==='demo'?'is-demo':'is-data'}">${state.snapshot?.mode==='demo'?'SIMULATED':state.snapshot?.mode==='kite'?'KITE CONNECT':'EXTERNAL FEED'}</span></div><div class="chart-toolbar"><div class="chart-actions" role="group" aria-label="Candlestick chart timeframe">${Object.keys(TIMEFRAMES).map(tf=>`<button type="button" class="timeframe ${state.timeframe===tf?'active':''}" data-action="timeframe" data-tf="${tf}" aria-pressed="${state.timeframe===tf}" title="View ${tfDescription(tf)} candles">${tf}</button>`).join('')}</div><div class="chart-viewport-actions" role="group" aria-label="Candlestick chart zoom and navigation"><button type="button" class="chart-nav-btn" data-action="chart-zoom-in" title="Zoom in (+)" aria-label="Zoom in">${icon('plus')}</button><button type="button" class="chart-nav-btn" data-action="chart-zoom-out" title="Zoom out (−)" aria-label="Zoom out">${icon('minus')}</button><button type="button" class="chart-nav-btn" data-action="chart-fit" title="Fit all loaded candles (Home)" aria-label="Fit all candles">${icon('expand')}</button><button type="button" class="chart-nav-btn chart-live-btn" data-action="chart-live" title="Auto-follow enabled; use after panning history (End)" aria-label="Chart auto-follow enabled" aria-pressed="true">● AUTO</button>${tradingViewLink(d.symbol,'chart')}</div></div></div>
       <div class="chart-wrap" id="chart-wrap" tabindex="0" aria-label="Interactive candlestick chart. Mouse wheel zooms, drag pans, pinch zooms on mobile, plus and minus keys zoom, arrows pan, Home fits all and End jumps to latest.">${chartSVG(d)}</div>
       <div class="chart-interaction-hint"><span>${icon('crosshair')} Scroll to zoom · Drag to pan · Pinch on touch</span><span id="chart-view-label" aria-live="off">${chartWindowLabel(d)}</span></div>
       <div class="chart-legend"><span><i class="squarekey"></i> Candidate OB</span><span><i class="linekey"></i> ${indicatorButton('vwap','VWAP',state.showVwap)}</span><span><i class="linekey green"></i> ${indicatorButton('levels','S/R levels',state.showLevels)}</span><span><i class="linekey pink"></i> ${indicatorButton('ob','OB + BOS / FVG / Sweep',state.showOB)}</span><span title="Uniformly spread 5m candle volume across its high-low range; an approximation, not market depth">VOL PROFILE ≈ (5M CANDLE EST.)</span></div>
     </section>
     <div class="detail-bottom"><section class="detail-card structure"><h3>Signal breakdown</h3>
        <div class="structure-row"><span>Momentum state</span><b>${esc(d.momentumState||'WARMING_UP')}</b></div><div class="structure-row"><span>5m / 15m / 30m / 1h</span><b>${recentChange(d.recent5mPct)} / ${recentChange(d.recent15mPct)} / ${recentChange(d.recent30mPct)} / ${recentChange(d.recent60mPct)}</b></div><div class="structure-row"><span>Rolling windows ready</span><b>${d.momentumWindowsReady||0} / 4 (same-session)</b></div><div class="structure-row"><span>Momentum strength</span><b>${d.momentumScore100==null?'NOT READY':`${d.momentumScore100}`}${d.momentumScoreBasis==='DEMO'?' · SIMULATED':''}</b></div><div class="structure-row"><span>Raw freshness (internal)</span><b>${fmt(d.freshScore,1)}</b></div><div class="structure-row"><span>Daily strength score</span><b>${fmt(d.score,1)}</b></div><div class="structure-row"><span>Intraday change</span><b class="${cls(d.change)}">${change(d.change)}</b></div><div class="structure-row"><span>Relative volume</span><b>${rvolText(d.rvol)}</b></div><div class="structure-row"><span>Above / below VWAP</span><b>${d.price>=d.vwap?'ABOVE ↑':'BELOW ↓'}</b></div></section>
       <section class="detail-card structure"><h3>Order-block lens</h3><div class="structure-row"><span>Timeframe</span><b>${esc(d.timeframe.toUpperCase())}</b></div><div class="structure-row"><span>Candidate zones</span><b>${d.blocks.length}</b></div>
       <div class="structure-row"><span>OB quality</span><b>${d.obQualityScore==null?'NOT READY':d.obQualityScore}</b></div>
       <div class="structure-row"><span>Setup state</span><b>${esc(d.obState||'WAIT')}</b></div>
       <div class="structure-row"><span>Validated zone</span><b>${d.obZone?`${esc(d.obZone.type.toUpperCase())} · ${fmt(d.obZone.low)}–${fmt(d.obZone.high)}`:'NONE'}</b></div>
       <div class="structure-row"><span>BOS / FVG / SWEEP</span><b>${d.obBos?'YES':'—'} / ${d.obFvg?'YES':'—'} / ${d.obSweep?'YES':'—'}</b></div>
       <div class="structure-row"><span>Delta</span><b>UNAVAILABLE (KITE)</b></div>
       <div class="structure-row"><span>VWAP source</span><b>CANDLE VOLUME</b></div></section></div>
     <div class="method-note">${icon('info')}<span>Educational scanner. BOS, FVG, liquidity sweeps and OB rectangles are derived from completed candlesticks, <strong>not</strong> verified institutional limit orders. A READY state is a heuristic observation, <strong>not</strong> a buy/sell instruction or win probability. Kite quotes cannot establish true aggressor-side delta. S/R levels are calculated reference levels, not trade instructions. ${state.snapshot?.mode==='demo'?'Prices shown here are generated demo data, not live NSE market data.':(state.snapshot?.mode==='kite'?'NSE cash prices come from Kite; futures OI, when available, measures change since this app connected (not since previous close). The last quote timestamp may be from a previous session when the exchange is closed. The current candle can be incomplete.':'Prices reflect data supplied to your configured ingestion endpoint.')}</span></div>
    </div>
  </aside>`;
  wireChartHover();
}
function indicatorButton(key,label,active){return `<button data-action="indicator" data-key="${key}" style="border:0;padding:1px 2px;background:none;color:${active?'#597468':'#b8c5bc'};font:700 9px var(--mono);text-decoration:${active?'none':'line-through'}" title="Toggle ${label}">${label}</button>`}

// Interactive viewport is stored in candle indexes (not SVG pixels). This ensures
// candlesticks, volume, VWAP, OB/FVG/BOS annotations and tooltips share one scale.
const chartClamp=(value,lo,hi)=>Math.max(lo,Math.min(hi,value));
function chartWindow(d){
  const total=d?.candles?.length||0;if(!total)return {start:0,end:0,total:0};
  const mobile=window.matchMedia('(max-width: 600px)').matches;
  const previous=state.chartView;
  if(!previous||previous.symbol!==d.symbol||previous.timeframe!==state.timeframe){
    const end=total, width=Math.min(total,mobile?50:70);
    state.chartFollowLive=true; // opening a chart or changing timeframe follows the latest by default
    state.chartView={symbol:d.symbol,timeframe:state.timeframe,start:end-width,end,total};
  }else{
    const width=chartClamp(previous.end-previous.start,1,total);
    // Keep following newly completed candles as Kite refreshes every 15 seconds.
    // History panning explicitly disables this until the user returns to the edge.
    const end=state.chartFollowLive?total:chartClamp(previous.end,width,total);
    state.chartView={...previous,start:chartClamp(end-width,0,total-width),end,total};
  }
  return state.chartView;
}
function chartWindowLabel(d){
  const view=chartWindow(d);
  return `${view.end-view.start} / ${view.total} candles · ${state.chartFollowLive?'AUTO-FOLLOW':'HISTORY VIEW'}`;
}
function renderChartOnly(){
  if(!state.detail||!state.drawer)return;
  const wrap=$('#chart-wrap');if(!wrap)return;
  wrap.innerHTML=chartSVG(state.detail);updateChartButtons();
}
function updateChartButtons(){
  const view=state.chartView;if(!view)return;
  const label=$('#chart-view-label');if(label)label.textContent=chartWindowLabel(state.detail);
  const zoomIn=$('[data-action="chart-zoom-in"]'),zoomOut=$('[data-action="chart-zoom-out"]');
  const min=Math.min(5,view.total),count=view.end-view.start;
  if(zoomIn)zoomIn.disabled=count<=min;
  if(zoomOut)zoomOut.disabled=count>=view.total;
  const live=$('[data-action="chart-live"]');
  if(live){
    live.classList.toggle('at-live-edge',state.chartFollowLive);
    live.textContent=state.chartFollowLive?'● AUTO':'↗ LIVE';
    live.title=state.chartFollowLive?'Automatically following new candles; drag left to examine history':'Return to latest candles and resume auto-follow';
    live.setAttribute('aria-pressed',String(state.chartFollowLive));
    live.setAttribute('aria-label',state.chartFollowLive?'Chart auto-follow enabled':'Resume following latest candles');
  }
}
function chartZoom(factor,anchor=.5){
  if(!state.detail)return;
  const v=chartWindow(state.detail),min=Math.min(5,v.total),oldCount=v.end-v.start;
  const count=chartClamp(Math.round(oldCount*factor),min,v.total);
  if(count===oldCount)return false;
  const a=chartClamp(anchor,0,1);
  const anchorIndex=v.start+a*oldCount;
  const start=chartClamp(Math.round(anchorIndex-a*count),0,v.total-count);
  state.chartView={...v,start,end:start+count};
  if(state.chartFollowLive){state.chartView.end=v.total;state.chartView.start=v.total-count;}
  renderChartOnly();return true;
}
function chartPan(steps){
  if(!state.detail)return;
  const v=chartWindow(state.detail),count=v.end-v.start;
  const start=chartClamp(v.start+Math.round(steps),0,v.total-count);
  if(start===v.start)return;
  state.chartView={...v,start,end:start+count};
  state.chartFollowLive=state.chartView.end>=v.total;
  renderChartOnly();
}
function chartFit(){
  if(!state.detail)return;
  const v=chartWindow(state.detail);state.chartFollowLive=true;state.chartView={...v,start:0,end:v.total};renderChartOnly();
}
function chartGoLive(){
  if(!state.detail)return;
  const v=chartWindow(state.detail),count=Math.min(v.total,Math.max(10,Math.min(v.end-v.start,50)));
  state.chartFollowLive=true;state.chartView={...v,start:v.total-count,end:v.total};renderChartOnly();
}

function chartSVG(d){
  const allCandles=d.candles||[];if(!allCandles.length)return `<div class="skeleton">No ${tfDescription(d.timeframe)} candles received.</div>`;
  const mobileChart=window.matchMedia('(max-width: 600px)').matches;
  const view=chartWindow(d),startIndex=view.start,endIndex=view.end;
  const candles=allCandles.slice(startIndex,endIndex);
  const blocks=(d.blocks||[]).filter(b=>b.toIndex>=startIndex&&b.fromIndex<endIndex).map(b=>({...b,
    fromIndex:Math.max(0,b.fromIndex-startIndex),toIndex:Math.min(endIndex-1,b.toIndex)-startIndex}));
  const W=mobileChart?480:1160,H=mobileChart?530:505,L=mobileChart?43:54,R=mobileChart?67:80,T=26,B=45,volH=mobileChart?67:70;
  const chartBottom=H-B-volH,innerW=W-L-R;
  // Autoscale to the *visible* candles; distant levels must not flatten candles.
  const cLow=Math.min(...candles.map(c=>c.low)),cHigh=Math.max(...candles.map(c=>c.high));
  const vicinity=Math.max(cHigh-cLow,cHigh*.003);
  const extra=state.showLevels?Object.values(d.levels).filter(n=>typeof n==='number'&&n>=cLow-vicinity&&n<=cHigh+vicinity):[];
  const zoneValues=state.showOB?blocks.filter(b=>b.low>=cLow-vicinity&&b.high<=cHigh+vicinity):[];
  const lows=candles.map(c=>c.low).concat(zoneValues.map(b=>b.low),extra),highs=candles.map(c=>c.high).concat(zoneValues.map(b=>b.high),extra);
  let min=Math.min(...lows),max=Math.max(...highs);const pad=Math.max((max-min)*.09,d.price*.0025);min-=pad;max+=pad;
  const yp=p=>T+(max-p)/Math.max(.01,max-min)*(chartBottom-T);
  const dx=innerW/candles.length,cx=i=>L+(i+.5)*dx;
  const vmax=Math.max(1,...candles.map(c=>c.volume));
  const horizontal=Array.from({length:5},(_,i)=>{const value=max-(max-min)*i/4,y=yp(value);return `<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="var(--chart-grid,#eaf0eb)" stroke-width="1"/><text x="${W-R+9}" y="${y+4}" fill="var(--chart-axis,#a0afa6)" font-size="10" font-family="monospace">${fmt(value)}</text>`;}).join('');
  const multiDay=candles.length>1&&candles[0].time.slice(0,10)!==candles.at(-1).time.slice(0,10);
  const vertical=Array.from({length:mobileChart?4:7},(_,i)=>{const n=mobileChart?3:6,idx=Math.floor(i*(candles.length-1)/n),x=cx(idx),dt=new Date(candles[idx].time);const time=dt.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Kolkata',hour12:false});const label=multiDay?dt.toLocaleDateString('en-IN',{day:'2-digit',month:'short',timeZone:'Asia/Kolkata'})+' '+time:time;return `<line x1="${x}" x2="${x}" y1="${T}" y2="${H-B}" stroke="var(--chart-grid-secondary,#f1f5f1)" stroke-width="1"/><text x="${x}" y="${H-11}" fill="var(--chart-axis,#9caea2)" text-anchor="middle" font-family="monospace" font-size="9">${esc(label)}</text>`;}).join('');
  const qualityZone=d.obZone;
  // Candle-volume approximation rendered translucently at the far right of the plot;
  // never presented as exchange depth, resting liquidity or true delta.
  const profileBars=(()=>{
    const profile=d.candleProfile,raw=profile?.bars;
    if(!Array.isArray(raw)||!raw.length||profile.low==null||profile.high==null)return '';
    const step=(profile.high-profile.low)/raw.length;
    const xRight=W-R-4,span=Math.min(108,innerW*.105),color=d.change>=0?'#22b47e':'#de6d89';
    const labels=raw.map((fraction,i)=>{
      const p=profile.low+(i+.5)*step,y=yp(p),width=span*Math.max(0,Math.min(1,Number(fraction)||0));
      if(y<T||y>chartBottom||width<.6)return '';
      return `<rect x="${xRight-width}" y="${y-1.7}" width="${width}" height="3.4" fill="${color}" opacity=".28"/>`;
    }).join('');
    const pocY=yp(profile.pocPrice);
    return `<g pointer-events="none" aria-label="Estimated candle volume by price; not market depth">${labels}${pocY>=T&&pocY<=chartBottom?`<line x1="${xRight-span}" x2="${xRight}" y1="${pocY}" y2="${pocY}" stroke="${color}" stroke-dasharray="3 3" opacity=".56"/>`:''}</g>`;
  })();
  const confirmedOB=(state.showOB&&qualityZone&&qualityZone.fromIndex<endIndex&&qualityZone.toIndex>=startIndex)?(()=>{
    const x=cx(Math.max(startIndex,qualityZone.fromIndex)-startIndex)-dx/2;
    const y=yp(qualityZone.high);const h=Math.max(3,yp(qualityZone.low)-y);
    const col=qualityZone.type==='bullish'?'#16a16b':'#d95776';
    return `<rect x="${x}" y="${y}" width="${W-R-x}" height="${h}" fill="${col}" fill-opacity=".07" stroke="${col}" stroke-width="1.6" stroke-dasharray="5 3"/>
      <text x="${W-R-6}" y="${Math.max(T+11,y-5)}" text-anchor="end" font-size="10" font-weight="800" fill="${col}">QUALITY OB ${d.obQualityScore==null?'—':d.obQualityScore} · ${esc(d.obState)}</text>`;
  })():'';
  const fvgBand=(state.showOB&&d.obFvg&&d.obFvg.fromIndex<endIndex&&d.obFvg.toIndex>=startIndex)?(()=>{
    const z=d.obFvg;const left=cx(Math.max(startIndex,z.fromIndex)-startIndex)-dx/2;
    const col=z.type==='bullish'?'#1ea5ba':'#c27ad4';
    return `<rect x="${left}" y="${yp(z.high)}" width="${Math.min(W-R-left,dx*18)}" height="${Math.max(2,yp(z.low)-yp(z.high))}" fill="${col}" fill-opacity=".11" stroke="${col}" stroke-dasharray="2 4"/><text x="${left+3}" y="${Math.max(16,yp(z.high)-4)}" font-size="9" fill="${col}" font-weight="800">FVG</text>`;
  })():'';
  const bosGuide=(state.showOB&&d.obBos&&d.obBos.index>=startIndex&&d.obBos.index<endIndex)?(()=>{
    const x=cx(d.obBos.index-startIndex),y=yp(d.obBos.price);
    return `<line x1="${x}" x2="${Math.min(W-R,x+dx*12)}" y1="${y}" y2="${y}" stroke="#538cb7" stroke-width="1.4" stroke-dasharray="4 3"/><text x="${x+2}" y="${Math.max(15,y-5)}" font-size="9" fill="#538cb7" font-weight="800">BOS</text>`;
  })():'';
  const sweepGuide=(state.showOB&&d.obSweep&&d.obSweep.index>=startIndex&&d.obSweep.index<endIndex)?(()=>{
    const x=cx(d.obSweep.index-startIndex),y=yp(d.obSweep.price);
    return `<circle cx="${x}" cy="${y}" r="5" fill="none" stroke="#edaa36" stroke-width="2"/><text x="${x+7}" y="${Math.max(15,y-7)}" font-size="9" fill="#d9971b" font-weight="800">SWEEP</text>`;
  })():'';
  const ob=state.showOB?blocks.map((b,i)=>{const left=cx(b.fromIndex)-dx/2,right=cx(Math.min(b.toIndex,candles.length-1))+dx/2;const top=Math.max(T,yp(b.high)),bottom=Math.min(chartBottom,yp(b.low));const bull=b.type==='bullish';
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
  const liveLabel=(ly>=T-10&&ly<=chartBottom+10)?`<line x1="${L}" x2="${W-R}" y1="${ly}" y2="${ly}" stroke="${d.change>=0?'#27b879':'#d77483'}" stroke-width="1.1" stroke-dasharray="2 3"/><rect x="${W-R}" y="${ly-11}" width="${R}" height="22" rx="3" fill="${d.change>=0?'#1c9864':'#ce6d7b'}"/><text x="${W-R+R/2}" y="${ly+4}" text-anchor="middle" fill="white" font-size="11" font-family="monospace" font-weight="bold">${fmt(d.price)}</text>`:'';
  return `<svg id="ohlc-chart" class="chart-svg" data-start-index="${startIndex}" data-end-index="${endIndex}" data-chart-width="${W}" data-chart-left="${L}" data-chart-right="${R}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" aria-label="${esc(d.symbol)} ${tfDescription(d.timeframe)} OHLC candlestick chart with VWAP and candidate order blocks" role="img">
    <defs><clipPath id="plot-clip"><rect x="${L}" y="${T}" width="${innerW}" height="${H-T-B}"/></clipPath></defs>
    <rect width="${W}" height="${H}" fill="var(--chart-bg,#fff)"/><g>${horizontal}${vertical}</g>
    <line x1="${L}" x2="${W-R}" y1="${chartBottom}" y2="${chartBottom}" stroke="var(--chart-grid,#dae7df)" stroke-width="1"/>
    <g clip-path="url(#plot-clip)">${ob}${confirmedOB}${fvgBand}${bosGuide}${sweepGuide}${levels}${profileBars}${candlesSVG}${vwap}</g>${liveLabel}
    <line id="crosshair-v" x1="0" x2="0" y1="${T}" y2="${H-B}" stroke="var(--chart-axis,#8ca29a)" stroke-width="1" stroke-dasharray="3 3" style="display:none;pointer-events:none"/>
    <rect id="chart-hit" x="${L}" y="${T}" width="${innerW}" height="${H-T-B}" fill="transparent" data-candles="${candles.length}"/>
    <text x="${L+5}" y="${T+13}" fill="var(--chart-axis,#91a59a)" font-size="9" font-family="monospace">NSE · ${esc(d.timeframe)}</text>
    <text x="${L+5}" y="${chartBottom+14}" fill="var(--chart-axis,#98a9a0)" font-size="9" font-family="monospace">VOL</text>
  </svg><div id="chart-tooltip" class="chart-tooltip" style="display:none"></div>`;
}
function wireChartHover(){
  const wrap=$('#chart-wrap');if(!wrap)return;
  updateChartButtons();
  const pointers=new Map();let panRemainder=0,dragged=false,lastDistance=null,lastMidpoint=null;
  function dims(){
    const svg=wrap.querySelector('#ohlc-chart');if(!svg)return null;
    const rect=svg.getBoundingClientRect();const L=Number(svg.dataset.chartLeft||54),R=Number(svg.dataset.chartRight||80),W=Number(svg.dataset.chartWidth||1160);
    const matrix=svg.getScreenCTM();if(!matrix)return null;
    return {svg,rect,L,R,W,matrix};
  }
  function percentAt(clientX){
    const v=dims();if(!v)return .5;
    const p=v.svg.createSVGPoint();p.x=clientX;p.y=v.rect.top+v.rect.height/2;
    const x=p.matrixTransform(v.matrix.inverse()).x;
    return chartClamp((x-v.L)/(v.W-v.R-v.L),0,1);
  }
  function showCrosshair(e){
    const v=dims();if(!v)return;
    const point=v.svg.createSVGPoint();point.x=e.clientX;point.y=e.clientY;
    const x=point.matrixTransform(v.matrix.inverse()).x;
    const line=wrap.querySelector('#crosshair-v'),tooltip=wrap.querySelector('#chart-tooltip');
    if(!line||!tooltip)return;
    const view=chartWindow(state.detail),n=view.end-view.start;
    if(x<v.L||x>v.W-v.R||!n){tooltip.style.display='none';line.style.display='none';return;}
    const localIndex=chartClamp(Math.floor((x-v.L)/(v.W-v.R-v.L)*n),0,n-1),c=state.detail.candles[view.start+localIndex];
    if(!c)return;
    const xx=v.L+(localIndex+.5)*(v.W-v.R-v.L)/n;
    line.setAttribute('x1',xx);line.setAttribute('x2',xx);line.style.display='block';
    const dt=new Date(c.time).toLocaleString('en-IN',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Kolkata'});
    tooltip.style.display='block';tooltip.innerHTML=`<b>${esc(dt)}</b> &nbsp; O ${fmt(c.open)} &nbsp; H ${fmt(c.high)} &nbsp; L ${fmt(c.low)} &nbsp; C <span style="color:${c.close>=c.open?'#219a64':'#d17280'}">${fmt(c.close)}</span><br/>VOL ${compact(c.volume)} &nbsp; VWAP ${fmt(c.vwap)}`;
    tooltip.style.left=`${Math.max(8,Math.min(Math.max(8,v.rect.width-280),v.rect.width*.10))}px`;
  }
  function clearCrosshair(){const tip=wrap.querySelector('#chart-tooltip'),line=wrap.querySelector('#crosshair-v');if(tip)tip.style.display='none';if(line)line.style.display='none';}
  wrap.addEventListener('wheel',e=>{
    if(!state.detail||!e.target.closest('#ohlc-chart'))return;
    e.preventDefault();chartZoom(e.deltaY<0?.8:1.25,percentAt(e.clientX));clearCrosshair();
  },{passive:false});
  wrap.addEventListener('dblclick',e=>{if(e.target.closest('#ohlc-chart')){e.preventDefault();chartFit();clearCrosshair();}});
  wrap.addEventListener('pointerdown',e=>{
    if(!state.detail||!e.target.closest('#ohlc-chart')||e.button!==0)return;
    e.preventDefault();wrap.focus({preventScroll:true});
    try{wrap.setPointerCapture(e.pointerId)}catch(_e){}
    pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
    dragged=false;panRemainder=0;
    if(pointers.size===2){const a=[...pointers.values()];lastDistance=Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y);lastMidpoint=(a[0].x+a[1].x)/2;}
    wrap.classList.add('is-chart-dragging');
  });
  wrap.addEventListener('pointermove',e=>{
    if(!pointers.has(e.pointerId)){
      if(e.pointerType==='mouse'&&!pointers.size)showCrosshair(e);return;
    }
    e.preventDefault();
    const prev=pointers.get(e.pointerId);pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
    if(pointers.size===2){
      const a=[...pointers.values()],distance=Math.max(1,Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y)),middle=(a[0].x+a[1].x)/2;
      if(lastDistance!=null&&Math.abs(distance-lastDistance)>1){
        if(chartZoom(lastDistance/distance,percentAt(middle))){lastDistance=distance;dragged=true;}
      }
      if(lastMidpoint!=null){const v=state.chartView,rect=dims()?.rect;
        if(v&&rect){const steps=Math.round((lastMidpoint-middle)/Math.max(rect.width,1)*(v.end-v.start));if(steps){chartPan(steps);dragged=true;}}
      }
      lastMidpoint=middle;
    }else if(pointers.size===1){
      const v=state.chartView,rect=dims()?.rect;
      if(v&&rect){panRemainder+=(prev.x-e.clientX)/Math.max(rect.width,1)*(v.end-v.start);
        const steps=Math.trunc(panRemainder);
        if(steps){chartPan(steps);panRemainder-=steps;dragged=true;}
      }
    }
    if(dragged)clearCrosshair();
  });
  function stop(e){
    pointers.delete(e.pointerId);
    try{if(wrap.hasPointerCapture(e.pointerId))wrap.releasePointerCapture(e.pointerId)}catch(_e){}
    lastDistance=null;lastMidpoint=null;panRemainder=0;
    if(!pointers.size){wrap.classList.remove('is-chart-dragging');if(e.pointerType!=='mouse')clearCrosshair();}
  }
  wrap.addEventListener('pointerup',stop);wrap.addEventListener('pointercancel',stop);
  wrap.addEventListener('pointerleave',e=>{if(!pointers.size)clearCrosshair();});
  wrap.addEventListener('keydown',e=>{
    if(e.key==='+'||e.key==='='){e.preventDefault();chartZoom(.75);}
    else if(e.key==='-'||e.key==='_'){e.preventDefault();chartZoom(1.35);}
    else if(e.key==='ArrowLeft'){e.preventDefault();chartPan(-Math.max(1,Math.round((state.chartView.end-state.chartView.start)*.15)));}
    else if(e.key==='ArrowRight'){e.preventDefault();chartPan(Math.max(1,Math.round((state.chartView.end-state.chartView.start)*.15)));}
    else if(e.key==='Home'){e.preventDefault();chartFit();}
    else if(e.key==='End'){e.preventDefault();chartGoLive();}
  });
}

function showModal(type){
  state.modal=type;document.body.style.overflow='hidden';
  let content='';
  if(type==='methodology')content=`<p>This dashboard recreates the workflow shown in your screenshots, with a transparent demonstration engine that can be replaced by a licensed market-data feed.</p>
    <div class="modal-section"><strong>01 · Sector pulse & wires</strong><p>The left column aggregates constituents into sectors; wires illustrate breadth and price-direction clustering. They are <b>not</b> observed capital flows or exchange order-book liquidity.</p></div>
    <div class="modal-section"><strong>02 · Top 6 → Top 3</strong><p>In Kite mode, the score uses the daily move divided by its previous-20-session average daily range (ADR20), time-matched relative volume (RVOL), 20-day high/low breakouts, VWAP alignment and available futures OI. Missing historical data stays marked as pending; rankings use a temporary raw-momentum fallback while history loads. The Top 6 re-rank intraday using current 5-minute and 15-minute strength, volume participation, VWAP and a deliberately capped return since the market open. An early spike loses rank when momentum stalls. Up to three stock candidates are ALWAYS displayed from each Top 6 when three quotes exist. ACTIVE candidates require agreeing recent 5m/15m movement and RVOL at least 1.0×; others are explicitly shown as WATCH, SIDEWAYS, WARMUP or FADING, not as entry signals. The three picks prioritize active candidates and prefer different sectors. Each displayed pick additionally receives a separate bounded 0–100 momentum-strength rating based on 5m (30 pts), 15m (25 pts), 20-session RVOL (20 pts), recent volume acceleration (10 pts), VWAP alignment (10 pts), and 20-day directional breakout (5 pts). It measures current momentum strength, NOT the probability of a winning trade. Demo ratings are simulated and labeled as such. Values are approximate minute-sampled returns; no trade is automatically confirmed or executed. Thresholds can be tuned after backtesting. The scoring logic is documented in <code>fresh_momentum.py</code> and <code>server.py</code>.</p></div>
    <div class="modal-section"><strong>03 · Multiple-timeframe chart & order blocks</strong><p>Candles are built from 5-minute data and aggregated to the selected 5m / 15m / 30m / 1h interval (1h anchored at 09:15 IST). VWAP uses cumulative typical-price × volume of the source bars, and candidate order blocks are opposing candles ahead of a structure break. The separate OB Quality evaluator confirms closed-bar displacement and BOS, checks 15m context, FVG, candle-based sweep, volume, VWAP, retest, and invalidation. It shows WAIT / READY / INVALID; there is no real Kite aggressor-side delta, and the rating is not a probability or verified institutional order.</p></div>`;
  else content=`<p>The scanner is running in <b>${esc(state.snapshot?.mode==='kite'?'Zerodha Kite Connect':state.snapshot?.mode==='demo'?'demo simulation':'external-feed')}</b> mode.</p>
    <div class="modal-section"><strong>Connection status</strong><p>${esc(state.snapshot?.feedStatus||'unknown')} · Last market quote: ${esc(istTime(state.snapshot?.lastQuoteAt))}. ${esc(state.snapshot?.feedError||'')}</p></div>
    <div class="modal-section"><strong>Live setup</strong><p>For Kite, create a private <code>.env</code> from <code>.env.example</code> with <code>MARKET_MODE=kite</code>, <code>KITE_API_KEY</code> and today's <code>KITE_ACCESS_TOKEN</code>. Restart the server whenever the token expires. Never paste tokens into the browser.</p></div>
    <div class="modal-section"><strong>Market-data limitations</strong><p>NSE equity prices use Kite quotes; OI comes from the nearest listed NFO futures contract when one exists. OI changes are compared against the first value observed after connection. Relative volume compares today's cumulative cash volume with the average cumulative volume at the same 5-minute interval over as many as 20 completed sessions; it remains N/A without enough past data. Rankings are price-direction momentum, not actual order-book depth.</p></div>
    <div class="modal-section"><strong>Tracked instruments</strong><p>${state.snapshot?.universeCount||198} configured symbols · ${state.snapshot?.trackedCount||0} loaded quotes · ${state.snapshot?.futuresMapped||0} matching futures · ${state.snapshot?.missingSymbols?.length||0} unmapped equities. Watchlist NIFTY_50 is not double-counted.</p></div>`;
  $('#overlay-root').innerHTML=`<div class="overlay-scrim" data-action="close-overlay"></div><div class="modal" role="dialog" aria-modal="true" aria-label="${type==='methodology'?'Scanner methodology':'Feed information'}">
    <div class="modal-header"><span class="eyebrow">OrderFlow Studio / Guide</span><button class="icon-btn" data-action="close-overlay" aria-label="Close">${icon('x')}</button></div>
    <h2>${type==='methodology'?'Behind the signals':'Data source & setup'}</h2>${content}<div class="modal-footer"><button class="btn btn-primary" data-action="close-overlay">Got it ${icon('arrowRight')}</button></div></div>`;
}

function handleAction(node){
  const action=node.dataset.action;
  if(action==='toggle-theme'){applyTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');return;}
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
  if(action==='chart-zoom-in'){chartZoom(.75);return;}
  if(action==='chart-zoom-out'){chartZoom(1.35);return;}
  if(action==='chart-fit'){chartFit();return;}
  if(action==='chart-live'){chartGoLive();return;}
  if(action==='indicator'){
    const k=node.dataset.key;if(k==='vwap')state.showVwap=!state.showVwap;
    if(k==='ob')state.showOB=!state.showOB;
    if(k==='levels')state.showLevels=!state.showLevels;
    renderDrawer();return;
  }
}

document.addEventListener('click',ev=>{
  // Native anchor navigation opens the external 5m chart in a new tab.
  if (ev.target.closest('a[data-tradingview]')) return;
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
let resizeRenderTimer=0;
window.addEventListener('resize',()=>{
  clearTimeout(resizeRenderTimer);
  resizeRenderTimer=setTimeout(()=>{
    if(state.snapshot)renderFlow(true);
    if(state.detail&&state.drawer)renderDrawer();
  },160);
});

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
