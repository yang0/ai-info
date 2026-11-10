'use strict';
const $ = s => document.querySelector(s);
const escapeHTML = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const e = escapeHTML;
let state = null, selected = null, filter = 'all', view = 'command', token = '', toastTimer, stream;
const modes = {demo:'演示',opencli:'OpenCLI 最新动态',agent_reach:'Agent Reach 免费采集'};
const states = {running:'执行中',done:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断',info:'信息'};
const agentIcons = ['◎','⧉','⌁','◇','⌘','▤'];
const fmt = n => Number(n || 0).toLocaleString('zh-CN');
const time = s => s ? new Date(s).toLocaleTimeString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}) : '—';
const date = s => s ? new Date(s).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}) : '—';
function toast(message) { $('#toast').textContent = message; $('#toast').classList.remove('hidden'); clearTimeout(toastTimer); toastTimer = setTimeout(()=>$('#toast').classList.add('hidden'),6000); }
async function api(path, options={}) {
  const response = await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json','X-Intel-Token':token,...options.headers}});
  if(!response.ok) {
    const body = await response.json().catch(()=>({detail:`请求失败 (${response.status})`}));
    const message = typeof body.detail === 'string' ? body.detail : (body.detail || []).map(x=>x.msg).join('；');
    throw new Error(message || '请求失败');
  }
  return response.json();
}
function topic() { return state?.snapshot?.topics.find(t=>t.id === selected); }
function empty(message) { return `<div class="empty"><span class="empty-symbol">◎</span>${e(message)}</div>`; }
function tag(t) { return `<span class="state-tag ${t.state==='加速崛起'?'rising':t.state==='降温'?'cool':''}">${e(t.state)}</span>`; }
function spark(bins) {
  const max = Math.max(1,...bins); const points=bins.map((v,i)=>`${i*8},${20-v/max*18}`).join(' ');
  return `<svg class="sparkline" viewBox="0 0 58 22" aria-hidden="true"><polyline points="${points}" fill="none" stroke="#c4a064" stroke-width="1.3"/></svg>`;
}
function renderTopics() {
  const terms = $('#search').value.toLowerCase();
  const topics = (state?.snapshot?.topics||[]).filter(t=>
    (filter==='all'||(filter==='rising'?t.state==='加速崛起':t.cold_start)) &&
    (t.title+' '+t.keywords.join(' ')).toLowerCase().includes(terms));
  const sort = $('#sort').value; topics.sort((a,b)=>b[sort]-a[sort]);
  $('#topic-count').textContent = `${topics.length} 话题`;
  $('#topic-list').innerHTML = topics.length ? topics.map((t,i)=>
    `<button class="topic-row ${selected===t.id?'selected':''}" data-topic="${e(t.id)}" aria-pressed="${selected===t.id}"><span class="rank">${String(i+1).padStart(2,'0')}</span><div class="topic-content"><div class="topic-name">${e(t.title)}</div><div class="topic-meta">${tag(t)}<span>${fmt(t.current)} 条</span><span>${t.authors} 作者</span></div></div><div class="topic-score"><b>${t.score.toFixed(1)}</b>${spark(t.bins)}</div></button>`).join('') : empty(state?.snapshot?'没有符合条件的话题':'点击“立即扫描”开始发现信号');
}
function renderChart() {
  const t=topic(); if(!t) { $('#trend-chart').innerHTML=empty('完成扫描后显示趋势'); $('#chart-topic').textContent='等待观测'; $('#chart-value').textContent='—'; $('#chart-growth').textContent='—'; return; }
  $('#chart-topic').textContent=t.title;
  $('#chart-value').innerHTML=`${fmt(t.current)} <small>条 / 15min</small>`;
  $('#chart-growth').textContent=t.cold_start?'基线不足':`${t.growth>=0?'+':''}${t.growth.toFixed(0)}%`;
  $('#chart-growth').classList.toggle('negative',t.growth<0);
  $('#chart-note').textContent=t.cold_start?'冷启动 · 暂不认定持续崛起':'环比前 15 分钟';
  const bins=t.bins, max=Math.max(4,...bins), w=390,h=140,left=28,right=10,top=15,bottom=25;
  const X=i=>left+i*(w-left-right)/7; const Y=v=>h-bottom-v/max*(h-top-bottom);
  const pts=bins.map((v,i)=>[X(i),Y(v)]); const line=pts.map(([x,y],i)=>`${i?'L':'M'}${x},${y}`).join(' ');
  const generated=new Date(state.snapshot.generated_at);
  const grid=[0,.5,1].map(f=>`<line x1="${left}" x2="${w-right}" y1="${Y(max*f)}" y2="${Y(max*f)}" stroke="#29303b" stroke-dasharray="3 5"/><text x="${left-7}" y="${Y(max*f)+3}" text-anchor="end" fill="#59677d" font-size="8">${Math.round(max*f)}</text>`).join('');
  const labels=[0,2,4,6,7].map(i=>`<text x="${X(i)}" y="${h-5}" text-anchor="middle" fill="#63718a" font-size="8">${new Date(generated.getTime()-(7-i)*900000).toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'})}</text>`).join('');
  $('#trend-chart').innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${e(t.title)}最近两小时每15分钟样本：${bins.join('、')}"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#deb263" stop-opacity=".2"/><stop offset="100%" stop-color="#deb263" stop-opacity="0"/></linearGradient></defs>${grid}<path d="${line} L${X(7)},${Y(0)} L${X(0)},${Y(0)} Z" fill="url(#chart-fill)"/><path d="${line}" fill="none" stroke="#e3b66a" stroke-width="2"/>${pts.map(([x,y],i)=>`<circle cx="${x}" cy="${y}" r="${i===7?3.5:2}" fill="#e3b66a"><title>${bins[i]} 条</title></circle>`).join('')}${labels}</svg>`;
}
function renderDetail() {
  const t=topic(); if(!t) { $('#topic-detail').innerHTML=empty('选择一个话题，追踪原帖和研判证据'); return; }
  const a=t.assessment || {};
  const safeLink = url => { try {const p=new URL(url); return p.protocol==='https:'&&['x.com','www.x.com','twitter.com','www.twitter.com'].includes(p.hostname)&&/^\/[A-Za-z0-9_]+\/status\/\d+\/?$/.test(p.pathname)&&!p.username;} catch {return false;} };
  $('#topic-detail').innerHTML=`<div class="detail-topline">${tag(t)}<span class="mono">INTEL #${e(t.id.slice(0,6).toUpperCase())}</span></div><h3>${e(t.title)}</h3><p class="verification">◇ ${e(t.verification)}</p><div class="detail-stats"><div><b class="amber">${t.score}</b><small>趋势分 / 100</small></div><div><b>${t.cold_start?'—':`${t.growth>0?'+':''}${t.growth.toFixed(0)}%`}</b><small>15 分钟环比</small></div><div><b>${t.authors}</b><small>当前窗口作者</small></div></div><div class="assessment"><h4>↗ 机会观察 <span class="engine-label">${a.engine==='llm'?'本地模型':'规则分析'}</span></h4><p>${e(a.opportunity || '等待研判')}</p></div><div class="assessment skeptic"><h4>◇ 反方视角</h4><p>${e(a.skeptic || '等待研判')}</p></div>${a.fallback?`<p class="field-help">${e(a.fallback)}</p>`:''}<div class="evidence-title">原始证据 <small>${t.evidence.length} SOURCES</small></div>${t.evidence.map(p=>`<article class="evidence-card"><div class="evidence-meta"><span>@${e(p.author)}</span><time>${time(p.created_at)}</time></div><p>${e(p.text)}</p>${safeLink(p.url)?`<a href="${e(p.url)}" target="_blank" rel="noopener noreferrer">查看 X 原帖 ↗</a>`:'<small>模拟样本 · 无真实原帖</small>'}</article>`).join('')}`;
}
function agentState(id) {
  const run = state.active || state.runs.find(r=>r.scope===state.scope);
  const events=state.events.filter(ev=>ev.run_id===run?.id && ev.agent===id && ev.state!=='info');
  const last=events.at(-1);
  if(last?.state==='running' && run && run.state!=='running') return run.state;
  return last?.state || 'idle';
}
function renderAgents() {
  $('#agent-flow').innerHTML=state.agents.map((a,i)=>{
    const status=agentState(a.id);
    return `<div class="agent-card ${e(status)}"><div class="agent-card-top"><span class="agent-symbol">${agentIcons[i]}</span><span class="agent-state">${states[status]||'待命'}</span></div><strong>${e(a.name)}</strong><small>${e(['SCOUT','CLUSTER','TREND','VERIFY','ANALYST','EDITOR'][i])}</small></div>`;
  }).join('');
  const run=state.active||state.runs.find(r=>r.scope===state.scope); $('#run-state').textContent=states[run?.state]||'待命中';
}
function renderActivity() {
  $('#activity-list').innerHTML=state.events.length ? [...state.events].reverse().slice(0,40).map(ev=>`<div class="activity-row"><time>${time(ev.time)}</time><span>${e(state.agents.find(a=>a.id===ev.agent)?.name||ev.agent)}</span><span class="event-state ${e(ev.state)}">${states[ev.state]||e(ev.state)}</span><span>${e(ev.message)}</span></div>`).join('') : empty('任务电台待命，扫描开始后显示各阶段进展');
}
function renderOtherViews() {
  const cap=state.capabilities, s=state.settings;
  $('#sources-view').innerHTML=`<div class="view-grid">${[
    ['OpenCLI 最新动态','opencli',cap.opencli,'推荐免费通道。只读使用已登录的 Chrome，明确选择 X 最新动态（Latest）标签，更适合监控正在起势的话题。'],
    ['Agent Reach','agent_reach',cap.agent_reach,'通过本机 Agent Reach 统一接口搜索 X。当前上游默认热门排序，作为热门搜索抽样，不能代表最新时间线。'],
    ['Ollama 本地研判','ollama',cap.llm,'可选的本地模型。机会与质疑两个角色交叉分析，仅连接本机 Ollama，不需要云端 API Key。'],
    ['演示沙盘','demo',true,'合成 AI 讨论样本，用于体验趋势雷达、研判流程与任务电台。与真实数据隔离，不代表当前新闻。']
  ].map(([name,key,ready,desc])=>`<article class="panel info-card"><div class="source-title"><span class="eyebrow">${e(key.toUpperCase())}</span><span class="source-status">${ready?'● '+(['agent_reach','opencli'].includes(key)?'已安装，登录待检查':'可用'):'○ 待配置'}</span></div><h3>${name}</h3><p>${desc}</p><span class="badge">${s.mode===key?'当前使用':key==='ollama'?(s.llm_enabled?'已启用':'可选增强'):'未启用'}</span></article>`).join('')}</div><section class="panel activity-panel"><div class="panel-header"><h2>免费接入 <small>GET CONNECTED</small></h2></div><div class="query-list"><p>1. 安装 Node.js ≥ 20.18.1 后，在终端运行 <code>npm install -g @jackwener/opencli</code>。</p><p>2. 在 Chrome 安装 <a class="amber" href="https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk" target="_blank" rel="noopener noreferrer">官方 OpenCLI 扩展 ↗</a>，并自行登录 X。</p><p>3. 运行 <code>opencli doctor</code> 检查连接，保持 Chrome 打开，然后点击立即扫描。</p><p class="muted">已有 Agent Reach 可在配置中选择；本地 Ollama 是可选增强，不安装也能用。</p></div></section><section class="panel activity-panel"><div class="panel-header"><h2>监控查询 <small>WATCH QUERIES</small></h2><button class="button secondary" data-config>编辑查询</button></div><div class="query-list">${s.queries.map((q,i)=>`<div class="query-line"><span class="amber mono">0${i+1} / </span>${e(q)}</div>`).join('')}</div></section><div class="view-notice">当前节奏：${s.auto_scan?'自动扫描，每 '+s.interval_seconds+' 秒一次':'手动扫描'} · 每查询最多 ${s.result_limit} 条样本 · 崛起门槛 ${s.min_authors} 位独立作者。页面关闭后，服务继续运行即可保持监控。</div>`;
  $('#agents-view').innerHTML=`<div class="view-grid">${state.agents.map((a,i)=>`<article class="panel info-card"><div class="source-title"><span class="eyebrow">AGENT / 0${i+1}</span><span class="badge">${states[agentState(a.id)]||'待命'}</span></div><h3>${agentIcons[i]} ${e(a.name)}</h3><p>${e(a.role)}</p><span class="badge">${e(a.kind==='rules_or_llm'?(s.llm_enabled?'规则 + 可选模型':'规则分析'):a.kind==='tool'?'只读工具':'确定性规则')}</span></article>`).join('')}</div><div class="view-notice">每轮采集最多 2 个查询并发；核验并行执行。启用模型后，前 5 个话题分别由机会与质疑角色并行分析，最多 2 个话题同时调用；模型引用必须来自本轮证据。${cap.llm?'已配置模型：'+e(cap.llm_model):'尚未配置本地 Ollama，当前使用免费规则流程。'}</div><button class="button secondary" data-config>配置模型与执行参数</button>`;
  $('#runs-view').innerHTML=`<section class="panel"><div class="panel-header"><h2>运行档案 <small>MISSION ARCHIVE</small></h2><span class="badge">最近 ${state.runs.length} 次</span></div><div class="table-scroll">${state.runs.length?`<table class="run-table"><thead><tr><th>任务编号</th><th>开始时间（上海）</th><th>模式</th><th>耗时</th><th>状态</th><th>执行信息</th></tr></thead><tbody>${state.runs.map(r=>`<tr><td class="mono">${e(r.id)}</td><td>${date(r.started)}</td><td>${e(modes[r.mode]||r.mode)}</td><td>${r.finished?((new Date(r.finished)-new Date(r.started))/1000).toFixed(1)+'s':'—'}</td><td>${states[r.state]||e(r.state)}</td><td class="run-error">${e(r.error||'—')}</td></tr>`).join('')}</tbody></table>`:empty('尚无运行记录')}</div></section><p class="view-notice">运行记录和阶段事件保存在本机。失败或取消的扫描不会覆盖上一份完整情报。</p>`;
}
function render() {
  if(!state) return;
  const s=state.settings, snap=state.snapshot, topics=snap?.topics||[];
  if(!topics.some(t=>t.id===selected)) selected=topics[0]?.id||null;
  $('#mode-label').textContent=s.mode==='demo'?'DEMO':'X MONITOR';
  $('#mode-description').textContent=s.mode==='demo'?'演示沙盘 · 当前为合成样本，不代表 X 实时热点。配置真实情报源后开始监控。':`${modes[s.mode]} · ${s.auto_scan?'每 '+s.interval_seconds+' 秒自动扫描':'手动扫描'} · 查询范围内观测${snap?' · 上次更新 '+time(snap.generated_at):' · 等待首次扫描'}`;
  const lastRun=state.runs.find(r=>r.scope===state.scope);
  if(lastRun?.state==='failed') $('#mode-description').textContent += ` · 最近扫描失败：${lastRun.error}${snap?'，保留上次完整结果':''}`;
  if(state.cooldown_until*1000>Date.now()) $('#mode-description').textContent += ' · 限流冷却至 '+time(new Date(state.cooldown_until*1000).toISOString());
  $('#metric-topics').textContent=String(topics.length).padStart(2,'0');
  $('#metric-rising').textContent=String(topics.filter(t=>t.state==='加速崛起').length).padStart(2,'0');
  $('#metric-posts').textContent=snap?fmt(snap.sample_count):'—';
  $('#metric-coverage').textContent=snap?({synthetic:'合成数据 · 演示专用',sampled:'抽样覆盖 · 非全站统计',ranked_sampled:'热门排序抽样 · 非最新时间线'}[snap.coverage]||snap.coverage):'等待首次扫描';
  $('#metric-engine').textContent=s.llm_enabled?'前 5 话题 · 本地 Ollama 双视角':'工具与规则模式 · 模型未启用';
  $('#scan').disabled=!!state.active; $('#scan').innerHTML=state.active?'扫描执行中…':'<span>⌁</span> 立即扫描';
  $('#cancel').classList.toggle('hidden',!state.active);
  $('#export').disabled=!snap;
  $('#last-update').textContent=snap?`LAST SCAN ${time(snap.generated_at)} CST`:'等待运行';
  renderTopics(); renderChart(); renderDetail(); renderAgents(); renderActivity(); renderOtherViews();
}
async function refresh() { const next=await api('/state'); const previous=state; state=next; const comparable=x=>x?JSON.stringify({...x,server_time:''}):''; if(comparable(previous)!==comparable(next)) render(); }
async function scan() {
  $('#scan').disabled=true;
  try { await api('/runs',{method:'POST'}); await refresh(); } catch(err) {toast(err.message); $('#scan').disabled=false;}
}
function openSettings() {
  const s=state.settings, f=$('#settings-form');
  for(const [k,v] of Object.entries(s)) if(f.elements[k]) {
    const el=f.elements[k]; if(el.type==='checkbox') el.checked=v; else el.value=Array.isArray(v)?v.join('\n'):v;
  }
  $('#settings-error').textContent=''; updateHint(); $('#settings-dialog').showModal();
}
function updateHint() {
  const mode=$('#settings-form').elements.mode.value, cap=state.capabilities;
  $('#credentials-hint').textContent=mode==='demo'?'无需凭据，运行合成样本。':mode==='opencli'?(cap.opencli?'已安装 OpenCLI。请保持 Chrome 打开，安装官方 Browser Bridge 扩展并自行登录 X。':'尚未安装 OpenCLI。请先按情报源页的免费接入步骤安装，无需购买 API。'):(cap.agent_reach?'检测到 Agent Reach。请自行登录 X；此入口为热门排序抽样。':'尚未安装 Agent Reach。请按官方说明安装当前版本，并自行登录 X；无需购买 API。');
}
function changeView(next) {
  view=next; document.querySelectorAll('.view').forEach(x=>x.classList.toggle('hidden',x.id!==next+'-view'));
  document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x.dataset.view===next));
  const labels={command:['情报作战台','捕捉噪声中的信号，发现正在起势的下一个话题。'],sources:['监控情报源','定义你的观测范围，让每一条信号都有出处。'],agents:['Agent 编队','看见每个角色的职责、执行状态和研判方式。'],runs:['运行档案','保留每一轮任务的结果，追踪失败与恢复。']};
  $('#page-title').firstChild.textContent=labels[next][0]; $('#page-description').textContent=labels[next][1];
}
function connectEvents() {
  if(stream) stream.close();
  stream=new EventSource('/api/events');
  stream.onopen=()=>{ $('#connection').textContent='● 事件流已连接'; $('#connection').classList.remove('disconnected'); };
  stream.onerror=()=>{ $('#connection').textContent='○ 正在重连'; $('#connection').classList.add('disconnected'); };
  let pending;
  stream.onmessage=()=>{ clearTimeout(pending); pending=setTimeout(()=>refresh().catch(err=>toast(err.message)),150); };
}
$('#scan').addEventListener('click',scan);
$('#cancel').addEventListener('click',async()=>{try{await api('/runs/cancel',{method:'POST'});await refresh();toast('扫描已停止');}catch(err){toast(err.message);}});
$('#filters').addEventListener('click',ev=>{const b=ev.target.closest('[data-filter]');if(!b)return;filter=b.dataset.filter;$('#filters').querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x===b));renderTopics();});
$('#search').addEventListener('input',renderTopics); $('#sort').addEventListener('change',renderTopics);
$('#topic-list').addEventListener('click',ev=>{const b=ev.target.closest('[data-topic]');if(!b)return;selected=b.dataset.topic;renderTopics();renderChart();renderDetail();});
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>changeView(b.dataset.view)));
$('#configure').addEventListener('click',openSettings); $('#settings-nav').addEventListener('click',openSettings);
document.addEventListener('click',ev=>{if(ev.target.closest('[data-config]'))openSettings();});
document.querySelectorAll('.close-dialog').forEach(b=>b.addEventListener('click',()=>$('#settings-dialog').close()));
$('#settings-form').elements.mode.addEventListener('change',updateHint);
$('#settings-form').addEventListener('submit',async ev=>{
  ev.preventDefault();const f=ev.target;
  const data={mode:f.elements.mode.value,queries:f.elements.queries.value.split('\n').map(q=>q.trim()).filter(Boolean),interval_seconds:Number(f.elements.interval_seconds.value),result_limit:Number(f.elements.result_limit.value),min_authors:Number(f.elements.min_authors.value),auto_scan:f.elements.auto_scan.checked,llm_enabled:f.elements.llm_enabled.checked};
  try{await api('/settings',{method:'PUT',body:JSON.stringify(data)});$('#settings-dialog').close();await refresh();toast('监控配置已保存');}catch(err){$('#settings-error').textContent=err.message;}
});
$('#export').addEventListener('click',async()=>{try{const r=await fetch('/api/brief',{headers:{'X-Intel-Token':token}});if(!r.ok)throw new Error('简报导出失败，请先完成扫描');const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download='ai-info-brief.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('情报简报已导出');}catch(err){toast(err.message);}});
$('#fullscreen').addEventListener('click',async()=>{
  const enable=!document.body.classList.contains('wall-mode');
  document.body.classList.toggle('wall-mode',enable);
  $('#fullscreen').setAttribute('aria-pressed',String(enable));
  try{if(enable&&!document.fullscreenElement)await document.documentElement.requestFullscreen();else if(!enable&&document.fullscreenElement)await document.exitFullscreen();}catch{/* The compact wall layout also works when native fullscreen is unavailable. */}
});
document.addEventListener('keydown',ev=>{if(ev.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('#settings-dialog').open){ev.preventDefault();changeView('command');$('#search').focus();}});
setInterval(()=>{$('#clock').textContent=new Date().toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}).replaceAll('/','.');},1000);
(async()=>{
  try{token=(await (await fetch('/api/session')).json()).token;await refresh();connectEvents();if(!state.snapshot&&state.settings.mode==='demo'&&!state.active)await scan();setInterval(()=>refresh().catch(()=>{}),15000);}
  catch(err){$('#connection').textContent='○ 连接失败';$('#connection').classList.add('disconnected');toast(err.message);$('#mode-description').textContent='无法连接服务，请检查后端并刷新页面。';}
})();
