/* NEONFLOW 07 — enhanced TradingView-style hi-DPI candlestick + order-block canvas renderer.
   Features: price scale, time scale, crosshair axis labels, support/resistance levels,
   improved order-block shading, zoom/pan, and live last-candle updates.
*/
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const canvas = $('candleCanvas');
  const stage = $('chartStage');
  const tip = $('chartTooltip');
  const message = $('chartMessage');
  const count = $('chartCount');
  const ctx = canvas.getContext('2d');
  const COLORS = {
    up:'#34ffb4', down:'#ff638d', axis:'#89b9aa', grid:'#28463f', text:'#d7fff0',
    support:'#5bd7ff', resistance:'#ffc86a', scale:'#09141a', scaleBorder:'#22433b',
    ema9:'#f3ce72', ema20:'#68bff7', ema50:'#ce9df8', vwap:'#f9a76e', pdh:'#ff8b92', pdl:'#61debf', open:'#e3e6ee'
  };
  const fmtPrice = v => '₹' + Number(v).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const dateFmt = new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',hour12:false});
  const dayFmt = new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short'});
  const when = t => `${dayFmt.format(new Date(t*1000))} ${dateFmt.format(new Date(t*1000))}`;
  const dateKey = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kolkata',year:'numeric',month:'2-digit',day:'2-digit'});
  const istClock = new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  const supported = new Set(['1m','3m','5m','15m','30m','1D']);
  const checkboxIds = {ema9:'showEma9',ema20:'showEma20',ema50:'showEma50',vwap:'showVwap',levels:'showLevels',swings:'showSwings',ob:'showOrderBlocks'};
  const toggle = id => $(checkboxIds[id]).checked;
  const sessionKey = t => dateKey.format(new Date(t*1000));
  const num = n => Number(n).toLocaleString('en-IN',{maximumFractionDigits:0});
  let timeframe = '5m';

  let chart = null, symbol = null, cancel = null, generation = 0;
  let visibleBars = 92, pan = 0, hover = -1, drag = null;
  let lastTickTime = 0, dim = {width:0,height:0}, layout = null, hoverY = null;

  function setMessage(text, error = false) {
    message.textContent = text;
    message.classList.toggle('hidden', !text);
    message.classList.toggle('chart-error', error);
  }
  function resize() {
    const {width, height} = stage.getBoundingClientRect();
    if (!width || !height) return;
    const ratio = window.devicePixelRatio || 1;
    dim = {width: Math.round(width), height: Math.round(height)};
    canvas.width = Math.round(width*ratio);
    canvas.height = Math.round(height*ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(ratio,0,0,ratio,0,0);
    draw();
  }
  function line(x1,y1,x2,y2,color,width=1,dash=[]) {
    ctx.beginPath(); ctx.setLineDash(dash); ctx.strokeStyle=color; ctx.lineWidth=width;
    ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke(); ctx.setLineDash([]);
  }
  function box(x,y,w,h,fill,stroke=null) {
    ctx.fillStyle=fill; ctx.fillRect(x,y,w,h);
    if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.strokeRect(x+.5,y+.5,Math.max(1,w-1),Math.max(1,h-1));}
  }
  function roundedBox(x,y,w,h,r,fill,stroke=null) {
    const rr = Math.min(r, w/2, h/2);
    ctx.beginPath();
    ctx.moveTo(x+rr, y);
    ctx.arcTo(x+w, y, x+w, y+h, rr);
    ctx.arcTo(x+w, y+h, x, y+h, rr);
    ctx.arcTo(x, y+h, x, y, rr);
    ctx.arcTo(x, y, x+w, y, rr);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); }
  }
  function textChip(x, y, text, fill, color, align='center') {
    ctx.font = '10px "DM Mono", monospace';
    const padX = 8, padY = 5;
    const tw = ctx.measureText(text).width;
    let left = x - (align === 'center' ? (tw + padX*2)/2 : align === 'right' ? (tw + padX*2) : 0);
    roundedBox(left, y-12, tw + padX*2, 20, 4, fill);
    ctx.fillStyle = color;
    ctx.textAlign = align;
    const tx = align === 'center' ? x : align === 'right' ? left + tw + padX : left + padX;
    ctx.fillText(text, tx, y+2);
  }
  function priceChip(x, y, text, fill, color) {
    ctx.font='bold 10px "DM Mono", monospace';
    const padX=8, w=ctx.measureText(text).width + padX*2;
    roundedBox(x, y-10, w, 20, 4, fill);
    ctx.fillStyle=color; ctx.textAlign='left';
    ctx.fillText(text, x+padX, y+4);
  }
  function computeLevels(selected, lastClose, floor, ceiling) {
    if (!selected?.length) return {supports:[], resistances:[]};
    const pivots = [];
    for (let i = 2; i < selected.length - 2; i++) {
      const c = selected[i];
      const prev2 = selected[i-2], prev1 = selected[i-1], next1 = selected[i+1], next2 = selected[i+2];
      if (c.high > prev2.high && c.high > prev1.high && c.high >= next1.high && c.high >= next2.high) {
        const prominence = (c.high - Math.max(prev1.high, next1.high)) + (c.high - Math.max(prev2.high, next2.high));
        pivots.push({type:'R', price:c.high, time:c.time, prominence, index:i});
      }
      if (c.low < prev2.low && c.low < prev1.low && c.low <= next1.low && c.low <= next2.low) {
        const prominence = (Math.min(prev1.low, next1.low) - c.low) + (Math.min(prev2.low, next2.low) - c.low);
        pivots.push({type:'S', price:c.low, time:c.time, prominence, index:i});
      }
    }
    const rng = Math.max(0.01, ceiling - floor);
    const tol = rng * 0.018; // dedupe nearby levels within ~1.8% of visible range
    const supports = [], resistances = [];
    const dedupePush = (arr, item) => {
      const near = arr.find(x => Math.abs(x.price - item.price) <= tol);
      if (!near) arr.push(item);
    };
    pivots.sort((a,b) => b.prominence - a.prominence || b.time - a.time);
    for (const p of pivots) {
      if (p.type === 'R' && p.price >= lastClose) dedupePush(resistances, p);
      if (p.type === 'S' && p.price <= lastClose) dedupePush(supports, p);
    }
    supports.sort((a,b) => b.price - a.price);
    resistances.sort((a,b) => a.price - b.price);
    return {supports: supports.slice(0,3), resistances: resistances.slice(0,3)};
  }
  // Seed EMA with an SMA; do not pretend an underfilled EMA is valid.
  function emaSeries(data, period) {
    const out = Array(data.length).fill(null);
    if (data.length < period) return out;
    let sum=0, current=null;
    const alpha=2/(period+1);
    for (let i=0; i<data.length; i++) {
      if (i<period) sum+=data[i].close;
      if (i===period-1) current=sum/period;
      else if (i>=period) current=(data[i].close-current)*alpha+current;
      if (i>=period-1) out[i]=current;
    }
    return out;
  }
  // Session VWAP uses cash candle OHLC typical price and reported traded volume.
  // Daily candles cannot give meaningful intraday VWAP; hide the line in 1D.
  function vwapSeries(data) {
    const out=Array(data.length).fill(null);
    let previous='', vp=0, volume=0;
    data.forEach((c,i)=>{
      const day=sessionKey(c.time);
      if(day!==previous){vp=0;volume=0;previous=day;}
      const v=Math.max(0,Number(c.volume)||0);
      if(v){vp+=((c.high+c.low+c.close)/3)*v;volume+=v;}
      if(volume)out[i]=vp/volume;
    });
    return out;
  }
  function sessionLevels(data) {
    if(!data.length)return null;
    const current=sessionKey(data[data.length-1].time);
    const today=data.filter(c=>sessionKey(c.time)===current);
    const previous=data.filter(c=>sessionKey(c.time)<current);
    if(!today.length)return null;
    const prevDate=previous.length?sessionKey(previous[previous.length-1].time):null;
    const prev=previous.filter(c=>sessionKey(c.time)===prevDate);
    return {open:today[0].open, session:current,
      pdh:prev.length?Math.max(...prev.map(c=>c.high)):null,
      pdl:prev.length?Math.min(...prev.map(c=>c.low)):null};
  }
  function drawOverlay(series,color,start,end,X,yPrice,top,bottom,width=1.3) {
    ctx.beginPath(); ctx.strokeStyle=color; ctx.lineWidth=width; ctx.setLineDash([]);
    let hasSegment=false;
    for(let i=start;i<end;i++){
      const p=series[i];
      if(p==null || !Number.isFinite(p) || yPrice(p)<top || yPrice(p)>bottom) {hasSegment=false;continue;}
      if(!hasSegment)ctx.moveTo(X(i),yPrice(p));else ctx.lineTo(X(i),yPrice(p));
      hasSegment=true;
    }
    ctx.stroke();
  }
  function renderOHLC(c) {
    const root=$('chartOhlc');
    if(!c){root.textContent='SELECT A STOCK TO INSPECT OHLCV';return;}
    const sign=c.close>=c.open?'ohlc-up':'ohlc-down';
    root.innerHTML=`<span class="ohlc-date">${timeframe==='1D'?dayFmt.format(new Date(c.time*1000)):when(c.time)} IST · ${timeframe.toUpperCase()}</span><span>O <b>${fmtPrice(c.open)}</b></span><span>H <b>${fmtPrice(c.high)}</b></span><span>L <b>${fmtPrice(c.low)}</b></span><span class="${sign}">C <b>${fmtPrice(c.close)}</b></span><span>VOL <b>${num(c.volume||0)}</b></span>`;
  }
  function refreshTimeframes(){
    document.querySelectorAll('#chartTimeframes button').forEach(el=>{
      const active=el.dataset.tf===timeframe;
      el.classList.toggle('active',active);el.setAttribute('aria-pressed',String(active));
    });
    const vwap=$('showVwap');
    vwap.disabled=timeframe==='1D';
    vwap.closest('.chart-tool').title=timeframe==='1D'?'Session VWAP is only meaningful on intraday bars':'Session VWAP';
  }

  function draw() {
    const W=dim.width, H=dim.height;
    if (!W || !H) return;
    ctx.clearRect(0,0,W,H);
    const grad=ctx.createLinearGradient(0,0,0,H);grad.addColorStop(0,'#0c2928');grad.addColorStop(1,'#091319');
    box(0,0,W,H,grad);
    const L=12,R=84,T=16,B=34,V=68,gap=12;
    const plotW=Math.max(60,W-L-R), plotH=Math.max(85,H-T-B-V-gap);
    const Vtop=T+plotH+gap, Vbottom=H-B;
    box(W-R,0,R,H,COLORS.scale); line(W-R,0,W-R,H,COLORS.scaleBorder,1);
    box(0,H-B,W,B,COLORS.scale); line(0,H-B,W,H-B,COLORS.scaleBorder,1);
    const data=chart?.candles||[];
    const span=Math.min(Math.max(24, visibleBars), Math.max(24,data.length));
    const end=Math.max(0,data.length-pan);
    const start=Math.max(0,end-span);
    const selected=data.slice(start,end);
    if (!selected.length) return;
    const high=Math.max(...selected.map(c=>c.high));
    const low=Math.min(...selected.map(c=>c.low));
    const delta=Math.max(.02,high-low);
    const ceiling=high+delta*.10, floor=Math.max(0,low-delta*.10);
    const yPrice=p=>T+(ceiling-p)/Math.max(.01,ceiling-floor)*plotH;
    const X=i=>L+(i-start+.5)*(plotW/span);
    const candleWidth=Math.max(2,Math.min(11,(plotW/span)*.68));
    layout={L,R,T,B,V,plotW,plotH,span,start,end,X,yPrice,Vtop,Vbottom};

    // Horizontal price grid + labels
    ctx.font='10px "DM Mono",monospace';
    for(let k=0;k<=5;k++){
      const y=T+k*plotH/5, p=ceiling-k*(ceiling-floor)/5;
      line(L,y,W-R,y,COLORS.grid,.8,[3,4]);
      ctx.textAlign='right';ctx.fillStyle=COLORS.axis;ctx.fillText(p.toFixed(2),W-8,y+3);
    }

    // Time grid and bottom scale labels
    const step=Math.max(1,Math.floor(selected.length/7));
    for(let i=start;i<end;i+=step){
      const x=X(i);
      line(x,T,x,Vbottom,'#1b3c38',.6,[2,5]);
      const ts=data[i].time, prev=i ? data[i-1].time : 0;
      const label = timeframe==='1D'||ts-prev>3600 ? dayFmt.format(new Date(ts*1000)) : dateFmt.format(new Date(ts*1000));
      ctx.fillStyle='#72998e';ctx.textAlign='center';ctx.fillText(label,x,H-12);
    }

    line(L,Vtop-8,W-R,Vtop-8,'#376451',1);
    const volMax=Math.max(1,...selected.map(c=>c.volume||0));
    ctx.fillStyle='#729e91';ctx.textAlign='left';ctx.font='9px "DM Mono",monospace';ctx.fillText('VOLUME',L,Vtop+4);

    // Support / resistance levels from visible window.
    const lastVisible = selected[selected.length-1];
    const levels = computeLevels(selected, lastVisible.close, floor, ceiling);
    if(toggle('swings')) levels.resistances.forEach((lvl, idx) => {
      const y = yPrice(lvl.price);
      line(L,y,W-R,y,'#ffc86a90',1,[7,5]);
      textChip(L+34,y,`R${idx+1}`, '#4d3614', COLORS.resistance, 'center');
      priceChip(W-R+5,y,lvl.price.toFixed(2),'#3f2f10',COLORS.resistance);
    });
    if(toggle('swings')) levels.supports.forEach((lvl, idx) => {
      const y = yPrice(lvl.price);
      line(L,y,W-R,y,'#59d6ff90',1,[7,5]);
      textChip(L+34,y,`S${idx+1}`, '#143847', COLORS.support, 'center');
      priceChip(W-R+5,y,lvl.price.toFixed(2),'#102e3a',COLORS.support);
    });

    // Improved order-block regions BELOW price action.
    if (toggle('ob') && chart?.order_blocks?.length) {
      for(const zone of chart.order_blocks){
        const origin=data.findIndex(c=>c.time===zone.origin_time);
        if(origin<0 || origin>=end) continue;
        const from=Math.max(L,X(Math.max(start,origin))-candleWidth/2);
        const top=Math.max(T,yPrice(zone.high)), bottom=Math.min(T+plotH,yPrice(zone.low));
        if(bottom<=top) continue;
        const bull=zone.type==='bullish';
        const fillGrad = ctx.createLinearGradient(0, top, 0, bottom);
        if (bull) {
          fillGrad.addColorStop(0, '#34ffb42c');
          fillGrad.addColorStop(.5, '#34ffb418');
          fillGrad.addColorStop(1, '#34ffb40c');
        } else {
          fillGrad.addColorStop(0, '#ff638d2e');
          fillGrad.addColorStop(.5, '#ff638d18');
          fillGrad.addColorStop(1, '#ff638d0d');
        }
        box(from, top, W-R-from, bottom-top, fillGrad, bull?'#34ffb46b':'#ff638d73');
        line(from, top, W-R, top, bull?'#48efb297':'#ff7b9d9a', 1, [9,5]);
        line(from, bottom, W-R, bottom, bull?'#48efb278':'#ff7b9d76', 1, [9,5]);
        ctx.fillStyle=bull?'#aaffd8':'#ffb2c3'; ctx.textAlign='left'; ctx.font='10px "DM Mono",monospace';
        const label=`${bull?'BULL':'BEAR'} OB ${zone.tested?'· TESTED':'· FRESH'}`;
        const x=Math.min(W-R-ctx.measureText(label).width-4,from+5);
        if(x>=L) ctx.fillText(label,x,Math.max(T+10,top-4));
      }
    }

    // Prior-session high / low and opening price. Labels state what they are.
    const session = sessionLevels(data);
    if (toggle('levels') && session) {
      for(const [key,value,color] of [['PDH',session.pdh,COLORS.pdh],['PDL',session.pdl,COLORS.pdl],['OPEN',session.open,COLORS.open]]){
        if(value==null || value>ceiling || value<floor)continue;
        const y=yPrice(value);
        line(L,y,W-R,y,color+'a8',1,[9,4]);
        ctx.font='bold 9px "DM Mono",monospace';ctx.textAlign='left';ctx.fillStyle=color;
        ctx.fillText(key,L+4,Math.max(T+10,Math.min(T+plotH-5,y-4)));
      }
    }
    // Only draw averages if the user has enabled the series.
    for(const [id,period,col] of [['ema9',9,COLORS.ema9],['ema20',20,COLORS.ema20],['ema50',50,COLORS.ema50]]){
      if(toggle(id))drawOverlay(emaSeries(data,period),col,start,end,X,yPrice,T,T+plotH,1.45);
    }
    if(timeframe!=='1D' && toggle('vwap'))drawOverlay(vwapSeries(data),COLORS.vwap,start,end,X,yPrice,T,T+plotH,1.7);

    // Candles
    selected.forEach((c,k)=>{
      const i=start+k, x=X(i), green=c.close>=c.open, color=green?COLORS.up:COLORS.down;
      line(x,yPrice(c.high),x,yPrice(c.low),color,Math.max(1,candleWidth*.18));
      const y1=yPrice(Math.max(c.open,c.close)), y2=yPrice(Math.min(c.open,c.close));
      box(x-candleWidth/2, y1, candleWidth, Math.max(1.5,y2-y1), color);
      if(c.volume){
        const vheight=Math.max(.8,(c.volume/volMax)*(V-14));
        box(x-candleWidth/2, Vbottom-vheight, candleWidth, vheight, green?'#278e6ab0':'#ad476db0');
      }
    });

    // Current price line & chip.
    const last=data[data.length-1];
    if(last && last.close>=floor && last.close<=ceiling){
      const y=yPrice(last.close), col=last.close>=last.open?COLORS.up:COLORS.down;
      line(L,y,W-R,y,col,.9,[3,4]);
      priceChip(W-R+5,y,last.close.toFixed(2),col,'#061a17');
    }

    // Crosshair / labels / tooltip.
    if(hover>=start && hover<end){
      const c=data[hover], x=X(hover);
      line(x,T,x,Vbottom,'#d0fce37c',.9,[4,3]);
      const hitY=Math.max(T+8,Math.min(T+plotH-8,hoverY??yPrice(c.close)));
      const cursorPrice=ceiling-(hitY-T)/plotH*(ceiling-floor);
      line(L,hitY,W-R,hitY,'#d0fce352',.8,[2,3]);
      priceChip(W-R+5, hitY, cursorPrice.toFixed(2), '#dffaf0', '#08201b');
      textChip(x, H-16, timeframe==='1D'?dayFmt.format(new Date(c.time*1000)):dateFmt.format(new Date(c.time*1000)), '#dffaf0', '#08201b', 'center');
      tip.innerHTML=`<b>${timeframe==='1D'?dayFmt.format(new Date(c.time*1000)):when(c.time)} IST</b><br>O ${c.open.toFixed(2)} &nbsp; H ${c.high.toFixed(2)}<br>L ${c.low.toFixed(2)} &nbsp; C ${c.close.toFixed(2)}<br>VOL ${(c.volume||0).toLocaleString('en-IN')}`;
      tip.classList.remove('hidden');
      const left=Math.min(W-172,Math.max(9,x+12)); tip.style.left=`${left}px`;
      tip.style.top=`${Math.min(H-91,Math.max(8,hitY-48))}px`;
    } else tip.classList.add('hidden');
    renderOHLC(hover>=start && hover<end ? data[hover] : data[data.length-1]);
  }
  function chartPointerIndex(clientX) {
    if(!chart || !layout) return -1;
    const r=stage.getBoundingClientRect();
    const rel=(clientX-r.left-layout.L)/layout.plotW;
    return Math.max(layout.start, Math.min(layout.end-1, Math.floor(layout.start+rel*layout.span)));
  }
  function zoom(next) { visibleBars=Math.max(24,Math.min(260,next)); pan=Math.min(pan,Math.max(0,(chart?.candles.length||0)-visibleBars)); draw(); }
  canvas.addEventListener('wheel',ev=>{ if(!chart) return; ev.preventDefault(); zoom(visibleBars+(ev.deltaY>0?10:-10)); },{passive:false});
  canvas.addEventListener('pointerdown',ev=>{ if(!chart) return; drag={x:ev.clientX,initial:pan}; canvas.setPointerCapture(ev.pointerId); });
  canvas.addEventListener('pointermove',ev=>{
    if(!chart) return;
    if(drag){
      const barsPerPixel=visibleBars/Math.max(100,layout.plotW);
      pan=Math.min(Math.max(0,chart.candles.length-visibleBars), Math.max(0,drag.initial+(ev.clientX-drag.x)*barsPerPixel|0));
    }
    hover=chartPointerIndex(ev.clientX); hoverY=ev.clientY-stage.getBoundingClientRect().top; draw();
  });
  canvas.addEventListener('pointerup',()=>{drag=null});
  canvas.addEventListener('pointercancel',()=>{drag=null});
  canvas.addEventListener('pointerleave',()=>{if(!drag){hover=-1;hoverY=null;draw()}});
  Object.values(checkboxIds).forEach(id=>$(id).addEventListener('change',draw));
  document.querySelectorAll('#chartTimeframes button').forEach(el=>el.addEventListener('click',()=>{
    const tf=el.dataset.tf;
    if (!symbol || !supported.has(tf) || tf===timeframe)return;
    timeframe=tf;refreshTimeframes();open(symbol);
  }));
  refreshTimeframes();
  new ResizeObserver(resize).observe(stage);

  async function open(stock) {
    symbol=stock;
    chart=null; pan=0; hover=-1; hoverY=null; visibleBars=timeframe==='1D'?72:92; lastTickTime=0;
    generation++;
    const attempt=generation;
    if(cancel) cancel.abort();
    cancel=new AbortController();
    count.textContent=`${timeframe.toUpperCase()} / LOADING`;
    setMessage(`FETCHING ${timeframe.toUpperCase()} NSE CANDLES…`);
    resize();
    try{
      const response=await fetch(`/api/chart/${encodeURIComponent(stock)}?timeframe=${encodeURIComponent(timeframe)}`,{signal:cancel.signal,cache:'no-store'});
      const data=await response.json();
      if(!response.ok) throw Error(data.detail||`HTTP ${response.status}`);
      if(attempt!==generation || stock!==symbol) return;
      chart=data;
      count.textContent=`${timeframe.toUpperCase()} · ${data.candles.length} BARS / ${data.order_blocks.length} OB ZONES / ${data.mode.toUpperCase()}`;
      setMessage('');
      draw();
    }catch(err){
      if(err.name==='AbortError'||attempt!==generation) return;
      setMessage(`CHART UNAVAILABLE: ${err.message}`,true);
      count.textContent='NO HISTORICAL DATA';
    }
  }
  function close(){ generation++; symbol=null; chart=null; hover=-1; hoverY=null; cancel?.abort(); tip.classList.add('hidden'); }
  function updateTick(stock,s) {
    if(!chart || stock!==symbol || chart.mode!=='live' || !s?.tick_at || !Number.isFinite(Number(s.price))) return;
    const tickTime=Math.floor(Date.parse(s.tick_at)/1000);
    if(!Number.isFinite(tickTime) || tickTime<=lastTickTime) return;
    lastTickTime=tickTime;
    // Kite 30-minute bars are aligned to 09:15 IST, NOT midnight UTC.
    const parts=Object.fromEntries(istClock.formatToParts(new Date(tickTime*1000)).filter(p=>p.type!=='literal').map(p=>[p.type,Number(p.value)]));
    const localMinute=parts.hour*60+parts.minute;
    if(localMinute<555 || localMinute>=930)return;
    const istMidnight=tickTime-localMinute*60-parts.second;
    const anchor=istMidnight+555*60;
    const secs=chart.interval_seconds || 300;
    const bucket=timeframe==='1D' ? istMidnight : anchor+Math.floor((tickTime-anchor)/secs)*secs;
    const candles=chart.candles;
    const last=candles[candles.length-1];
    if(!last || bucket<last.time || tickTime>Date.now()/1000+60) return;
    const price=Number(s.price);
    if(bucket===last.time){
      last.high=Math.max(last.high,price); last.low=Math.min(last.low,price); last.close=price;
    }else{
      candles.push({time:bucket,open:last.close,high:Math.max(last.close,price),low:Math.min(last.close,price),close:price,volume:0});
      if(candles.length>3800) candles.shift();
      if(pan===0) hover=-1;
    }
    draw();
  }
  window.NeonChart={open,close,updateTick};
})();
