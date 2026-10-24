'use strict';
const $ = s => document.querySelector(s);
let state = null, detail = null, selectedRun = null, currentView = 'briefing', settingsBase = null, toastTimer, pollTimer, pending = false, detailRequest = 0;
const statusNames = {completed:'已生成', empty:'暂无推荐', preview:'采集预览', failed:'运行失败', started:'正在采集', collected:'正在处理'};
const feedbackNames = {useful:'有用', not_useful:'没用', later:'暂时不想看'};
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = v => {const d = new Date(v);return Number.isNaN(d.getTime())?'时间未知':d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'long',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});};
const duration = s => s == null ? '时长未知' : s >= 3600 ? `${Math.floor(s/3600)} 小时 ${Math.floor(s%3600/60)} 分` : `${Math.floor(s/60)} 分 ${s%60} 秒`;
const safeUrl = u => {try {const x=new URL(u);return ['http:','https:'].includes(x.protocol)?x.href:'#';}catch{return '#';}};
function toast(message, error=false) { const el=$('#toast'); el.textContent=message; el.classList.toggle('error',error); el.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.hidden=true,5000); }
async function api(path, body) {
  let response;
  try { response = await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-Info-Agent-Token':state?.token||''},body:body===undefined?undefined:JSON.stringify(body)}); }
  catch {throw new Error('无法连接本地服务，请确认界面启动进程仍在运行。');}
  const data=await response.json(); if(!response.ok) throw new Error(data.error||'操作未完成，请稍后重试。'); return data;
}
function setView(view) {
  if(!['briefing','history','preferences','settings'].includes(view)) view='briefing';
  currentView=view; document.querySelectorAll('.view').forEach(x=>x.hidden=x.id!==`view-${view}`);
  document.querySelectorAll('nav [data-view]').forEach(x=>x.classList.toggle('active',x.dataset.view===view));
  const names={briefing:'我的简报',history:'历史记录',preferences:'偏好学习',settings:'兴趣设置'};
  $('#breadcrumb').textContent=`工作台 / ${names[view]}`;
  history.replaceState(null,'',`#${view}`);
  if(view==='settings'&&state) fillSettings();
}
function updateButtons(){const busy=pending||state?.job?.status==='running';$('#generate').disabled=busy;$('#preview').disabled=busy;$('#save-settings').disabled=busy;document.querySelectorAll('[data-action="generate"]').forEach(b=>b.disabled=busy);}
function renderJob(){
  const j=state?.job, el=$('#job-panel'); el.hidden=!j||!['running','failed'].includes(j.status);
  if(j?.status==='running') el.innerHTML=`<span class="spinner" aria-hidden="true"></span><div>${j.preview?'正在采集候选内容':'正在生成你的简报'}<small>后台处理期间可以浏览历史。${j.preview?'本次不会调用模型。':'本次将使用已配置的模型，请耐心等待。'}</small></div>`;
  if(j?.status==='failed') el.innerHTML=`<div>${esc(j.error)}<small>采集预览不调用模型；如预览失败，请先检查 RSSHub 本机转发。</small><button class="text-button" data-view="history">查看失败记录 →</button></div>`;
  updateButtons();
}
async function refresh(loadDetail=true){
  state=await api('/api/state');$('#connection-error').hidden=true;
  $('#keyword-chips').innerHTML=state.profile.keywords.map(k=>`<span class="chip">${esc(k)}</span>`).join('');
  $('#feedback-count').textContent=state.stats.feedback_count;
  renderHistory();renderPreferences();renderJob();
  if(loadDetail){if(!selectedRun) selectedRun=state.runs.find(r=>r.current_context&&r.status==='completed')?.id || state.runs.find(r=>r.current_context)?.id || state.runs[0]?.id || null;await loadRun(selectedRun);}
  schedulePoll();
}
function schedulePoll(){clearTimeout(pollTimer);if(state?.job?.status==='running')pollTimer=setTimeout(poll,1600);}
async function poll(){
  const oldJob=state?.job?.id;
  try{await refresh(false);if(state.job?.id===oldJob&&state.job.status!=='running'){
    if(state.job.status==='completed'){selectedRun=state.job.run_id;await loadRun(selectedRun);toast(state.job.preview?'采集完成，候选已保存。':'简报已生成。');}
    else{toast(state.job.error,true);await refresh(false);}
  }}catch(e){$('#connection-error').textContent=e.message;$('#connection-error').hidden=false;pollTimer=setTimeout(poll,5000);}
}
async function loadRun(id){
  const request=++detailRequest;
  const result=id?await api('/api/runs/'+encodeURIComponent(id)):null;
  if(request!==detailRequest)return;
  detail=result;selectedRun=id;renderBriefing();
}
function empty(title, text, action=false){return `<div class="empty"><div class="empty-symbol" aria-hidden="true">▤</div><h2>${esc(title)}</h2><p>${esc(text)}</p>${action?'<button class="button primary" data-action="generate">生成第一份简报</button><button class="button secondary" data-view="settings">先设置我的兴趣</button>':''}</div>`;}
function renderBriefing(){
  const d=detail, download=$('#download');download.hidden=!d?.report||!['completed','empty'].includes(d?.status);
  if(!download.hidden)download.href='/api/runs/'+encodeURIComponent(d.id)+'/download';
  $('#briefing-heading').textContent=d?.status==='preview'?'采集预览':d?.status==='failed'?'运行记录':'我的简报';
  $('#briefing-meta').textContent=d?`${date(d.created)} · ${d.picks.length} 条推荐 · ${(d.metrics.candidate_count??d.candidates.length)} 条候选`: '你的下一份简报，从这里开始';
  const regular=d?.picks.filter(x=>x.lane!=='explore').length||0, explore=d?.picks.filter(x=>x.lane==='explore').length||0;
  $('#regular-count').textContent=regular;$('#explore-count').textContent=explore;
  $('#mix-bar').classList.toggle('no-data',!regular&&!explore);
  $('#mix-bar').innerHTML=Array.from({length:regular},()=>'<span></span>').join('')+Array.from({length:explore},()=>'<span class="explore"></span>').join('');
  if(!d){$('#briefing-body').innerHTML=empty('还没有简报，先拾取一点灵感。','设置兴趣后生成第一份简报。也可以先采集预览，检查 RSSHub 是否连接正常。',true);updateButtons();return;}
  let html='';
  if(!d.current_context)html+='<div class="alert">这份记录来自以前的兴趣设置，可以阅读和导出；反馈请在当前兴趣的简报中提交。</div>';
  if(d.failures.length)html+=`<div class="alert">本次采集不完整：${d.failures.map(esc).join('；')}</div>`;
  if(d.preference_update_error)html+='<div class="alert">后台偏好暂未更新，已使用最近的有效版本。</div>';
  if(d.status==='failed')html+=`<div class="alert error">本次运行未完成。阶段：${esc(d.failed_stage||'未知')}；错误类型：${esc(d.error_type||'未知')}。已取得的候选仍保留，可检查连接和模型配置后重新生成。</div>`;
  if(d.picks.length)html+=d.picks.map(card).join('');
  else if(d.status==='empty')html+=empty('这一轮，没有合适的推荐。','可能是近期已推荐、时长限制或内容不匹配。我们保留空缺，不用无关内容填满简报。');
  else if(d.status==='preview')html+='<p class="candidate-info">以下是采集和过滤后的候选，尚未经过模型推荐。本次没有调用模型，也不会占用推荐历史。</p>';
  if(!d.picks.length&&d.candidates.length)html+=d.candidates.map((a,i)=>`<article class="candidate-card"><div class="card-source">候选 ${i+1} · ${esc(a.author)} · ${duration(a.duration_seconds)}</div><h3><a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${esc(a.title)} ↗</a></h3><p class="card-summary">${esc(a.excerpt||'来源未提供简介。')}</p><div class="card-tags">${(a.tags||[]).slice(0,8).map(x=>`<span># ${esc(x)}</span>`).join('')}</div></article>`).join('');
  if(!d.picks.length&&!d.candidates.length&&d.status!=='empty')html+=empty('暂无可显示内容','采集记录会保留在历史中，请检查 RSSHub 连接或稍后再试。');
  const m=d.metrics;
  html+=`<details class="run-diagnostics"><summary>查看运行信息</summary><ul><li>运行编号：${esc(d.id)}</li><li>状态：${esc(statusNames[d.status]||d.status)}</li><li>逻辑模型调用：${m.logical_model_calls??'未知'} 次</li><li>耗时：${Number.isFinite(m.elapsed_seconds)?m.elapsed_seconds.toFixed(1)+' 秒':'未知'}</li><li>近期推荐过滤：${m.history_filtered??0} 条；时长过滤：${m.duration_filtered??0} 条</li><li>模型：${esc(m.model_name||'未记录')}</li></ul></details>`;
  $('#briefing-body').innerHTML=html;updateButtons();
}
function card(p){const a=p.article,f=p.feedback,isExplore=p.lane==='explore';return `<article class="brief-card" data-rec="${esc(p.recommendation_id)}"><div class="card-top"><span class="card-number">${String(p.position).padStart(2,'0')}</span><span class="badge ${isExplore?'explore':''}">${isExplore?'探索推荐':'为你精选'}</span><span class="card-time">◷ ${duration(a.duration_seconds)}</span></div><h3><a href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a></h3><div class="card-source"><span>${esc(a.author||'未知作者')}</span><span>${a.published_at?date(a.published_at):esc(a.published||'时间未知')}</span></div><p class="card-summary">${esc(p.summary)}</p><div class="reason"><strong>${isExplore?'探索理由':'为什么推荐'}</strong><span>${esc(isExplore?p.exploration_reason||p.reason:p.reason)}</span></div><div class="card-tags">${(a.tags||[]).slice(0,5).map(x=>`<span># ${esc(x)}</span>`).join('')}</div>${p.action?`<details class="muted"><summary>建议行动</summary>${esc(p.action)}</details>`:''}<div class="card-bottom">${Object.entries(feedbackNames).map(([v,n])=>`<button class="feedback-btn ${f?.value===v?'selected':''}" data-feedback="${v}" ${!detail.current_context?'disabled':''} aria-pressed="${f?.value===v}">${v==='useful'?'＋ ':v==='later'?'◷ ':''}${n}</button>`).join('')}${f&&detail.current_context?`<button class="text-button" data-undo="${esc(f.id)}">撤销</button>`:''}<a class="open-source" href="${esc(safeUrl(a.url))}" target="_blank" rel="noopener noreferrer">打开视频 ↗</a></div>${f?`<div class="feedback-state">已记录：${esc(feedbackNames[f.value])}${f.reason?' · '+esc(f.reason):''}</div>`:''}${f?.value==='not_useful'&&detail.current_context?`<div class="feedback-reason"><label>原因可选 <select data-reason><option value="">不补充原因</option>${['不感兴趣','太浅','太深','已经知道','质量不好','太长','太短'].map(x=>`<option value="${x}" ${f.reason===x?'selected':''}>${x}</option>`).join('')}</select></label><span>不需要额外确认，后台会自动处理。</span></div>`:''}</article>`;}
function renderHistory(){
  $('#history-body').innerHTML=state.runs.length?`<p class="candidate-info">最近 ${state.runs.length} 次运行 · 点击一条记录查看详情</p><div class="history-list">${state.runs.map(r=>`<button class="history-row" data-run="${esc(r.id)}"><span class="history-status ${r.status==='failed'?'failed':''}">${esc(statusNames[r.status]||r.status)}</span><span class="run-info"><strong>${date(r.created)}${!r.current_context?'<span class="old-label">历史兴趣</span>':''}</strong><small>${esc(r.keywords.join(' / ')||'未记录关键词')} · ${esc(r.id)}</small></span><span class="history-count">${r.count} 推荐 / ${r.candidates} 候选</span><span class="history-arrow">↗</span></button>`).join('')}</div>`:empty('还没有运行记录','第一次采集或生成后，结果会出现在这里。');
}
function renderPreferences(){
  const s=state.stats, prefs=state.preferences, entries=Object.entries(prefs.weights),enabled=state.profile.learning.enabled;
  const name=k=>k.startsWith('tag:')?k.slice(4):k.startsWith('duration:')?'时长区间 '+k.slice(9):k;
  $('#preferences-body').innerHTML=`<div class="stats-grid"><div class="stat-tile"><span>有效反馈</span><strong>${s.feedback_count}</strong><small>包含暂时不想看</small></div><div class="stat-tile"><span>已评价内容的有用率</span><strong>${s.useful_rate==null?'—':Math.round(s.useful_rate*100)+'%'}</strong><small>基于 ${s.evaluated} 条有用 / 没用评价</small></div><div class="stat-tile"><span>已激活偏好</span><strong>${entries.length}</strong><small>${enabled?'自动学习已开启':'当前不应用学习权重'}</small></div></div><section class="preference-section"><div class="section-heading"><h2>当前偏好</h2><span class="muted">${prefs.version?'版本 '+prefs.version:'尚未生成版本'}</span></div>${!enabled?'<div class="alert">自动学习的排序影响已关闭。反馈仍会保留，后端仍可能汇总证据。</div>':''}${entries.length?entries.map(([k,v])=>`<div class="weight-row"><span>${esc(name(k))}</span><span>${v>0?'适当增加':'适当减少'} · ${v>0?'+':''}${Number(v).toFixed(2)}</span></div>`).join(''):'<p class="muted">还没有足够证据形成稳定偏好，这是正常的。不同日期、不同内容上的一致反馈积累后，系统才会小幅调整推荐。</p>'}<p class="field-note">偏好仅在当前兴趣上下文中生效，探索推荐不应用这些调整。</p></section><div class="principles"><div><h3>一次反馈，不急着下结论</h3><p>限制同一天连续反馈的影响，避免一时疲惫改变长期推荐。</p></div><div><h3>能纠正，也会淡化</h3><p>相反反馈抵消旧证据；撤销后重新计算，较早的证据逐渐减弱。</p></div><div><h3>明确兴趣，始终优先</h3><p>只有限调整常规排序，不生成永久黑名单。你可以随时修改兴趣设置。</p></div></div><p class="field-note">反馈率与有用率受主动评价习惯影响，不代表所有推荐的真实准确率。当前累计推荐 ${s.recommendations} 条。</p>`;
}
function fillSettings(){const p=state.profile;settingsBase=structuredClone(p);$('#keywords').value=p.keywords.join(', ');$('#interests').value=p.interests;$('#max-picks').value=p.max_picks;$('#max-duration').value=p.max_duration_seconds==null?'':p.max_duration_seconds/60;$('#exploration').value=p.exploration_ratio*100;$('#cooldown').value=p.history_cooldown_days;$('#learning').checked=p.learning.enabled;$('#settings-message').textContent='';updateButtons();}
async function startRun(preview=false){if(pending)return;pending=true;updateButtons();try{const j=await api('/api/run',{preview});state.job=j;setView('briefing');renderJob();schedulePoll();}catch(e){toast(e.message,true);}finally{pending=false;updateButtons();}}
async function sendFeedback(button, value, reason='', undo=null){const cardEl=button.closest('[data-rec]');if(!cardEl||cardEl.dataset.busy)return;cardEl.dataset.busy='true';cardEl.querySelectorAll('button,select').forEach(x=>x.disabled=true);try{const payload=undo?{event:undo,key:crypto.randomUUID()}:{recommendation:cardEl.dataset.rec,value,reason,key:crypto.randomUUID()};const r=await api(undo?'/api/undo':'/api/feedback',payload);await refresh();toast(r.warning||(undo?'已撤销，偏好证据已重新计算。':'反馈已记录，后台自动更新。'));}catch(e){toast(e.message,true);cardEl.querySelectorAll('button,select').forEach(x=>x.disabled=false);}finally{delete cardEl.dataset.busy;}}
document.addEventListener('click',async e=>{const nav=e.target.closest('[data-view]');if(nav){setView(nav.dataset.view);return;}const run=e.target.closest('[data-run]');if(run){try{await loadRun(run.dataset.run);setView('briefing');window.scrollTo({top:0,behavior:'smooth'});}catch(err){toast(err.message,true);}return;}const fb=e.target.closest('[data-feedback]');if(fb){await sendFeedback(fb,fb.dataset.feedback);return;}const undo=e.target.closest('[data-undo]');if(undo){await sendFeedback(undo,null,'',undo.dataset.undo);return;}if(e.target.closest('[data-action="generate"]'))startRun(false);});
document.addEventListener('change',e=>{if(e.target.matches('[data-reason]'))sendFeedback(e.target,'not_useful',e.target.value);});
$('#generate').addEventListener('click',()=>startRun(false));$('#preview').addEventListener('click',()=>startRun(true));
$('#refresh-history').addEventListener('click',async()=>{try{await refresh(false);toast('历史记录已刷新。');}catch(e){toast(e.message,true);}});
$('#settings-form').addEventListener('submit',async e=>{e.preventDefault();if(pending)return;pending=true;updateButtons();try{const raw=$('#max-duration').value;const changes={keywords:$('#keywords').value.split(/[,，\n]/).map(x=>x.trim()).filter(Boolean),interests:$('#interests').value,max_picks:Number($('#max-picks').value),max_duration_seconds:raw===''?null:Math.round(Number(raw)*60),exploration_ratio:Number($('#exploration').value)/100,history_cooldown_days:Number($('#cooldown').value),learning_enabled:$('#learning').checked};const r=await api('/api/profile',{expected:settingsBase,changes});await refresh(false);fillSettings();$('#settings-message').textContent=r.changed_context?'设置已保存，已启用新的学习上下文。':'设置已保存，下次运行生效。';if(r.changed_context){selectedRun=null;await loadRun(state.runs.find(x=>x.current_context)?.id||null);}toast('兴趣设置已保存。');}catch(err){$('#settings-message').textContent=err.message;toast(err.message,true);}finally{pending=false;updateButtons();}});
$('#today').textContent=new Date().toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'long',day:'numeric',weekday:'long'});
(async()=>{setView(location.hash.slice(1));try{await refresh();if(currentView==='settings')fillSettings();}catch(e){$('#connection-error').textContent=e.message;$('#connection-error').hidden=false;$('#briefing-body').innerHTML=empty('暂时无法读取数据',e.message);}})();
