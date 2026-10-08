/* NEONFLOW — no chart framework required. All feed values come from the Python server. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const els = {
    sectorList: $('sectorList'), gainers: $('gainersList'), losers: $('losersList'),
    flowSvg: $('flowSvg'), flowLines: $('flowLines'), shell: $('marketShell'),
    search: $('searchInput'), sectorSelect: $('sectorSelect'),
    inspector: $('inspector'), infoDialog: $('infoDialog'),
  };
  let model = null, filter = 'ALL', query = '', selected = null;
  let rankMode = 'pro';
  let socket = null, fallbackTimer = null, reconnectTimer = null;
  let receivedAt = 0, optionsLoaded = false, redrawQueued = false;
  const P = '#3affb6', N = '#ff5c8a';
  let focusedSymbol = null;

  const safe = (x) => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sign = n => Number(n) > 0 ? '+' : '';
  const fnum = (v, d=2) => v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
  const pct = (v) => v == null ? '—' : `${sign(v)}${fnum(v)}%`;
  const compact = (v) => v == null ? '—' : v >= 1e7 ? `${fnum(v/1e7,2)} Cr` : v >= 1e5 ? `${fnum(v/1e5,2)} L` : fnum(v,0);
  const positive = v => Number(v) >= 0;
  const asPrice = n => n == null ? '—' : `₹${fnum(n)}`;
  const timeIST = () => new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date());
  const stamp = iso => iso ? new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(iso)) : '—';
  const escapeCSS = value => window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/[^a-zA-Z0-9_-]/g,'_');

  function sparkSVG(points, color, w=61, h=18, fill=false) {
    let values = (points || []).filter(x => Number.isFinite(x));
    if (values.length < 2) return `<svg class="stock-spark" viewBox="0 0 ${w} ${h}"><path d="M0 ${h/2}H${w}" stroke="${color}" stroke-opacity=".3"/></svg>`;
    let min=Math.min(...values), max=Math.max(...values), range=Math.max(max-min,.0001);
    let path=values.map((v,i)=>`${i?'L':'M'}${(i/(values.length-1)*w).toFixed(1)} ${(h-3-(v-min)/range*(h-6)).toFixed(1)}`).join(' ');
    let area = `${path} L${w} ${h} L0 ${h} Z`;
    return `<svg class="stock-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:${w===61?'61px':'100%'};height:${h===18?'18px':'100%'}">${fill?`<path d="${area}" fill="${color}" fill-opacity=".08"/>`:''}<path d="${path}" stroke="${color}" stroke-width="${w===61?1.4:2}" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>`;
  }

  function renderSeeding(data) {
    const overlay = $('seedOverlay');
    if (!overlay) return;
    const ready = data.status === 'ok' && !!data.stocks?.length;
    overlay.classList.toggle('hidden', ready);
    if (ready) return;
    const error = data.status === 'error';
    overlay.classList.toggle('seed-error', error);
    const step = Math.max(0, Math.min(3, Number(data.meta?.seed_step ?? 0)));
    $('seedHeading').textContent = error ? 'MARKET SEED FAILED' : 'SEEDING MARKET DATA';
    $('seedStage').textContent = data.meta?.seed_stage || (error ? 'CONNECTION ERROR' : 'CONNECTING TO MARKET');
    $('seedDescription').textContent = error
      ? (data.error || 'Check Zerodha access token and Render service logs.')
      : 'Render website is online. Kite instruments, quotes and ticker initialize in the background.';
    $('seedProgress').style.width = `${error ? 100 : (step / 3 * 100)}%`;
    $('seedStep').textContent = error ? 'KITE INIT ERROR' : `STEP ${step} / 3`;
    $('seedMode').textContent = data.mode === 'live' ? 'ZERODHA KITE' : 'DEMO MODE';
  }

  function showStatus(data) {
    renderSeeding(data);
    const error = data.status === 'error', starting = data.status !== 'ok' && !error;
    const stream = data.mode === 'live' && data.meta?.feed === 'ticker';
    const state = data.meta?.feed_state;
    const healthy = !error && (!stream || state === 'streaming');
    const mode = data.mode === 'live' ? (stream ? 'LIVE / KITE TICKS' : 'LIVE / KITE REST') : 'DEMO / SYNTHETIC';
    $('modeLabel').textContent = mode + (error ? ' • ERROR' : '');
    $('modeLabel').style.color = error ? N : data.mode === 'demo' ? '#ecc67f' : (healthy ? P : '#ecc67f');
    $('liveDot').style.background = error ? N : data.mode === 'demo' ? '#ecc67f' : (healthy ? P : '#ecc67f');
    const states = {seeding:'SEEDING MARKET DATA',streaming:'STREAMING TICKS', connecting:'CONNECTING KITE', waiting_for_ticks:'WAITING FOR TICKS', stale:'FEED STALE', disconnected:'FEED DISCONNECTED', market_closed_or_idle:'MARKET CLOSED / IDLE'};
    $('feedStatus').textContent = error ? 'FEED ERROR' : starting ? 'SEEDING MARKET DATA' : data.mode === 'demo' ? 'SYNTHETIC DEMO' : stream ? (states[state] || 'CONNECTING KITE') : 'REST LIVE QUOTES';
    $('feedStatus').title = data.error || data.meta?.feed_error || '';
    $('footerStatus').textContent = error ? `FEED ERROR — ${data.error}` : starting ? `MARKET SEED • ${data.meta?.seed_stage || 'PREPARING'}` : stream
      ? `KITE WEBSOCKET • ${data.meta?.received_ticks ?? 0} TICKS • ${data.meta?.subscribed_tokens ?? 0} TOKENS`
      : `CONNECTED • ${data.mode === 'live' ? 'KITE LIVE REST QUOTES' : 'SYNTHETIC DEMO DATA'}`;
    // Snapshot time means last dashboard refresh; last_tick_at is genuine upstream tick freshness.
    $('lastUpdate').textContent = starting ? `SEEDING • ${data.meta?.seed_stage || 'PREPARING'}` : data.timestamp ? `DASHBOARD  ${stamp(data.timestamp)} IST` : 'Connecting to data source...';
    $('footerTime').textContent = stream && data.meta?.last_tick_at ? `LAST TICK ${stamp(data.meta.last_tick_at)} IST` : data.timestamp ? `${stamp(data.timestamp)} IST` : '—';
  }

  function renderSummary(data) {
    const s=data.summary || {}, total=(s.advances||0)+(s.declines||0)+(s.flat||0), breadth=total ? s.advances/total*100 : 0;
    $('breadthValue').innerHTML = `${fnum(breadth,1)}<small>%</small>`;
    $('advanceCount').textContent = s.advances ?? '--'; $('declineCount').textContent = s.declines ?? '--';
    $('breadthTrack').style.width = `${breadth}%`;
    $('linkValue').textContent = `${fnum(breadth,0)}%`;
    $('averageValue').textContent = pct(s.average_pct);
    $('averageValue').style.color = positive(s.average_pct) ? P : N;
    const sectorSorted=[...(data.sectors||[])].sort((a,b)=>b.change_pct-a.change_pct);
    const lead=sectorSorted[0];
    $('topSector').textContent = lead?.name || '—';
    $('topSectorPct').textContent = lead ? pct(lead.change_pct) : '—';
    $('topSectorPct').style.color = positive(lead?.change_pct) ? P : N;
    $('trackedValue').innerHTML = `${s.total||0}<small>/${data.meta?.configured_symbols||198}</small>`;
    $('sectorCount').textContent = `${data.sectors?.length||0} GROUPS`;
  }

  function initOptions(data) {
    if (optionsLoaded || !data.sectors?.length) return;
    for (const sector of [...data.sectors].sort((a,b)=>a.name.localeCompare(b.name))) {
      const el = document.createElement('option');el.value=sector.name;el.textContent=sector.name;els.sectorSelect.appendChild(el);
    }
    optionsLoaded = true;
  }

  function renderSectors(data) {
    const max=Math.max(1, ...data.sectors.map(s => Math.abs(s.change_pct)));
    const has20d=data.meta?.momentum20?.status === 'ready';
    const by20d = has20d && rankMode==='20d';
    const sectorRows=[...data.sectors].sort((a,b)=>by20d
      ? (Number(b.momentum_20d_x!=null)-Number(a.momentum_20d_x!=null)) || ((b.momentum_20d_x??-Infinity)-(a.momentum_20d_x??-Infinity))
      : b.change_pct-a.change_pct);
    els.sectorList.innerHTML = sectorRows.map(s => {
      const up=positive(s.change_pct), active=filter === s.name;
      return `<button class="sector-item ${up?'':'bearish'} ${active?'active':''}" data-sector="${safe(s.name)}" aria-label="Filter to ${safe(s.name)} sector">
        <div class="sector-main"><span class="sector-name">${safe(s.name)}</span><span class="sector-pct" style="color:${up?P:N}">${pct(s.change_pct)}</span></div>
        <div class="sector-bar"><i style="width:${Math.max(4,Math.abs(s.change_pct)/max*100)}%"></i></div>
        <div class="sector-meta"><span>${s.count} STOCKS</span><span>${has20d && s.momentum_20d_x!=null ? `20D ${sign(s.momentum_20d_x)}${fnum(s.momentum_20d_x)}×` : `${s.advances} ↑ / ${s.declines} ↓`}</span></div>
      </button>`;
    }).join('');
    els.sectorList.querySelectorAll('[data-sector]').forEach(el=>el.addEventListener('click',()=>{
      filter = filter===el.dataset.sector?'ALL':el.dataset.sector;
      els.sectorSelect.value=filter;
      render(model);
    }));
  }

  // Signed momentum score is today's move / average absolute daily move over
  // the previous 20 *completed* trading sessions. Null means no valid baseline.
  const validScore = s => {
    const v=s.momentum_score ?? s.momentum_20d_x;
    return v == null || !Number.isFinite(Number(v)) ? null : Number(v);
  };
  const formatScore = s => {
    const v=validScore(s);
    return v == null ? '—' : `${sign(v)}${fnum(v)}×`;
  };
  const proValid = s => s?.pro_score != null && Number.isFinite(Number(s.pro_score))
      && ((s.change_pct > 0 && s.pro_direction === 'bullish') || (s.change_pct < 0 && s.pro_direction === 'bearish'));
  function rowHTML(s, i, kind) {
    const up=positive(s.change_pct), col=up?P:N;
    const valid=proValid(s), n=valid?Number(s.pro_score):null;
    const isPro = rankMode==='pro';
    const old=validScore(s);
    const main=isPro ? (valid?fnum(n,1):'—') : (rankMode==='20d' ? formatScore(s) : pct(s.change_pct));
    const secondary=isPro ? `RVOL ${s.pro_rvol == null?'—':fnum(s.pro_rvol,2)+'×'}  ·  EFF ${s.pro_efficiency==null?'—':fnum(s.pro_efficiency,0)+'%'}` : `20D ${formatScore(s)} · ${safe(s.sector)}`;
    const label=isPro?'PRO / 100':rankMode==='20d'?'MOM 20D':'DAY CHANGE';
    const flags=(s.pro_flags||[]).join(', ');
    return `<button class="stock-row ${up?'':'negative-row'}" data-symbol="${safe(s.symbol)}" data-kind="${kind}"
        data-pro-score="${n == null?'':n}" title="${safe(`${s.symbol} · ${label} ${main} · ${secondary} · ${flags}`)}"
        aria-label="Inspect ${safe(s.symbol)}, ${label} ${main}">
      <div class="stock-row-top"><span class="stock-symbol"><span class="row-number">${String(i+1).padStart(2,'0')}</span>${safe(s.symbol)}</span>
      <span class="stock-momentum ${main==='—'?'no-score':''}" style="--score-color:${col}"><small>${label}</small><strong>${main}${isPro&&valid?'<small class="outof">/100</small>':''}</strong></span></div>
      ${isPro?`<div class="pro-score-track"><i style="width:${valid?n:0}%;background:${col}"></i></div>`:''}
      <div class="stock-row-bottom"><span class="stock-row-metadata"><span class="stock-change" style="color:${col}">${pct(s.change_pct)} <small>DAY</small></span><span class="stock-detail">${safe(secondary)}</span></span>${sparkSVG(s.spark,col)}</div>
    </button>`;
  }

  function visibleStocks() {
    return (model?.stocks||[]).filter(s=>(filter==='ALL'||s.sector===filter) && (!query || s.symbol.toLowerCase().includes(query.toLowerCase())) && s.change_pct != null);
  }

  const is20dReady = () => rankMode === '20d' && model?.meta?.momentum20?.status === 'ready'
    && Number(model.meta.momentum20.available) > 0;

  function renderMomentumStatus(data) {
    const m=data.meta?.momentum20 || {}, pro=data.meta?.proscore || {};
    const banner=$('momentumProgress');if(!banner)return;
    if(rankMode==='pro') {
      const stream=data.meta?.stream_5m;
      const phase=pro.status==='loading'
        ? `ONE-TIME SEED ${pro.processed||0}/${pro.total||198}`
        : stream && pro.status==='ready'
          ? `TICK CANDLES · LAST CLOSE ${stream.last_closed_at ? stamp(new Date(stream.last_closed_at*1000).toISOString())+' IST' : 'WAITING'}${stream.pending_recovery ? ' · GAP REPAIR '+stream.pending_recovery : ''}`
          : pro.status==='ready'?'SORT: SCORE 0–100':(pro.status||'WAITING').toUpperCase();
      banner.textContent = `PRO 5M ${pro.available||0}/${pro.total||198} READY · ${phase}`;
    } else if(rankMode==='20d') {
      banner.textContent=`20D ${m.available||0}/${m.total||198} READY · SORT: NORMALIZED MOMENTUM ×`;
    } else banner.textContent='SORT: DAILY % CHANGE · PRO SCORE AVAILABLE IN DETAILS';
    banner.classList.toggle('ready',rankMode==='pro'?pro.status==='ready':rankMode==='20d'?m.status==='ready':true);
  }
  function rankSide(stocks, side) {
    const direction=side==='bullish'?1:-1;
    return stocks.filter(s=>direction*Number(s.change_pct)>0).sort((a,b)=>{
      if(rankMode==='pro') {
        const x=proValid(a)?Number(a.pro_score):null, y=proValid(b)?Number(b.pro_score):null;
        if(x==null&&y!=null)return 1;
        if(y==null&&x!=null)return -1;
        if(x!=null&&y!=null&&x!==y)return y-x; // Both leaders: higher quality score wins.
      }
      if(rankMode==='20d') {
        const x=validScore(a),y=validScore(b);
        if(x==null&&y!=null)return 1;
        if(y==null&&x!=null)return -1;
        if(x!=null&&y!=null&&x!==y)return direction*(y-x);
      }
      return direction*(Number(b.change_pct)-Number(a.change_pct))||a.symbol.localeCompare(b.symbol);
    });
  }
  function renderStocks() {
    const list=visibleStocks();
    const gainers=rankSide(list,'bullish').slice(0,6);
    const losers=rankSide(list,'bearish').slice(0,6);
    $('bullishRankTitle').textContent=rankMode==='pro'?'BULLISH / PRO SCORE':rankMode==='20d'?'BULLISH / TOP MOMENTUM':'BULLISH / GAINERS';
    $('bearishRankTitle').textContent=rankMode==='pro'?'BEARISH / PRO SCORE':rankMode==='20d'?'BEARISH / TOP MOMENTUM':'BEARISH / LOSERS';
    els.gainers.innerHTML=gainers.length?gainers.map((s,i)=>rowHTML(s,i,'up')).join(''):'<div class="skeleton-lines">No matching gainers</div>';
    els.losers.innerHTML=losers.length?losers.map((s,i)=>rowHTML(s,i,'down')).join(''):'<div class="skeleton-lines">No matching decliners</div>';
    $('gainerCount').textContent=`${gainers.length} SHOWN`;
    $('loserCount').textContent=`${losers.length} SHOWN`;
    for(const el of document.querySelectorAll('.stock-row')) {
      el.addEventListener('click',()=>openInspector(el.dataset.symbol));
      el.addEventListener('mouseenter',()=>{focusedSymbol=el.dataset.symbol;queueFlow()});
      el.addEventListener('mouseleave',()=>{focusedSymbol=null;queueFlow()});
    }
  }

  function renderProfile() {
    if (!model?.stocks) return;
    const stocks = visibleStocks();
    const positiveStocks = stocks.filter(s=>Number(s.change_pct)>0);
    const negativeStocks = stocks.filter(s=>Number(s.change_pct)<0);
    const bullish = positiveStocks.length, bearish = negativeStocks.length, total = stocks.length;
    const bullPct = total ? bullish / total * 100 : 0;
    const bearPct = total ? bearish / total * 100 : 0;
    const net = bullish - bearish;
    let status = 'BALANCED', side = 'neutral';
    if (Math.abs(net) > total * 0.05) {
      if (bullish > bearish) { status = 'BULLISH DOMINANT'; side = 'bullish'; }
      else if (bearish > bullish) { status = 'BEARISH DOMINANT'; side = 'bearish'; }
    }
    let ratio = 'RATIO —';
    if (bullish > 0 && bearish > 0) {
      ratio = bearish > bullish
        ? `BEAR / BULL ${fnum(bearish / bullish, 2)}×`
        : `BULL / BEAR ${fnum(bullish / bearish, 2)}×`;
    } else if (bullish > 0 && bearish === 0) ratio = 'ALL BULLISH';
    else if (bearish > 0 && bullish === 0) ratio = 'ALL BEARISH';
    $('profileUp').textContent = bullish;
    $('profileDown').textContent = bearish;
    $('profileTotal').textContent = total;
    $('profileBullPct').textContent = `${fnum(bullPct,1)}%`;
    $('profileBearPct').textContent = `${fnum(bearPct,1)}%`;
    $('profileNet').textContent = `${net > 0 ? '+' : ''}${net}`;
    $('profileRatio').textContent = ratio;
    const statusEl = $('profileStatus');
    statusEl.textContent = status;
    statusEl.className = `profile-status ${side}`;
    const profilePanel = document.querySelector('.profile-panel');
    if (profilePanel) profilePanel.classList.remove('dominant-bullish','dominant-bearish','dominant-neutral');
    if (profilePanel) profilePanel.classList.add(`dominant-${side}`);
    $('profileMeterLeft').style.width = `${bullPct}%`;
    $('profileMeterRight').style.width = `${bearPct}%`;
    const pointer = $('profileMeterPointer');
    const pointerPos = Math.max(6, Math.min(94, 50 + (bearPct - bullPct) / 2));
    pointer.style.left = `${pointerPos}%`;
    pointer.className = `profile-meter-pointer ${side}`;
    const count = 112;
    const maxAbs = Math.max(4, ...stocks.map(s=>Math.abs(Number(s.change_pct)||0)));
    const hits = new Array(count).fill(0);
    for (const stock of stocks) {
      const v = Number(stock.change_pct)||0;
      const index = v >= 0
        ? Math.min(55, Math.floor((1 - Math.min(v/maxAbs,1))*55))
        : Math.max(56, Math.min(count-1, 56+Math.floor(Math.min(Math.abs(v)/maxAbs,1)*55)));
      hits[index]++;
    }
    const smooth = hits.map((_,i) => {
      let d=0;
      for(let j=-5;j<=5;j++) if(i+j>=0&&i+j<count) d+=hits[i+j]*(6-Math.abs(j))/6;
      return d;
    });
    const peak = Math.max(1,...smooth);
    $('profileBins').innerHTML = smooth.map((density,i)=> {
      const y=i/(count-1)*99.5;
      const val=10+Math.sqrt(density/peak)*86;
      const bright=density/peak>.65?'hot':'';
      return `<div class="profile-bin ${i<56?'up':'down'} ${bright}" style="top:${y.toFixed(3)}%;width:${val.toFixed(1)}%"></div>`;
    }).join('');
  }

  function drawFlow() {
    redrawQueued=false;
    // Mobile view uses the same luminous histogram, but omits the huge SVG for clarity.
    if (!model?.stocks?.length || window.innerWidth<=790) {els.flowLines.innerHTML='';return;}
    const shell=els.shell.getBoundingClientRect(), width=shell.width, height=shell.height;
    if (!width || !height) return;
    els.flowSvg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    const sectorElements = new Map([...els.sectorList.querySelectorAll('[data-sector]')].map(x=>[x.dataset.sector,x]));
    const profileFrame=$('profileFrame').getBoundingClientRect();
    const leaderPanel=els.shell.querySelector('.leader-panel').getBoundingClientRect();
    const originX=els.shell.querySelector('.sector-panel').getBoundingClientRect().right-shell.left-1;
    const profileX=profileFrame.left-shell.left;
    const profileOutX=profileFrame.right-shell.left;
    const targetX=leaderPanel.left-shell.left+1;
    const yTop=64, yMid=height*.5, yBottom=height-38;
    const stocks=visibleStocks();
    const gainers=stocks.filter(s=>Number(s.change_pct)>=0).sort((a,b)=>b.change_pct-a.change_pct);
    const losers=stocks.filter(s=>Number(s.change_pct)<0).sort((a,b)=>b.change_pct-a.change_pct);
    const layout=[
      ...gainers.map((s,i)=>({s,y:yTop+((i+.5)/(gainers.length||1))*(yMid-yTop-9),i})),
      ...losers.map((s,i)=>({s,y:yMid+9+((i+.5)/(losers.length||1))*(yBottom-yMid-9),i})),
    ];
    let paths=[];
    const highlight=focusedSymbol || (filter !== 'ALL' ? filter : null);
    // All valid stocks produce individual curved connections into the central histogram.
    // These are price-strength relationships, NOT institutional cash movement.
    layout.forEach(({s,y,i},n)=>{
      const sector=sectorElements.get(s.sector);
      if (!sector) return;
      const rect=sector.getBoundingClientRect();
      const y1=rect.top-shell.top+rect.height/2;
      const green=positive(s.change_pct), color=green?P:N;
      const matched=!highlight||highlight===s.symbol||highlight===s.sector;
      const nudge=Math.sin(n*1.7)*10;
      const strength=Math.min(1,Math.abs(s.change_pct)/6);
      const d=`M${originX.toFixed(1)} ${y1.toFixed(1)} C${(originX+(profileX-originX)*.31).toFixed(1)} ${(y1+nudge).toFixed(1)},${(profileX-(profileX-originX)*.20).toFixed(1)} ${(y+nudge*.6).toFixed(1)},${profileX.toFixed(1)} ${y.toFixed(1)}`;
      const glow=n%13===0||strength>.8;
      paths.push(`<path class="flow-line ${glow?'hot':'soft'} ${!matched?'muted':''}" stroke="${color}" d="${d}" style="opacity:${matched ? (glow?.75:.20+strength*.27):.02}"/>`);
      if(n%4===0){
        paths.push(`<path class="flow-line flow-animated ${!matched?'muted':''}" stroke="${color}" d="${d}" style="opacity:${matched?.27:.01};animation-delay:-${n%17}s"/>`);
      }
    });
    // The reference video has a second cascade from the histogram to stock tiles.
    // Anchor each visible right-hand tile to a point on the gain/decline distribution.
    const leaderCards=[...els.gainers.querySelectorAll('[data-symbol]'),...els.losers.querySelectorAll('[data-symbol]')];
    const lookup=new Map(stocks.map(s=>[s.symbol,s]));
    const getY=(stock)=> {
      const ordered=stock.change_pct>=0?gainers:losers;
      const idx=ordered.findIndex(x=>x.symbol===stock.symbol);
      return stock.change_pct>=0
        ? yTop+((idx+.5)/Math.max(1,ordered.length))*(yMid-yTop-9)
        : yMid+9+((idx+.5)/Math.max(1,ordered.length))*(yBottom-yMid-9);
    };
    leaderCards.forEach((el,k)=>{
      const stock=lookup.get(el.dataset.symbol);
      if(!stock) return;
      const rect=el.getBoundingClientRect();
      if(rect.bottom < shell.top||rect.top>shell.bottom) return;
      const fromY=getY(stock), toY=rect.top-shell.top+rect.height/2;
      const color=positive(stock.change_pct)?P:N;
      const matched=!highlight||highlight===stock.symbol||highlight===stock.sector;
      for(let j=0;j<3;j++){
        const a=fromY+(j-1)*2.6,b=toY+(j-1)*3;
        const d=`M${profileOutX.toFixed(1)} ${a.toFixed(1)} C${(profileOutX+35).toFixed(1)} ${a.toFixed(1)},${(targetX-45).toFixed(1)} ${b.toFixed(1)},${targetX.toFixed(1)} ${b.toFixed(1)}`;
        paths.push(`<path class="flow-line ${j===1?'hot':''} ${!matched?'muted':''}" stroke="${color}" d="${d}" style="opacity:${matched ? (j===1?.72:.19):.02}"/>`);
      }
      paths.push(`<circle class="end-dot" cx="${targetX}" cy="${toY}" r="2.2" fill="${color}" opacity="${matched?'.95':'.08'}"/>`);
    });
    els.flowLines.removeAttribute('mask');
    els.flowLines.innerHTML=paths.join('');
  }
  function queueFlow() {if(!redrawQueued){redrawQueued=true;requestAnimationFrame(drawFlow)}}

  function render(data) {
    if(!data) return;
    model=data;
    showStatus(data);
    if(!data.stocks?.length)return;
    initOptions(data);
    renderSummary(data);
    renderMomentumStatus(data);
    renderSectors(data);
    renderStocks();
    renderProfile();
    queueFlow();
    if(selected && !els.inspector.classList.contains('hidden')){
      renderInspector(selected);
      window.NeonChart?.updateTick(selected, model?.stocks?.find(x=>x.symbol===selected));
    }
    receivedAt=Date.now();
  }

  const metrics = (key,value) => `<div class="metric-cell"><div class="metric-label">${key}</div><div class="metric-value">${value}</div></div>`;
  function renderInspector(symbol) {
    const s=model?.stocks?.find(x=>x.symbol===symbol);
    if(!s)return;
    let up=positive(s.change_pct), color=up?P:N;
    $('inspectSector').textContent=`${s.sector} ${s.nifty50?' / NIFTY 50 BASKET':''}`;
    $('inspectorTitle').textContent=s.symbol;
    $('inspectContract').textContent=s.futures_contract ? `NSE CASH / ${s.futures_contract} / ${s.futures_expiry}` : 'NSE CASH / NO FUTURES MAPPED';
    $('inspectPrice').textContent=asPrice(s.price);
    $('inspectChange').textContent=`${pct(s.change_pct)}   (${s.change_abs==null?'—':sign(s.change_abs)+asPrice(s.change_abs)})`;
    $('inspectChange').style.color=color;
    const build=$('inspectBuildup');build.textContent=s.buildup;
    build.classList.toggle('bearish',/SHORT BUILD-UP|LONG UNWINDING/.test(s.buildup));
    const m=$('inspectMomentum');
    m.textContent = validScore(s) == null ? 'MOMENTUM SCORE 20D · UNAVAILABLE / LOADING' : `MOMENTUM SCORE ${formatScore(s)}  |  TODAY ${pct(s.change_pct)}  |  20D AVG MOVE ${fnum(s.avg_day_move_20d_pct,2)}%`;
    m.classList.toggle('bearish',s.momentum_20d_x!=null && s.momentum_20d_x<0);
    const header=$('inspectProScore'), parts=$('inspectProComponents');
    const val=proValid(s)?Number(s.pro_score):null;
    header.textContent = val==null ? `PRO SCORE SEEDING · ${s.pro_reason||'WAITING FOR 5M CANDLES'}` :
      `PRO MOMENTUM ${fnum(val,1)}/100  ·  RVOL ${s.pro_rvol??'—'}×  ·  TREND EFF ${s.pro_efficiency??'—'}%  ·  COVERAGE ${s.pro_coverage??'—'}%`;
    header.classList.toggle('bearish',s.change_pct<0);
    const weights={momentum:20,rvol:20,trend:20,vwap:10,ema50:10,oi:10,sector:5,breakout:5};
    parts.innerHTML=val==null?'':Object.entries(weights).map(([k,max])=>{
      const score=s.pro_components?.[k]; const amount=Number(score)||0;
      return `<div class="pro-component"><span>${k.toUpperCase()} <b>${fnum(amount,1)}/${max}</b></span><div class="pro-component-bar"><i style="width:${Math.min(100,amount/max*100)}%;background:${s.change_pct<0?N:P}"></i></div></div>`;
    }).join('')+`<div class="pro-component-flags">${(s.pro_flags||[]).map(safe).join(' · ')||'NO LIQUIDITY / EXTENSION FLAGS'} · 5M CANDLE CLOSE ${s.pro_asof?stamp(new Date(s.pro_asof*1000).toISOString()):'—'} IST</div>`;
  }
  function openInspector(symbol) {selected=symbol;renderInspector(symbol);els.inspector.classList.remove('hidden');window.NeonChart?.open(symbol)}
  function closeInspector(){selected=null;els.inspector.classList.add('hidden');window.NeonChart?.close()}
  function openInfo(){els.infoDialog.classList.remove('hidden')}
  function closeInfo(){els.infoDialog.classList.add('hidden')}

  async function fetchOnce(){try{const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error(response.status);render(await response.json())}catch(err){$('feedStatus').textContent='DISCONNECTED';$('feedStatus').title=String(err)}}
  function stopFallback(){if(fallbackTimer){clearInterval(fallbackTimer);fallbackTimer=null}}
  function startFallback(){if(!fallbackTimer){fetchOnce();fallbackTimer=setInterval(fetchOnce,5000)}}
  function connect(){
    clearTimeout(reconnectTimer);
    const scheme=location.protocol==='https:'?'wss':'ws';
    try{socket=new WebSocket(`${scheme}://${location.host}/ws`)}catch(_){startFallback();return}
    socket.onopen=()=>{stopFallback()};
    socket.onmessage=e=>{try{render(JSON.parse(e.data))}catch(err){console.error('Feed decode failed:',err)}};
    socket.onclose=()=>{socket=null;startFallback();reconnectTimer=setTimeout(connect,4000)};
    socket.onerror=()=>{socket?.close()};
  }
  $('searchInput').addEventListener('input',e=>{query=e.target.value.trim();if(model)render(model)});
  $('sectorSelect').addEventListener('change',e=>{filter=e.target.value;if(model)render(model)});
  $('rankMode').addEventListener('change',e=>{rankMode=e.target.value;if(model)render(model)});
  $('closeInspector').addEventListener('click',closeInspector);
  $('infoBtn').addEventListener('click',openInfo);$('closeInfo').addEventListener('click',closeInfo);
  els.inspector.addEventListener('click',e=>{if(e.target===els.inspector)closeInspector()});
  els.infoDialog.addEventListener('click',e=>{if(e.target===els.infoDialog)closeInfo()});
  window.addEventListener('keydown',e=>{
    if(e.key==='Escape'){closeInspector();closeInfo()}
    if(e.key==='/' && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) {e.preventDefault();els.search.focus()}
  });
  window.addEventListener('resize',queueFlow);
  $('stocksScroll').addEventListener('scroll',queueFlow,{passive:true});
  els.sectorList.addEventListener('scroll',queueFlow,{passive:true});
  setInterval(()=>{$('marketClock').textContent=`${timeIST()} IST`; if(receivedAt && Date.now()-receivedAt>30000){$('feedStatus').textContent='DATA STALE'}},1000);
  $('marketClock').textContent=`${timeIST()} IST`;
  connect();
})();
