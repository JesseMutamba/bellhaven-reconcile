const $ = (id) => document.getElementById(id);
const labels = {chow:'Ownership · preserve billing', reparent:'Correct parent', update:'Update facility', create:'New facility', duplicate:'Duplicate account', missing:'Missing from website', investigate:'Needs investigation'};
const fieldLabels = {name:'Facility name', address:'Street', city:'City', state:'State', zip:'ZIP', care_offerings:'Care offerings', parent_id:'Parent account', status:'Status', note:'Account note', duplicate_of_account:'Surviving account'};
let state, selected, busy = false, reviewer = '', note = '';
const escapeHTML = (value) => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const display = (value) => Array.isArray(value) ? value.join(', ') : value ?? '—';
const money = value => value == null ? 'Unknown' : new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(value);
const date = value => value ? new Date(value).toLocaleString() : '—';
function message(id,text){$(id).textContent=text;$(id).hidden=!text;}
async function api(path, body){
  const response = await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Review-Token':state.review_token}:{},body:body?JSON.stringify(body):undefined});
  const data = await response.json();
  if(!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
async function refresh(){state=await api('/api/state');render();}
function render(){
  $('mode').textContent=state.mode==='demo'?'Demo workspace · local CRM':'Assessment sandbox · read only';
  const run=state.last_run, summary=run?.summary;
  $('run-summary').textContent=run?`${run.status==='complete'?'Last reconciled':'Last run '+run.status} ${date(run.finished_at||run.started_at)}`:'Run reconciliation to build your review queue.';
  $('facilities').textContent=summary?.facilities??'—';$('matches').textContent=summary?.matched?.length??'—';
  $('pending').textContent=state.proposals.filter(p=>p.state==='pending').length;$('applied').textContent=state.proposals.filter(p=>p.state==='applied').length;
  $('scan').disabled=busy;
  if(state.mode==='demo' && !$('notice').textContent) message('notice','Demo data. Approvals update only the local fictional CRM.');
  if(state.mode!=='demo') message('notice','Sandbox account reads are connected. Live writes need authenticated API schema verification before they can be enabled.');
  if(run?.status==='failed') message('error',run.error);
  const filter=$('filter').value;
  const list=state.proposals.filter(p=>filter==='all'||(filter==='pending'?['pending','retryable','applying','uncertain','stale'].includes(p.state):p.state===filter));
  if(!list.some(p=>p.id===selected)){selected=list[0]?.id;note='';}
  $('queue').innerHTML=list.length?list.map(p=>`<button class="item ${selected===p.id?'selected':''}" data-proposal="${p.id}" aria-pressed="${selected===p.id}"><span class="item-top"><span class="badge ${p.kind}">${labels[p.kind]}</span><span class="state">${escapeHTML(p.state)}</span></span><span class="item-name">${escapeHTML(p.title)}</span><span class="item-sub">${escapeHTML(p.plan.facility.city)}, ${escapeHTML(p.plan.facility.state)} · ${escapeHTML(p.plan.facility.zip)}</span></button>`).join(''):'<div class="empty"><h2>Queue is clear</h2><p>No proposals in this view. Run reconciliation or view all decisions.</p></div>';
  renderDetail();
}
function renderDetail(){
  const p=state.proposals.find(p=>p.id===selected);
  if(!p){$('detail').innerHTML='<div class="empty"><h2>Ready for the next review</h2><p>Choose a proposal to compare CRM data with the website evidence.</p></div>';return;}
  const {facility:f,before,changes,evidence,candidates}=p.plan;
  const rows=Object.entries(changes).map(([key,value])=>`<tr><td>${escapeHTML(fieldLabels[key]||key)}</td><td>${escapeHTML(display(p.kind==='create'||p.kind==='chow'?null:before?.[key]))}</td><td>${escapeHTML(display(value))}</td></tr>`).join('');
  const source=f.source_url && /^https?:\/\//.test(f.source_url)?`<a class="source" href="${escapeHTML(f.source_url)}" target="_blank" rel="noopener noreferrer">View source page ↗</a>`:'<span class="source-meta">Evidence: complete portfolio crawl and CRM snapshot</span>';
  const candidatesHTML=candidates.length?`<h3>Candidate CRM accounts</h3><table><thead><tr><th>Account</th><th>Address</th><th>Parent</th></tr></thead><tbody>${candidates.map(a=>`<tr><td>${escapeHTML(a.name)}<br>${escapeHTML(a.id)}</td><td>${escapeHTML(a.address)}</td><td>${escapeHTML(a.parent_id)}</td></tr>`).join('')}</tbody></table>`:'';
  const form=p.state==='pending'||['retryable','applying'].includes(p.state)?`<form id="review-form" class="review"><label for="reviewer">Reviewer name</label><input id="reviewer" name="reviewer" value="${escapeHTML(reviewer)}" required maxlength="120" autocomplete="name" placeholder="Your name"><label for="reason">Review note</label><textarea id="reason" name="reason" maxlength="2000" placeholder="Required when rejecting or recording an investigation">${escapeHTML(note)}</textarea><div class="actions">${p.state==='pending'?`<button type="submit" class="primary" name="decision" value="${p.kind==='investigate'?'reviewed':'approve'}" ${busy||(!state.writes_available&&p.kind!=='investigate')?'disabled':''}>${p.kind==='investigate'?'Record investigation':p.kind==='chow'?'Approve successor account':'Approve change'}</button><button type="submit" name="decision" value="reject" ${busy?'disabled':''}>Reject proposal</button>`:`<button type="submit" class="primary" name="decision" value="retry" ${busy?'disabled':''}>Retry approved operation</button>`}</div></form>`:`<div class="decision"><strong>${escapeHTML(p.state.replaceAll('_',' '))}</strong><p>${escapeHTML(p.reviewer||'System')} · ${escapeHTML(date(p.decided_at||p.created_at))}</p>${p.reason?`<p>${escapeHTML(p.reason)}</p>`:''}</div>`;
  $('detail').innerHTML=`<div class="detail-head"><span class="badge ${p.kind}">${labels[p.kind]}</span><h2>${escapeHTML(p.title)}</h2><p>${escapeHTML(f.address)}<br>${escapeHTML(f.city)}, ${escapeHTML(f.state)} ${escapeHTML(f.zip)}${before?` · CRM ${escapeHTML(before.id)}`:''}</p></div><div class="detail-body">${p.kind==='chow'?'<div class="billing"><strong>Keep the historical account intact</strong>Create a new facility account under Bellhaven, then link the old account to it. Its existing parent, revenue history, balance, name, and status remain unchanged.</div>':''}${before?`<div class="finances"><span>Lifetime revenue<b>${escapeHTML(money(before.lifetime_revenue))}</b></span><span>Outstanding AR<b>${escapeHTML(money(before.outstanding_ar))}</b></span>${p.kind==='chow'?`<span>Preserved parent<b>${escapeHTML(before.parent_id)}</b></span>`:''}</div>`:''}${rows?`<h3>${p.kind==='chow'?'New successor account':'Proposed changes'}</h3><table><thead><tr><th>Field</th><th>Current</th><th>Proposed</th></tr></thead><tbody>${rows}</tbody></table>`:''}${candidatesHTML}<h3>Supporting evidence</h3><ul class="evidence">${evidence.map(e=>`<li>${escapeHTML(e)}</li>`).join('')}</ul>${source}<div class="source-meta">Snapshot run ${p.run_id} · captured ${escapeHTML(date(p.created_at))}${f.source_sha256?` · SHA-256 ${escapeHTML(f.source_sha256.slice(0,12))}…`:''}</div>${p.error?`<div class="error">${escapeHTML(p.error)}</div>`:''}${form}<div id="audit" class="source-meta"></div></div>`;
  const formElement=$('review-form');
  if(formElement){$('reviewer').addEventListener('input',e=>reviewer=e.target.value);$('reason').addEventListener('input',e=>note=e.target.value);formElement.addEventListener('submit',decide);}
  api('/api/history/'+p.id).then(history=>{if(selected===p.id&&$('audit'))$('audit').textContent=history.length?'Audit: '+history.map(h=>`${h.event} · ${date(h.at)}`).join(' / '):'No decisions recorded.';}).catch(()=>{});
}
async function decide(event){
  event.preventDefault();if(busy)return;
  const decision=event.submitter?.value;if(!decision)return;
  const p=state.proposals.find(p=>p.id===selected);
  busy=true;message('error','');renderDetail();
  try{
    const result=await api('/api/decision',{id:p.id,decision,reviewer,reason:note});
    note='';message(result.error?'error':'notice',result.error||`${p.title}: ${result.state}.`);
    await refresh();
  }catch(error){message('error',error.message);}finally{busy=false;render();}
}
$('queue').addEventListener('click',event=>{const item=event.target.closest('[data-proposal]');if(item&&!busy){selected=item.dataset.proposal;note='';render();}});
$('filter').addEventListener('change',()=>{if(!busy)render();});
$('scan').addEventListener('click',async()=>{if(busy)return;busy=true;$('scan').disabled=true;message('error','');message('notice','Reading the website and comparing CRM accounts…');try{const result=await api('/api/scan',{});message('notice',`${result.facilities} facilities checked. ${result.new_proposals} new proposals. Previous decisions are preserved.`);await refresh();}catch(error){message('error',error.message);}finally{busy=false;render();}});
function registerAgentTools(){
  if(!document.modelContext?.registerTool)return;
  const lifecycle=new AbortController();
  const tools=[
    {name:'list_reconciliation_proposals',title:'List ownership proposals',description:'Read the same proposals and evidence shown in the review app. No CRM changes.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:async()=>{await refresh();return state.proposals.map(p=>({id:p.id,kind:p.kind,state:p.state,title:p.title,plan:p.plan}));}},
    {name:'open_reconciliation_proposal',title:'Open an ownership proposal',description:'Display a proposal and its supporting evidence. This does not approve or apply it.',inputSchema:{type:'object',properties:{id:{type:'string'}},required:['id'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute:async(input)=>{if(busy)throw new Error('An operation is running');if(!input||typeof input.id!=='string'||Object.keys(input).some(k=>k!=='id'))throw new Error('Expected a proposal id');await refresh();if(!state.proposals.some(p=>p.id===input.id))throw new Error('Proposal not found');$('filter').value='all';selected=input.id;render();return{id:selected,opened:true};}}
  ];
  for(const tool of tools){try{Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});}catch{}}
  window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
}
refresh().then(registerAgentTools).catch(error=>{message('error',error.message);$('run-summary').textContent='Could not load the workspace. Refresh to retry.';});
