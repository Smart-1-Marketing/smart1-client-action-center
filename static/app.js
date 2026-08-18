const state={
  tab:"client", client:[], payment:[], invoice:[], paidAccounts:[], history:[], gmailSuggestions:[], chatSuggestions:[],
  sentFollowups:[], meetingReviews:[], summary:{}, system:null, currentReply:null,currentChatReply:null, quickFilter:"", sentQuick:"all", invoiceAge:"all",
  discoveryTasks:[], watchDomains:[], sortBy:"due", counts:{}, syncPoll:null, orgExpanded:{}
};
const el=id=>document.getElementById(id);
const TODAY=new Date().toISOString().slice(0,10);

async function api(url,options={}){
  const r=await fetch(url,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  let data=null;try{data=await r.json()}catch(_){data=null}
  if(!r.ok)throw new Error(data?.error||`Request failed (${r.status})`);
  return data;
}
function esc(v=""){const d=document.createElement("div");d.textContent=String(v);return d.innerHTML}
function money(v,c="USD"){const n=Number(v||0);return n?new Intl.NumberFormat("en-US",{style:"currency",currency:c||"USD"}).format(n):"—"}
function safeDate(v){if(!v)return "";try{return new Date(v).toLocaleString([], {year:"numeric",month:"short",day:"numeric",hour:"numeric",minute:"2-digit"})}catch(_){return v}}
function receivedLabel(t){return safeDate(t.source_received_at||t.created_at||"")}
function dueBubble(t){
  if(!t.due_date)return `<span class="bubble no-due">No Deadline</span>`;
  if(t.due_date<TODAY)return `<span class="bubble overdue">OVERDUE · ${esc(t.due_date)}</span>`;
  if(t.due_date===TODAY)return `<span class="bubble due-today">DUE TODAY</span>`;
  return `<span class="bubble future">Due ${esc(t.due_date)}</span>`;
}
function participantText(t){
  const p=t.participants||[];
  if(!p.length)return "";
  return p.map(x=>x.display_name?`${x.display_name} <${x.email}>`:x.email).join(", ");
}
function notesHtml(t){
  if(!t.notes?.length)return "";
  return `<div class="log-section"><h4>Notes</h4>${t.notes.slice(0,4).map(n=>`<div class="note">${esc(n.body)}<small>${esc(n.created_at)}</small></div>`).join("")}</div>`;
}
function researchLogsHtml(t){
  if(!t.research_logs?.length)return "";
  return `<div class="log-section"><h4>Email Research Log</h4>${t.research_logs.slice(0,5).map(log=>{
    const sources=(log.sources||[]).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.subject||"Email")}</a>`).join(" · ");
    return `<div class="research-log"><strong>Q: ${esc(log.question)}</strong><div>${esc(log.answer)}</div><small>${esc((log.confidence||"").toUpperCase())} confidence · ${esc(log.created_at)}</small>${sources?`<div class="source-links">${sources}</div>`:""}</div>`;
  }).join("")}</div>`;
}
function emailUpdatesHtml(t){
  if(!t.email_updates?.length)return "";
  return `<div class="log-section"><h4>Email Chain Updates</h4>${t.email_updates.slice(0,6).map(u=>{
    const direction=(u.direction||"incoming").toUpperCase();
    const recipients=[u.to_emails?`To: ${u.to_emails}`:"",u.cc_emails?`Cc: ${u.cc_emails}`:""].filter(Boolean).join(" · ");
    return `<div class="email-update"><strong>${direction}: ${esc(u.subject||"Related email")}</strong><div>${esc(u.snippet||"")}</div><small>${esc(u.sender_name||u.sender_email)} · ${esc(u.received_at)} · ${esc(u.match_method||"")}</small>${recipients?`<small>${esc(recipients)}</small>`:""}${u.email_url?`<div class="source-links"><a href="${esc(u.email_url)}" target="_blank" rel="noopener">Open email</a></div>`:""}</div>`;
  }).join("")}</div>`;
}
function chatUpdatesHtml(t){
  if(!t.chat_updates?.length)return "";
  return `<div class="log-section"><h4>Google Chat Updates</h4>${t.chat_updates.slice(0,6).map(u=>`<div class="email-update"><strong>${esc(u.space_display_name||"Google Chat")}</strong><div>${esc(u.message_text||"")}</div><small>${esc(u.sender_display_name||"")} · ${esc(u.create_time||"")} · ${esc(u.match_method||"")}</small>${u.space_uri?`<div class="source-links"><a href="${esc(u.space_uri)}" target="_blank" rel="noopener">Open Chat</a></div>`:""}</div>`).join("")}</div>`;
}
function resolutionHtml(t){
  const pending=(t.resolution_reviews||[]).filter(r=>r.state==="pending");
  if(!pending.length)return "";
  return pending.map(r=>{
    const sources=(r.sources||[]).map(s=>s.url?`<a href="${esc(s.url)}" target="_blank" rel="noopener">View supporting message</a>`:"").filter(Boolean).join(" · ");
    return `<div class="resolution-review"><div class="resolution-title">This communication may have resolved the task.</div><div>${esc(r.summary)}</div><div class="helper">${esc((r.confidence||"").toUpperCase())} confidence</div>${sources?`<div class="source-links">${sources}</div>`:""}<div class="row"><button class="button primary" data-resolution-yes="${t.id}" data-review-id="${r.id}">Yes — Complete It</button><button class="button secondary" data-resolution-no="${t.id}" data-review-id="${r.id}">No — Keep Open</button></div></div>`;
  }).join("");
}
function gptHelpHtml(t){
  if(t.category==="paid_account"||!t.gpt_can_help)return "";
  const prepared=!!(t.gpt_help_prompt||"").trim();
  return `<div class="gpt-help"><strong>GPT can probably help complete this task.</strong><div class="helper">${esc(t.gpt_help_reason||"This request can be prepared or solved with GPT assistance.")}</div><div class="helper">${prepared?"Prompt already prepared.":"Full prompt is generated only when you click Prepare GPT Prompt."}</div><div class="row"><button class="button primary" data-show-gpt-prompt="${t.id}">${prepared?"View GPT Prompt":"Prepare GPT Prompt"}</button><button class="button secondary" data-suppress-gpt-help="${t.id}">Don't Suggest GPT Help for This Type</button></div></div>`;
}
function paymentFields(t){
  if(t.category!=="payment")return "";
  const paid=t.completed||t.paid_at;
  return `<div class="payment-box">
    <div class="payment-field"><span>Invoice Amount</span><strong>${money(t.amount,t.currency)}</strong></div>
    <div class="payment-field"><span>Invoice #</span><strong>${esc(t.invoice_number||"—")}</strong></div>
    <div class="payment-field"><span>Status</span><strong>${paid?"PAID":"UNPAID"}</strong></div>
    ${paid?`<div class="payment-field"><span>Paid</span><strong>${money(t.paid_amount||t.amount,t.currency)}</strong></div><div class="payment-field"><span>Paid Date</span><strong>${esc(safeDate(t.paid_at||t.completed_at))}</strong></div><div class="payment-field"><span>Reference</span><strong>${esc(t.payment_reference||"—")}</strong></div>`:""}
  </div>`;
}
function actionButtons(t,history=false){
  if(t.category==="paid_account"&&!history){
    return `<div class="actions">
      <button class="task-action" data-status="${t.id}">Status</button>
      <button class="task-action" data-note="${t.id}">Create Note</button>
      <button class="task-action" data-deadline="${t.id}">Add Deadline</button>
      <button class="task-action" data-email-research="${t.id}">Ask Email</button>
      ${t.email_url?`<a class="task-action" href="${esc(t.email_url)}" target="_blank" rel="noopener">See Email</a>`:""}
      <button class="task-action" data-reply="${t.id}">Prepare Reply</button>
    </div>`;
  }
  if(t.completed&&t.category==="payment"&&!history)return `<div class="actions">${t.email_url?`<a class="task-action" href="${esc(t.email_url)}" target="_blank" rel="noopener">See Invoice Email</a>`:""}<span class="paid-status">Paid ✓ ${esc(safeDate(t.paid_at||t.completed_at))}</span></div>`;
  if(history)return `<div class="actions"><button class="task-action" data-restore="${t.id}">Restore</button><button class="task-action danger" data-delete="${t.id}">Delete</button></div>`;
  const invoiceBtn=t.category==="payment"?`<button class="task-action invoice-button ${t.invoice_sent?"active":""}" data-invoice-toggle="${t.id}">${t.invoice_sent?"Invoice Sent ✓":"Mark Invoice Sent"}</button>`:"";
  return `<div class="actions">
    <button class="task-action" data-status="${t.id}">Status</button>
    <button class="task-action" data-note="${t.id}">Create Note</button>
    <button class="task-action" data-deadline="${t.id}">Add Deadline</button>
    <button class="task-action" data-email-research="${t.id}">Ask Email</button>
    <button class="task-action" data-check-resolution="${t.id}">Check Resolution</button>
    <button class="task-action" data-gpt-help="${t.id}">${t.gpt_can_help?"GPT Help":"Can GPT Help?"}</button>
    ${t.category==="payment"?`<button class="task-action" data-payment-edit="${t.id}">Payment Info</button>`:""}
    ${invoiceBtn}
    ${t.email_url?`<a class="task-action" href="${esc(t.email_url)}" target="_blank" rel="noopener">See Email</a>`:""}
    ${t.chat_space_uri?`<a class="task-action" href="${esc(t.chat_space_uri)}" target="_blank" rel="noopener">See Chat</a>`:""}
    ${t.chat_space_name?`<button class="task-action" data-chat-reply="${t.id}">Reply in Chat</button>`:""}
    ${["gmail","chat"].includes(t.source_kind)?`<button class="task-action not-task-button" data-live-not-task="${t.id}">Not a Task — Train Type</button>`:""}
    <button class="task-action" data-reply="${t.id}">Prepare Reply</button>
    <button class="task-action complete" ${t.category==="payment"?`data-mark-paid="${t.id}"`:`data-complete="${t.id}"`}>${t.category==="payment"?"Mark Paid":"Complete"}</button>
  </div>`;
}
function taskCard(t,history=false){
  const multi=Number(t.recipient_count||0)>1?`<span class="bubble multi">MULTI-PERSON · ${Number(t.recipient_count)}</span>`:"";
  const assignee=t.assignee?`<div class="assignee">Assigned to: <strong>${esc(t.assignee)}</strong></div>`:"";
  const participants=participantText(t);
  return `<article class="task" id="task-${t.id}">
    <div class="task-head"><div><h3>${esc(t.title)}</h3><div class="task-received">Received: ${esc(receivedLabel(t)||"Unknown")}</div><div class="party">${esc(t.party)}</div>${assignee}${participants?`<div class="participant-line">Participants: ${esc(participants)}</div>`:""}</div>
      <div class="bubbles"><span class="bubble ${t.category==="payment"?"payment":t.category==="paid_account"?"paid-account":"client"}">${t.category==="payment"?"PAYMENT":t.category==="paid_account"?"PAID ACCOUNT":"CLIENT TASK"}</span>${t.invoice_sent?`<span class="bubble invoice">INVOICE SENT</span>`:""}${multi}<span class="bubble ${esc(t.priority)}">${esc((t.priority||"normal").toUpperCase())}</span>${dueBubble(t)}<span class="bubble status-${esc(t.status)}">${esc(t.status)}</span></div></div>
    <div class="detail">${esc(t.detail||"")}</div>
    ${paymentFields(t)}${resolutionHtml(t)}${gptHelpHtml(t)}${notesHtml(t)}${emailUpdatesHtml(t)}${chatUpdatesHtml(t)}${researchLogsHtml(t)}
    <div id="reveal-${t.id}"></div>${actionButtons(t,history)}
  </article>`;
}
function getTask(id){return [...state.client,...state.payment,...state.invoice,...state.paidAccounts,...state.history].find(t=>t.id===Number(id))}

function normalizeTaskDomain(t){
  const raw=String(t.sender_domain||"").trim().toLowerCase();
  if(raw)return raw.replace(/^www\./,"");
  const email=String(t.sender_email||t.email_to||"").trim().toLowerCase();
  const at=email.lastIndexOf("@");
  return at>=0?email.slice(at+1).replace(/^www\./,""):"";
}

function domainDisplayName(domain){
  const root=String(domain||"").split(".")[0]||"";
  return root.replace(/[-_]+/g," ").replace(/\b\w/g,c=>c.toUpperCase())||"Organization";
}

function organizationNameForDomain(tasks,domain){
  const backendName=tasks.map(t=>String(t.organization_name||"").trim()).find(Boolean);
  if(backendName)return backendName;

  const counts={};
  for(const t of tasks){
    const name=String(t.party||"").trim();
    if(!name)continue;
    counts[name]=(counts[name]||0)+1;
  }
  const ranked=Object.entries(counts).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0]));
  if(ranked.length)return ranked[0][0];
  return domainDisplayName(domain);
}

function groupClientTasksByDomain(rows){
  const domains=new Map();
  const standalone=[];

  for(const t of rows){
    const domain=normalizeTaskDomain(t);
    if(!domain){
      standalone.push({type:"task",task:t});
      continue;
    }
    if(!domains.has(domain))domains.set(domain,[]);
    domains.get(domain).push(t);
  }

  const groups=[];
  for(const [domain,tasks] of domains.entries()){
    groups.push({
      type:"group",
      domain,
      organization:organizationNameForDomain(tasks,domain),
      tasks
    });
  }

  // Every sender domain is an Organization, even if it currently has only one task.
  // Multiple tasks from the same domain always stay together.
  groups.sort((a,b)=>a.organization.localeCompare(b.organization)||a.domain.localeCompare(b.domain));
  return [...groups,...standalone];
}

function organizationGroupHtml(group){
  const expanded=state.orgExpanded[group.domain]!==false;
  const urgent=group.tasks.filter(t=>t.priority==="urgent").length;
  const waiting=group.tasks.filter(t=>t.status==="Waiting").length;
  const due=group.tasks.filter(t=>t.due_date&&t.due_date<=TODAY&&!t.completed).length;

  return `<section class="organization-group">
    <button class="organization-head" data-org-toggle="${esc(group.domain)}">
      <div class="organization-title-wrap">
        <span class="organization-chevron">${expanded?"▾":"▸"}</span>
        <div>
          <div class="organization-kicker">ORGANIZATION</div>
          <div class="organization-title">${esc(group.organization)}</div>
          <div class="organization-domain">${esc(group.domain)}</div>
        </div>
      </div>
      <div class="organization-badges">
        <span class="org-count">${group.tasks.length} task${group.tasks.length===1?"":"s"}</span>
        ${urgent?`<span class="org-mini urgent">${urgent} urgent</span>`:""}
        ${due?`<span class="org-mini due">${due} due</span>`:""}
        ${waiting?`<span class="org-mini waiting">${waiting} waiting</span>`:""}
      </div>
    </button>
    <div class="organization-tasks ${expanded?"":"hidden"}">
      ${group.tasks.map(t=>taskCard(t,false)).join("")}
    </div>
  </section>`;
}

function groupedClientHtml(rows){
  const units=groupClientTasksByDomain(rows);
  return units.map(x=>x.type==="group"?organizationGroupHtml(x):taskCard(x.task,false)).join("");
}

function sortTasks(rows){
  const mode=state.sortBy||"due";
  const received=t=>String(t.source_received_at||t.created_at||"");
  const company=t=>String(t.party||"").trim().toLowerCase();
  const priorityRank={urgent:0,high:1,normal:2};

  return [...rows].sort((a,b)=>{
    if(mode==="received-newest")return received(b).localeCompare(received(a));
    if(mode==="received-oldest")return received(a).localeCompare(received(b));

    if(mode==="company-az"){
      const d=company(a).localeCompare(company(b));
      return d!==0?d:received(b).localeCompare(received(a));
    }

    if(mode==="company-za"){
      const d=company(b).localeCompare(company(a));
      return d!==0?d:received(b).localeCompare(received(a));
    }

    if(mode==="priority"){
      const d=(priorityRank[a.priority]??9)-(priorityRank[b.priority]??9);
      if(d!==0)return d;
      return received(b).localeCompare(received(a));
    }

    if(a.due_date&&b.due_date)return a.due_date.localeCompare(b.due_date);
    if(a.due_date)return -1;
    if(b.due_date)return 1;
    return received(b).localeCompare(received(a));
  });
}
function applyQuickFilter(rows){
  const qf=state.quickFilter;if(!qf||qf.endsWith("-all"))return rows;
  if(qf==="client-urgent")return rows.filter(t=>t.priority==="urgent");
  if(qf==="client-due")return rows.filter(t=>t.due_date&&t.due_date<=TODAY);
  if(qf==="client-working")return rows.filter(t=>t.status==="Working");
  if(qf==="client-waiting")return rows.filter(t=>t.status==="Waiting");
  if(qf==="payment-known")return rows.filter(t=>Number(t.amount||0)>0);
  if(qf==="payment-overdue")return rows.filter(t=>t.due_date&&t.due_date<TODAY);
  if(qf==="payment-overdue-known")return rows.filter(t=>t.due_date&&t.due_date<TODAY&&Number(t.amount||0)>0);
  if(qf==="invoice-unpaid")return rows.filter(t=>!t.completed);if(qf==="invoice-paid")return rows.filter(t=>!!t.completed);if(qf==="invoice-known")return rows.filter(t=>Number(t.amount||0)>0);
  if(qf==="invoice-overdue")return rows.filter(t=>t.due_date&&t.due_date<TODAY);
  if(qf==="invoice-missing")return rows.filter(t=>!Number(t.amount||0));
  return rows;
}
function filtered(rows){
  const q=el("search").value.toLowerCase().trim(),st=el("statusFilter").value;
  // The default Client Tasks feed is the true active inbox. Working and Waiting
  // are moved into their scorecard feeds instead of staying in the main list.
  if(state.tab==="client"&&(!state.quickFilter||state.quickFilter==="client-all")&&st==="all"){
    rows=rows.filter(t=>!["Working","Waiting"].includes(t.status));
  }else{
    rows=applyQuickFilter(rows);
  }
  return sortTasks(rows.filter(t=>{
    const hay=`${t.party} ${t.title} ${t.detail} ${t.assignee||""} ${participantText(t)} ${(t.notes||[]).map(n=>n.body).join(" ")} ${(t.research_logs||[]).map(r=>r.question+" "+r.answer).join(" ")}`.toLowerCase();
    return(!q||hay.includes(q))&&(st==="all"||t.status===st);
  }));
}
function renderMetrics(){
  const c=state.counts||{};el("tabClientCount").textContent=c.client??state.client.length;el("tabPaymentCount").textContent=c.payment??state.payment.length;el("tabPaidAccountsCount").textContent=c.paid_accounts??state.paidAccounts.filter(x=>x.paid_account_state==="current").length;el("tabInvoiceCount").textContent=c.invoice??state.invoice.length;el("tabSentCount").textContent=c.sent??state.sentFollowups.length;el("tabGmailCount").textContent=c.gmail??state.gmailSuggestions.length;el("tabChatCount").textContent=c.chat??state.chatSuggestions.length;el("tabMeetingCount").textContent=c.meetings??state.meetingReviews.length;el("tabCompletedCount").textContent=c.completed??state.history.length;
  el("clientCount").textContent=state.client.filter(t=>!["Working","Waiting"].includes(t.status)).length;el("clientUrgent").textContent=state.client.filter(t=>t.priority==="urgent").length;el("clientDue").textContent=state.client.filter(t=>t.due_date&&t.due_date<=TODAY).length;el("clientWorking").textContent=state.client.filter(t=>t.status==="Working").length;el("clientWaiting").textContent=state.client.filter(t=>t.status==="Waiting").length;
  const s=state.summary||{};el("payCount").textContent=s.count_all||0;el("payTotal").textContent=money(s.total_known||0);el("payOverdue").textContent=s.overdue_count||0;el("payOverdueTotal").textContent=money(s.overdue_total||0);

  el("sentCount").textContent=state.sentFollowups.length;el("sentDue").textContent=state.sentFollowups.filter(x=>x.followup_due&&x.followup_due<=TODAY).length;el("sentLinked").textContent=state.sentFollowups.filter(x=>Number(x.task_id||0)>0).length;el("sentMulti").textContent=state.sentFollowups.filter(x=>Number(x.recipient_count||0)>1||(x.recipients||"").split(",").filter(Boolean).length>1).length;
  document.querySelectorAll(".metric[data-quick]").forEach(m=>m.classList.toggle("active-filter",m.dataset.quick===state.quickFilter));
  document.querySelectorAll(".metric[data-sent-quick]").forEach(m=>m.classList.toggle("active-filter",m.dataset.sentQuick===state.sentQuick));
  el("clearQuickFilter").classList.toggle("hidden",!state.quickFilter||state.quickFilter.endsWith("-all"));
}
function renderTasks(){
  renderMetrics();let rows=[];if(state.tab==="client")rows=state.client;if(state.tab==="payment")rows=state.payment;if(state.tab==="invoice")rows=state.invoice;if(state.tab==="completed")rows=state.history;
  const history=state.tab==="completed";
  const visible=filtered(rows);
  if(state.tab==="client"){
    el("taskList").innerHTML=groupedClientHtml(visible)||`<div class="panel">No matching items.</div>`;
  }else{
    el("taskList").innerHTML=visible.map(t=>taskCard(t,history)).join("")||`<div class="panel">No matching items.</div>`;
  }
}

function dateOnly(v){
  if(!v)return "—";
  try{return new Date(v).toLocaleDateString()}catch(_){return v}
}
function daysOverdue(due){
  if(!due)return 0;
  const d=new Date(`${due}T00:00:00`);
  const t=new Date(`${TODAY}T00:00:00`);
  return Math.max(0,Math.floor((t-d)/86400000));
}
function invoiceAgeBucket(t){
  const n=daysOverdue(t.due_date);
  if(n>=90)return "90";
  if(n>=60)return "60";
  if(n>=30)return "30";
  return "current";
}
function iconButton(symbol,title,attr,id,kind=""){
  return `<button class="table-icon ${kind}" title="${esc(title)}" aria-label="${esc(title)}" ${attr}="${id}">${symbol}</button>`;
}
function invoiceActionIcons(t){
  return `<span class="table-actions">
    ${iconButton("✓","Mark Paid","data-invoice-paid",t.id,"positive")}
    ${iconButton("✎","See / Edit","data-record-edit",t.id)}
    ${iconButton("×","Delete","data-invoice-delete",t.id,"danger")}
  </span>`;
}
function paidAccountActionIcons(t){
  return `<span class="table-actions">
    ${iconButton("✎","See / Edit","data-record-edit",t.id)}
    ${iconButton("✓","Reconciled","data-account-reconciled",t.id,"positive")}
    ${iconButton("×","Delete","data-account-delete",t.id,"danger")}
  </span>`;
}
function renderPaidAccounts(){
  const q=(el("paidAccountsSearch")?.value||"").toLowerCase().trim();
  const current=state.paidAccounts.filter(t=>(t.paid_account_state||"current")==="current"&&!t.completed)
    .filter(t=>!q||`${t.party} ${t.invoice_number}`.toLowerCase().includes(q));
  const history=state.paidAccounts.filter(t=>["reconciled","deleted"].includes(t.paid_account_state||""));

  el("paidAccountsCurrentBody").innerHTML=current.length?current.map(t=>`<tr>
    <td>${esc(dateOnly(t.source_received_at))}</td>
    <td><strong>${esc(t.party||"—")}</strong></td>
    <td><div class="invoice-action-cell"><strong>${esc(t.invoice_number||"—")}</strong>${paidAccountActionIcons(t)}</div></td>
    <td class="money-col">${money(t.amount,t.currency)}</td>
  </tr>`).join(""):`<tr><td colspan="4" class="empty-cell">No payments waiting to reconcile.</td></tr>`;

  el("paidAccountsHistoryBody").innerHTML=history.length?history.sort((a,b)=>String(b.paid_account_action_at||"").localeCompare(String(a.paid_account_action_at||""))).map(t=>`<tr>
    <td>${esc(dateOnly(t.paid_account_action_at||t.completed_at))}</td>
    <td><span class="history-state ${esc(t.paid_account_state)}">${esc((t.paid_account_state||"").toUpperCase())}</span></td>
    <td>${esc(dateOnly(t.source_received_at))}</td>
    <td>${esc(t.party||"—")}</td>
    <td>${esc(t.invoice_number||"—")}</td>
    <td class="money-col">${money(t.amount,t.currency)}</td>
  </tr>`).join(""):`<tr><td colspan="6" class="empty-cell">No reconciliation history yet.</td></tr>`;
}
function renderInvoices(){
  const q=(el("invoiceSearch")?.value||"").toLowerCase().trim();
  let current=state.invoice.filter(t=>(t.invoice_state||"current")==="current"&&!t.completed)
    .filter(t=>!q||`${t.party} ${t.invoice_number}`.toLowerCase().includes(q));
  if(state.invoiceAge!=="all")current=current.filter(t=>invoiceAgeBucket(t)===state.invoiceAge);

  const sums={current:0,"30":0,"60":0,"90":0};
  for(const t of state.invoice.filter(t=>(t.invoice_state||"current")==="current"&&!t.completed)){
    sums[invoiceAgeBucket(t)]+=Number(t.amount||0);
  }
  el("invoiceAgeCurrent").textContent=money(sums.current);
  el("invoiceAge30").textContent=money(sums["30"]);
  el("invoiceAge60").textContent=money(sums["60"]);
  el("invoiceAge90").textContent=money(sums["90"]);
  document.querySelectorAll("[data-invoice-age]").forEach(x=>x.classList.toggle("active-filter",x.dataset.invoiceAge===state.invoiceAge));
  el("clearInvoiceAge").classList.toggle("hidden",state.invoiceAge==="all");

  el("invoiceCurrentBody").innerHTML=current.length?current.map(t=>`<tr>
    <td>${esc(dateOnly(t.source_received_at))}</td>
    <td><strong>${esc(t.party||"—")}</strong></td>
    <td><div class="invoice-action-cell"><strong>${esc(t.invoice_number||"—")}</strong>${invoiceActionIcons(t)}</div></td>
    <td>${esc(t.due_date||"—")}</td>
    <td class="money-col"><strong>${money(t.amount,t.currency)}</strong>${Number(t.amount_paid||0)>0?`<small>${money(t.amount_paid,t.currency)} paid</small>`:""}</td>
  </tr>`).join(""):`<tr><td colspan="5" class="empty-cell">No invoices in this view.</td></tr>`;

  const history=state.invoice.filter(t=>["paid","deleted"].includes(t.invoice_state||""));
  el("invoiceHistoryBody").innerHTML=history.length?history.sort((a,b)=>String(b.invoice_action_at||b.completed_at||"").localeCompare(String(a.invoice_action_at||a.completed_at||""))).map(t=>`<tr>
    <td>${esc(dateOnly(t.invoice_action_at||t.completed_at))}</td>
    <td><span class="history-state ${esc(t.invoice_state)}">${esc((t.invoice_state||"").toUpperCase())}</span></td>
    <td>${esc(t.party||"—")}</td>
    <td>${esc(t.invoice_number||"—")}</td>
    <td>${esc(t.due_date||"—")}</td>
    <td class="money-col">${money(t.original_amount||t.amount,t.currency)}</td>
    <td class="money-col">${money(t.amount_paid||t.paid_amount,t.currency)}</td>
  </tr>`).join(""):`<tr><td colspan="7" class="empty-cell">No paid or deleted invoice history yet.</td></tr>`;
}
async function openRecordDetail(id){
  const t=await api(`/api/tasks/${id}`);
  el("recordDetailTitle").textContent=t.category==="paid_account"?"Paid Account Details":"Invoice Details";
  const paymentEvents=(t.invoice_payment_events||[]).map(x=>`<div class="note"><strong>${esc((x.event_type||"").toUpperCase())} · ${money(x.amount,t.currency)}</strong><div>${esc(x.note||"")}</div><small>${esc(safeDate(x.created_at))}${x.reference?` · ${esc(x.reference)}`:""}</small></div>`).join("");
  const source=t.email_url?`<a class="button secondary" href="${esc(t.email_url)}" target="_blank" rel="noopener">See Source Email</a>`:"";
  const partial=t.category==="payment"&&(t.invoice_state||"current")==="current"&&!t.completed?`<div class="detail-section"><h3>Partial Payment</h3><div class="formgrid"><label>Amount<input id="recordPartialAmount" type="number" min="0.01" step="0.01" max="${Number(t.amount||0)}"></label><label>Reference<input id="recordPartialReference"></label><label class="span2">Note<textarea id="recordPartialNote" rows="2"></textarea></label></div><button class="button primary" data-record-partial="${t.id}">Record Partial Payment</button></div>`:"";
  el("recordDetailBody").innerHTML=`<div class="record-detail-summary">
    <div><span>Organization / Vendor</span><strong>${esc(t.party||"—")}</strong></div>
    <div><span>Received</span><strong>${esc(safeDate(t.source_received_at||t.created_at))}</strong></div>
    <div><span>Invoice #</span><strong>${esc(t.invoice_number||"—")}</strong></div>
    <div><span>Remaining Amount</span><strong>${money(t.amount,t.currency)}</strong></div>
    ${t.category==="payment"?`<div><span>Original Amount</span><strong>${money(t.original_amount||t.amount,t.currency)}</strong></div><div><span>Paid So Far</span><strong>${money(t.amount_paid||0,t.currency)}</strong></div><div><span>Due Date</span><strong>${esc(t.due_date||"—")}</strong></div>`:""}
  </div>
  <div class="detail-section"><h3>Details</h3><p>${esc(t.detail||"")}</p>${source}</div>
  ${partial}
  ${paymentEvents?`<div class="detail-section"><h3>Payment History</h3>${paymentEvents}</div>`:""}
  ${notesHtml(t)}${emailUpdatesHtml(t)}${chatUpdatesHtml(t)}${researchLogsHtml(t)}
  <div class="detail-section"><h3>Actions</h3>
    <div class="row">
      <button class="button secondary" data-record-reply="${t.id}">Prepare Reply</button>
      <button class="button secondary" data-record-resolution="${t.id}">Check Resolution</button>
    </div>
    <label>Create Note<textarea id="recordNoteText" rows="2" placeholder="Add an update or reconciliation note..."></textarea></label>
    <button class="button secondary" data-record-note="${t.id}">Save Note</button>
    <label>Ask Email<textarea id="recordResearchText" rows="2" placeholder="Ask a question about the source email or chain..."></textarea></label>
    <button class="button secondary" data-record-research="${t.id}">Search Email</button>
    <div id="recordActionStatus" class="helper"></div>
  </div>
  <div class="detail-section"><h3>Edit</h3><div class="formgrid">
    <label>Organization / Vendor<input id="recordParty" value="${esc(t.party||"")}"></label>
    <label>Invoice #<input id="recordInvoice" value="${esc(t.invoice_number||"")}"></label>
    <label>Remaining Amount<input id="recordAmount" type="number" min="0" step="0.01" value="${Number(t.amount||0)}"></label>
    <label>Due Date<input id="recordDue" type="date" value="${esc(t.due_date||"")}"></label>
    <label class="span2">Details<textarea id="recordDetailText" rows="4">${esc(t.detail||"")}</textarea></label>
  </div><div class="row"><button class="button primary" data-record-save="${t.id}">Save Changes</button>${source}</div></div>`;
  el("recordDetailDialog").showModal();
}

function gmailSuggestionCard(s){
  const payment=s.suggested_category==="payment",multi=Number(s.recipient_count||0)>1;
  return `<article class="suggestion"><div class="task-head"><div><h3>${esc(s.suggested_title||s.subject||"Gmail action")}</h3><div class="party">${esc(s.sender_name||s.sender_email)}${s.sender_email?` · ${esc(s.sender_email)}`:""}</div></div><div class="bubbles"><span class="bubble ${payment?"payment":"client"}">${payment?"PAYMENT":"CLIENT TASK"}</span>${multi?`<span class="bubble multi">MULTI-PERSON · ${s.recipient_count}</span>`:""}${s.invoice_sent?`<span class="bubble invoice">INVOICE SENT</span>`:""}${s.gpt_can_help?`<span class="bubble gpt">GPT CAN HELP</span>`:""}<span class="bubble ${esc(s.suggested_priority)}">${esc((s.suggested_priority||"normal").toUpperCase())}</span>${s.suggested_due_date?`<span class="bubble future">Due ${esc(s.suggested_due_date)}</span>`:`<span class="bubble no-due">No deadline</span>`}</div></div><div class="ai-summary">${esc(s.suggested_summary||s.snippet||"")}</div>${payment?`<div class="payment-box"><div class="payment-field"><span>Amount</span><strong>${money(s.payment_amount,s.currency)}</strong></div><div class="payment-field"><span>Invoice #</span><strong>${esc(s.invoice_number||"—")}</strong></div><div class="payment-field"><span>Invoice Sent</span><strong>${s.invoice_sent?"Yes":"No"}</strong></div></div>`:""}${s.gpt_can_help?`<div class="helper">GPT help: ${esc(s.gpt_help_reason||"")}</div>`:""}<div class="suggestion-meta"><span class="confidence">${esc((s.confidence||"").toUpperCase())} confidence</span> · ${esc(s.reason||"")} · Analyzer: ${esc(s.analyzer||"")}</div><div class="row"><button class="button primary" data-approve="${s.id}">Approve</button><a class="button secondary" href="${esc(s.email_url)}" target="_blank" rel="noopener">See Email</a>${s.gpt_can_help?`<button class="button secondary" data-suppress-gpt-suggestion="${s.id}">Don't Suggest GPT Help for This Type</button>`:""}<button class="button not-task-button" data-not-task="${s.id}">Not a Task — Train</button><button class="button secondary" data-dismiss="${s.id}">Dismiss</button></div></article>`;
}
function renderGmailSuggestions(){el("suggestionList").innerHTML=state.gmailSuggestions.length?state.gmailSuggestions.map(gmailSuggestionCard).join(""):`<div class="panel">No new Gmail items waiting for review.</div>`}
function chatSuggestionCard(s){
  const payment=s.suggested_category==="payment";
  return `<article class="suggestion ${s.snoozed?"snoozed":""}">
    <div class="task-head"><div><h3>${esc(s.suggested_title||"Google Chat task")}</h3><div class="party">${esc(s.space_display_name||"Google Chat")} · ${esc(s.sender_display_name||"")} · ${esc(safeDate(s.create_time))}</div></div>
      <div class="bubbles"><span class="bubble ${payment?"payment":"client"}">${payment?"PAYMENT":"CHAT TASK"}</span>${s.snoozed?`<span class="bubble waiting">SNOOZED</span>`:""}<span class="bubble ${esc(s.suggested_priority)}">${esc((s.suggested_priority||"normal").toUpperCase())}</span></div></div>
    <div class="ai-summary">${esc(s.suggested_summary||s.message_text||"")}</div>
    <div class="row">
      <button class="button primary" data-chat-approve="${s.id}">Make Task</button>
      <button class="button secondary" data-chat-invoice="${s.id}">Send to Invoices</button>
      <button class="button secondary" data-chat-snooze="${s.id}">Snooze</button>
      <button class="button secondary" data-chat-dismiss-chain="${s.id}">Dismiss Rest of Chat Chain</button>
      <button class="button secondary" data-chat-dismiss="${s.id}">Dismiss</button>
      ${s.space_uri?`<a class="button secondary" href="${esc(s.space_uri)}" target="_blank" rel="noopener">Open Chat</a>`:""}
    </div>
  </article>`;
}
function chatGroupLabel(s){
  const space=String(s.space_display_name||"").trim();
  const low=space.toLowerCase();
  const generic=!space||["google chat","general","direct message","dm","chat"].includes(low)||low.includes("team");
  const title=String(s.suggested_title||"").trim();
  return generic?(title||String(s.sender_display_name||"Google Chat")):space;
}
function renderChatSuggestions(){
  const rows=[...state.chatSuggestions].sort((a,b)=>{
    const snooze=(Number(a.snoozed||0)-Number(b.snoozed||0));
    return snooze!==0?snooze:String(b.create_time||"").localeCompare(String(a.create_time||""));
  });
  const groups=new Map();
  for(const s of rows){
    const label=chatGroupLabel(s);
    if(!groups.has(label))groups.set(label,[]);
    groups.get(label).push(s);
  }
  const ordered=[...groups.entries()].sort((a,b)=>String(b[1][0]?.create_time||"").localeCompare(String(a[1][0]?.create_time||"")));
  el("chatSuggestionList").innerHTML=ordered.length?ordered.map(([label,items])=>`<section class="chat-topic-group"><div class="chat-topic-head"><strong>${esc(label)}</strong><span>${items.length} item${items.length===1?"":"s"} · newest first</span></div>${items.map(chatSuggestionCard).join("")}</section>`).join(""):`<div class="panel">No new Google Chat items waiting for review.</div>`;
}
function renderSent(){
  renderMetrics();let rows=[...state.sentFollowups].sort((a,b)=>String(b.sent_at||"").localeCompare(String(a.sent_at||"")));
  if(state.sentQuick==="due")rows=rows.filter(x=>x.followup_due&&x.followup_due<=TODAY);
  if(state.sentQuick==="linked")rows=rows.filter(x=>Number(x.task_id||0)>0);
  if(state.sentQuick==="multi")rows=rows.filter(x=>sentRecipientCount(x)>1);
  el("sentList").innerHTML=rows.length?rows.map(sentCard).join(""):`<div class="panel">No sent follow-ups in this view.</div>`;
}
function meetingCard(m){
  const tasks=m.tasks||[],multi=tasks.length>1;
  return `<article class="suggestion meeting-review"><div class="task-head"><div><h3>${esc(m.meeting_title||"Gemini meeting recap")}</h3><div class="party">${safeDate(m.received_at)}</div></div><div class="bubbles"><span class="bubble meeting">GEMINI RECAP</span>${multi?`<span class="bubble urgent">ASSIGNMENT REVIEW REQUIRED</span>`:`<span class="bubble high">1 ACTION ITEM</span>`}</div></div><div class="ai-summary">${esc(m.summary||"")}</div>${multi?`<div class="assignment-warning">I found ${tasks.length} separate tasks. Choose the ones you want added and confirm who should own each one before anything is assigned.</div>`:""}<div class="meeting-tasks">${tasks.map((t,i)=>`<div class="meeting-task"><label class="meeting-select"><input type="checkbox" data-meeting-task="${m.id}" data-task-index="${i}" checked><div><strong>${esc(t.title||"Meeting action")}</strong><div>${esc(t.summary||"")}</div><div class="bubbles compact"><span class="bubble ${esc(t.priority||"normal")}">${esc((t.priority||"normal").toUpperCase())}</span>${t.due_date?`<span class="bubble future">Due ${esc(t.due_date)}</span>`:""}${t.gpt_can_help?`<span class="bubble gpt">GPT CAN HELP</span>`:""}</div></div></label><label class="assignee-input">Assign to<input data-meeting-assignee="${m.id}" data-task-index="${i}" value="${esc(t.assignee||"")}" placeholder="Todd, Dave, Louann…"></label></div>`).join("")}</div><div class="row"><button class="button primary" data-meeting-add="${m.id}">Add Selected Tasks</button>${m.email_url?`<a class="button secondary" href="${esc(m.email_url)}" target="_blank" rel="noopener">See Gemini Email</a>`:""}<button class="button secondary" data-meeting-dismiss="${m.id}">Dismiss Recap</button></div><div id="meeting-status-${m.id}" class="helper"></div></article>`;
}
function renderMeetings(){el("meetingList").innerHTML=state.meetingReviews.length?state.meetingReviews.map(meetingCard).join(""):`<div class="panel">No Gemini meeting recaps waiting for review.</div>`}
function updateViews(){
  ["client","payment","paidaccounts","invoice","sent","gmail","chat","meetings","completed"].forEach(name=>el(`${name}View`).classList.toggle("hidden",state.tab!==name));
  const taskTab=["client","payment","completed"].includes(state.tab);el("taskControls").classList.toggle("hidden",!taskTab);el("taskList").classList.toggle("hidden",!taskTab);
  document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("active",b.dataset.tab===state.tab));
  if(taskTab)renderTasks();if(state.tab==="invoice")renderInvoices();if(state.tab==="paidaccounts")renderPaidAccounts();if(state.tab==="sent")renderSent();if(state.tab==="gmail")renderGmailSuggestions();if(state.tab==="chat")renderChatSuggestions();if(state.tab==="meetings")renderMeetings();
}
async function loadSystem(){
  state.system=await api("/api/system/status");const s=state.system;

  el("gmailState").textContent=s.gmail_connected?"Connected":s.google_configured?"Not connected":"Needs setup";
  el("gmailState").className=`pill ${s.gmail_connected?"ok":"neutral"}`;
  const mailBits=[];
  if(s.gmail_last_sync)mailBits.push(`Inbox ${safeDate(s.gmail_last_sync)}`);
  if(s.sent_last_sync)mailBits.push(`Sent ${safeDate(s.sent_last_sync)}`);
  if(s.meeting_last_sync)mailBits.push(`Meetings ${safeDate(s.meeting_last_sync)}`);
  if(s.gmail_last_error)mailBits.push(`Error: ${s.gmail_last_error}`);
  el("gmailDetail").textContent=mailBits.join(" · ");

  el("chatState").textContent=s.chat_connected?"Read Connected":s.google_scope_upgrade_needed?"Reconnect Google":"Not connected";
  el("chatState").className=`pill ${s.chat_connected?"ok":s.google_scope_upgrade_needed?"bad":"neutral"}`;
  const chatBits=[];
  if(s.chat_connected)chatBits.push(s.chat_last_sync?`Last sync ${safeDate(s.chat_last_sync)}`:"Ready to sync");
  if(s.chat_connected)chatBits.push(s.chat_send_enabled?"Replies enabled":"Read only");
  if(s.chat_last_error)chatBits.push(`Error: ${s.chat_last_error}`);
  el("chatDetail").textContent=chatBits.join(" · ");

  el("aiSimpleState").textContent=s.openai_configured?"Configured":"Not configured";
  el("aiSimpleState").className=`pill ${s.openai_configured?"ok":"neutral"}`;
  el("aiSimpleDetail").textContent=s.openai_last_error?`Last error: ${s.openai_last_error}`:"";
  el("buildVersion").textContent=s.app_build_version||"Unknown";

  const showConnect=!s.gmail_connected||s.google_scope_upgrade_needed;
  el("connectGoogle").classList.toggle("hidden",!showConnect||!s.google_configured);
  el("connectGoogle").textContent=s.google_scope_upgrade_needed?"Reconnect Google":"Connect Google";
  el("syncAll").classList.toggle("hidden",!s.gmail_connected);

  if(!state.syncPoll){
    const latest=s.gmail_last_sync||s.chat_last_sync||s.sent_last_sync||"";
    el("syncStatus").textContent=latest?`Last sync ${safeDate(latest)}`:(s.gmail_connected?"Ready":"Connect Google");
  }
}

function diagnosticSpaceRow(s){
  return `<tr><td>${esc(s.display_name||s.name||"Unnamed")}</td><td>${esc(s.space_type||"")}</td><td>${Number(s.recent_messages_visible||0)}</td><td>${esc(s.error||"")}</td></tr>`;
}
function renderChatDiagnostics(d){
  const errors=d.errors||[];
  const types=Object.entries(d.space_types||{}).map(([k,v])=>`${esc(k)}: ${v}`).join(" · ")||"None";
  el("chatDiagnosticsBody").innerHTML=`
    <div class="diagnostic-grid">
      <div class="diagnostic-card"><span>Chat Read Permission</span><strong>${d.read_scope_ok?"YES":"NO"}</strong></div>
      <div class="diagnostic-card"><span>Chat Send Permission</span><strong>${d.send_scope_ok?"YES":"NO"}</strong></div>
      <div class="diagnostic-card"><span>Spaces Google Returned</span><strong>${Number(d.space_count||0)}</strong></div>
      <div class="diagnostic-card"><span>Recent Sample Messages</span><strong>${Number(d.recent_message_samples||0)}</strong></div>
    </div>
    <div class="callout"><strong>Space types:</strong> ${types}<br><strong>Lookback:</strong> ${Number(d.lookback_days||30)} days. No OpenAI call is made by this diagnostic.${(d.ignored_spaces||[]).length?`<br><strong>Ignored spaces:</strong> ${esc((d.ignored_spaces||[]).join(", "))}`:""}</div>
    ${errors.length?`<div class="diagnostic-errors"><strong>Errors</strong>${errors.map(x=>`<div>${esc(x)}</div>`).join("")}</div>`:""}
    <div class="table-wrap"><table class="diagnostic-table"><thead><tr><th>Space</th><th>Type</th><th>Recent sample</th><th>Error</th></tr></thead><tbody>${(d.sampled_spaces||[]).map(diagnosticSpaceRow).join("")}</tbody></table></div>`;
}
async function runChatDiagnostics(){
  el("runChatDiagnostics").disabled=true;
  el("runChatDiagnostics").textContent="Checking…";
  el("chatDiagnosticsBody").innerHTML=`<div class="helper">Testing scopes, spaces, and recent messages directly with Google Chat…</div>`;
  try{
    const d=await api("/api/chat/diagnostics");
    renderChatDiagnostics(d);
  }catch(err){
    el("chatDiagnosticsBody").innerHTML=`<div class="diagnostic-errors"><strong>Chat check failed</strong><div>${esc(err.message)}</div></div>`;
  }finally{
    el("runChatDiagnostics").disabled=false;
    el("runChatDiagnostics").textContent="Run Chat Check";
  }
}

function clearInactiveTabData(active){
  if(active!=="client")state.client=[];
  if(active!=="payment")state.payment=[];
  if(active!=="invoice")state.invoice=[];
  if(active!=="paidaccounts")state.paidAccounts=[];
  if(active!=="completed")state.history=[];
  if(active!=="gmail")state.gmailSuggestions=[];
  if(active!=="chat")state.chatSuggestions=[];
  if(active!=="sent")state.sentFollowups=[];
  if(active!=="meetings")state.meetingReviews=[];
}
async function loadCounts(){state.counts=await api("/api/dashboard/counts")}
async function loadTabData(tab){
  clearInactiveTabData(tab);
  if(tab==="client")state.client=await api("/api/tasks?category=client");
  if(tab==="payment"){state.summary=await api("/api/payment-summary");state.payment=await api("/api/tasks?category=payment")}
  if(tab==="invoice")state.invoice=await api("/api/invoices");
  if(tab==="paidaccounts")state.paidAccounts=await api("/api/paid-accounts");
  if(tab==="completed")state.history=await api("/api/tasks?completed=1");
  if(tab==="gmail")state.gmailSuggestions=await api("/api/gmail/suggestions");
  if(tab==="chat")state.chatSuggestions=await api("/api/chat/suggestions");
  if(tab==="sent")state.sentFollowups=await api("/api/sent-followups");
  if(tab==="meetings")state.meetingReviews=await api("/api/meetings/reviews");
}
async function load(){
  await loadSystem();
  await loadCounts();
  await loadTabData(state.tab);
  renderMetrics();renderGmailSuggestions();renderChatSuggestions();renderMeetings();renderSent();updateViews();
}
function showPanel(id,html){const box=el(`reveal-${id}`);box.innerHTML=html;box.className="reveal"}

// Theme: manual preference wins; otherwise dark at night (7pm–6am).
function preferredTheme(){const saved=localStorage.getItem("s1-theme");if(saved==="dark"||saved==="light")return saved;const h=new Date().getHours();return(h>=19||h<6)?"dark":"light"}
function applyTheme(theme){document.body.dataset.theme=theme;localStorage.setItem("s1-theme",theme);el("themeToggle").textContent=theme==="dark"?"Light Mode":"Dark Mode"}
applyTheme(preferredTheme());el("themeToggle").addEventListener("click",()=>applyTheme(document.body.dataset.theme==="dark"?"light":"dark"));

// Tabs and scorecards.
document.querySelector(".tabs").addEventListener("click",async e=>{const b=e.target.closest("[data-tab]");if(!b)return;state.tab=b.dataset.tab;state.quickFilter="";await loadTabData(state.tab);await loadCounts();renderMetrics();updateViews()});
document.addEventListener("click",e=>{const m=e.target.closest(".metric[data-quick]");if(!m)return;state.quickFilter=m.dataset.quick;renderTasks();const first=el("taskList").querySelector(".task");if(first)first.scrollIntoView({behavior:"smooth",block:"start"})});
document.addEventListener("click",e=>{const m=e.target.closest(".metric[data-sent-quick]");if(!m)return;state.sentQuick=m.dataset.sentQuick;renderSent()});
el("clearQuickFilter").addEventListener("click",()=>{state.quickFilter="";renderTasks()});

// Task actions.
el("taskList").addEventListener("click",e=>{
  const toggle=e.target.closest("[data-org-toggle]");
  if(!toggle)return;
  const domain=toggle.dataset.orgToggle;
  state.orgExpanded[domain]=state.orgExpanded[domain]===false?true:false;
  renderTasks();
});

el("taskList").addEventListener("click",async e=>{
  const b=e.target.closest("[data-status],[data-note],[data-deadline],[data-email-research],[data-check-resolution],[data-gpt-help],[data-show-gpt-prompt],[data-suppress-gpt-help],[data-payment-edit],[data-invoice-toggle],[data-reply],[data-chat-reply],[data-live-not-task],[data-mark-paid],[data-complete],[data-restore],[data-delete],[data-resolution-yes],[data-resolution-no]");if(!b)return;
  const key=Object.keys(b.dataset)[0],id=Number(b.dataset[key]),t=getTask(id);if(!t)return;
  if(key==="status")showPanel(id,`<label>Status<select id="statusInput-${id}">${["Open","Working","Waiting","Blocked"].map(s=>`<option ${s===t.status?"selected":""}>${s}</option>`).join("")}</select></label><div class="row"><button class="button primary" data-save-status="${id}">Save Status</button></div>`);
  if(key==="note")showPanel(id,`<label>Create Note<textarea id="noteInput-${id}" rows="3" placeholder="Add update, owner, promise, payment confirmation or next step..."></textarea></label><div class="row"><button class="button primary" data-save-note="${id}">Save Note</button></div>`);
  if(key==="deadline")showPanel(id,`<label>Deadline<input id="deadlineInput-${id}" type="date" value="${esc(t.due_date||"")}"></label><div class="row"><button class="button primary" data-save-deadline="${id}">Save Deadline</button></div>`);
  if(key==="emailResearch")showPanel(id,`<label>Ask Email About This Task<textarea id="researchInput-${id}" rows="2" placeholder="What did they say the deadline was? What did I promise? Did they approve this?"></textarea></label><div class="helper">This searches your entire Gmail history for answers, not only the 30-day automatic task window.</div><div class="row"><button class="button primary" data-run-research="${id}">Search My Email</button></div><div id="researchStatus-${id}" class="helper"></div>`);
  if(key==="paymentEdit")showPanel(id,`<div class="formgrid"><label>Amount<input id="amountInput-${id}" type="number" min="0" step="0.01" value="${Number(t.amount||0)}"></label><label>Invoice #<input id="invoiceInput-${id}" value="${esc(t.invoice_number||"")}"></label></div><div class="row"><button class="button primary" data-save-payment="${id}">Save Payment Info</button></div>`);
  if(key==="invoiceToggle"){await api(`/api/tasks/${id}`,{method:"PATCH",body:JSON.stringify({invoice_sent:!t.invoice_sent})});await load()}
  if(key==="checkResolution"){b.disabled=true;showPanel(id,`<div class="helper">Reviewing the attached email/chat chain for a possible resolution…</div>`);try{const r=await api(`/api/tasks/${id}/check-resolution`,{method:"POST"});if(r.assessment?.likely_resolved){await load()}else showPanel(id,`<div>No clear resolution yet.</div><div class="helper">${esc(r.assessment?.reason||"The communication does not appear to complete the task.")}</div>`)}catch(err){showPanel(id,`<div class="helper">${esc(err.message)}</div>`)}finally{b.disabled=false}}
  if(key==="gptHelp"){b.disabled=true;showPanel(id,`<div class="helper">Checking whether GPT can materially help complete this task…</div>`);try{const r=await api(`/api/tasks/${id}/gpt-help`,{method:"POST"});if(r.can_help){showPanel(id,`<div class="gpt-help"><strong>Yes — GPT can help.</strong><div>${esc(r.reason||"")}</div><label>Ready-to-use prompt<textarea rows="9">${esc(r.prompt||"")}</textarea></label></div>`)}else showPanel(id,`<div>GPT is not a good fit for completing this task directly.</div><div class="helper">${esc(r.reason||"")}</div>`)}catch(err){showPanel(id,`<div class="helper">${esc(err.message)}</div>`)}finally{b.disabled=false}}
  if(key==="showGptPrompt"){
    if((t.gpt_help_prompt||"").trim()){
      showPanel(id,`<div class="gpt-help"><strong>Prepared GPT Prompt</strong><div class="helper">${esc(t.gpt_help_reason||"")}</div><textarea rows="10">${esc(t.gpt_help_prompt||"")}</textarea></div>`);
    }else{
      b.disabled=true;showPanel(id,`<div class="helper">Preparing the prompt with OpenAI…</div>`);
      try{
        const r=await api(`/api/tasks/${id}/gpt-help`,{method:"POST"});
        if(r.can_help)showPanel(id,`<div class="gpt-help"><strong>Prepared GPT Prompt</strong><div class="helper">${esc(r.reason||"")}</div><textarea rows="10">${esc(r.prompt||"")}</textarea></div>`);
        else showPanel(id,`<div>GPT is not a good fit for completing this task directly.</div><div class="helper">${esc(r.reason||"")}</div>`);
        await load();
      }catch(err){showPanel(id,`<div class="helper">${esc(err.message)}</div>`)}
      finally{b.disabled=false}
    }
  }
  if(key==="suppressGptHelp"){
    if(confirm("Stop suggesting GPT help for future emails of this same type?")){
      await api(`/api/tasks/${id}/gpt-help/suppress`,{method:"POST"});
      await load();
    }
  }
  if(key==="reply"){state.currentReply=t;el("replyTo").value=t.email_to||"";el("replySubject").value=t.email_subject||`Re: ${t.title}`;el("replyBody").value=t.suggested_reply||`Hi,\n\nI wanted to follow up regarding ${t.title.toLowerCase()}.\n\n[INSERT CURRENT UPDATE]\n\nPlease let me know if you need anything further from us.\n\nThanks,\nTodd`;el("replyStatus").textContent="";el("openReplySource").classList.toggle("hidden",!(t.email_url||t.chat_space_uri));if(t.email_url||t.chat_space_uri)el("openReplySource").href=t.email_url||t.chat_space_uri;updateCompose();el("replyDialog").showModal()}
  if(key==="chatReply"){
    state.currentChatReply=t;
    el("chatReplyContext").textContent=`${t.party} · ${t.title}`;
    el("chatReplyBody").value=t.suggested_reply||"";
    el("chatReplyStatus").textContent="";
    el("chatReplyDialog").showModal();
  }
  if(key==="markPaid"){
    showPanel(id,`<div class="formgrid">
      <label>Paid Amount<input id="paidAmount-${id}" type="number" min="0" step="0.01" value="${Number(t.amount||0)}"></label>
      <label>Payment Reference<input id="paidReference-${id}" placeholder="Check #, ACH confirmation, card, etc."></label>
      <label class="span2">Payment Note<textarea id="paidNote-${id}" rows="2" placeholder="Optional payment details"></textarea></label>
    </div><div class="row"><button class="button primary" data-confirm-paid="${id}">Confirm Paid</button></div>`);
  }
  if(key==="liveNotTask"){
    if(confirm("Remove this from your open tasks and train the AI not to create similar tasks in the future?")){
      await api(`/api/tasks/${id}/not-task`,{method:"POST"});
      await load();
    }
  }
  if(key==="complete"){await api(`/api/tasks/${id}/complete`,{method:"POST"});await load()}
  if(key==="restore"){await api(`/api/tasks/${id}/restore`,{method:"POST"});await load()}
  if(key==="delete"&&confirm("Permanently delete this item?")){await api(`/api/tasks/${id}`,{method:"DELETE"});await load()}
  if(key==="resolutionYes"||key==="resolutionNo"){const reviewId=Number(b.dataset.reviewId),resolved=key==="resolutionYes";await api(`/api/tasks/${id}/resolution/${reviewId}`,{method:"POST",body:JSON.stringify({resolved})});await load()}
});
el("taskList").addEventListener("click",async e=>{
  const b=e.target.closest("[data-save-status],[data-save-note],[data-save-deadline],[data-save-payment,[data-confirm-paid]],[data-run-research]");if(!b)return;
  if(b.dataset.saveStatus){const id=Number(b.dataset.saveStatus);await api(`/api/tasks/${id}`,{method:"PATCH",body:JSON.stringify({status:el(`statusInput-${id}`).value})});await load()}
  if(b.dataset.saveNote){const id=Number(b.dataset.saveNote),body=el(`noteInput-${id}`).value.trim();if(body){await api(`/api/tasks/${id}/notes`,{method:"POST",body:JSON.stringify({body})});await load()}}
  if(b.dataset.saveDeadline){const id=Number(b.dataset.saveDeadline);await api(`/api/tasks/${id}`,{method:"PATCH",body:JSON.stringify({due_date:el(`deadlineInput-${id}`).value})});await load()}
  if(b.dataset.savePayment){const id=Number(b.dataset.savePayment);await api(`/api/tasks/${id}`,{method:"PATCH",body:JSON.stringify({amount:Number(el(`amountInput-${id}`).value||0),invoice_number:el(`invoiceInput-${id}`).value})});await load()}
  if(b.dataset.confirmPaid){
    const id=Number(b.dataset.confirmPaid);
    await api(`/api/tasks/${id}/mark-paid`,{method:"POST",body:JSON.stringify({
      paid_amount:Number(el(`paidAmount-${id}`).value||0),
      payment_reference:el(`paidReference-${id}`).value.trim(),
      payment_note:el(`paidNote-${id}`).value.trim()
    })});
    await load();
  }
  if(b.dataset.runResearch){const id=Number(b.dataset.runResearch),question=el(`researchInput-${id}`).value.trim();if(!question)return;const status=el(`researchStatus-${id}`);b.disabled=true;status.textContent="Searching your full Gmail history and analyzing matching messages…";try{const r=await api(`/api/tasks/${id}/email-research`,{method:"POST",body:JSON.stringify({question})});status.textContent=`${r.answer} (${(r.confidence||"").toUpperCase()} confidence)`;setTimeout(load,900)}catch(err){status.textContent=`Search failed: ${err.message}`}finally{b.disabled=false}}
});

// Gmail review.
el("suggestionList").addEventListener("click",async e=>{const approve=e.target.closest("[data-approve]"),suppress=e.target.closest("[data-suppress-gpt-suggestion]"),train=e.target.closest("[data-not-task]"),dismiss=e.target.closest("[data-dismiss]");if(approve){await api(`/api/gmail/suggestions/${approve.dataset.approve}/approve`,{method:"POST",body:"{}"});await load()}if(suppress){if(confirm("Stop suggesting GPT help for future emails of this same type?")){await api(`/api/gmail/suggestions/${suppress.dataset.suppressGptSuggestion}/gpt-help/suppress`,{method:"POST"});await load()}}if(train){train.disabled=true;train.textContent="Training…";try{await api(`/api/gmail/suggestions/${train.dataset.notTask}/not-task`,{method:"POST",body:JSON.stringify({reason:"Marked Not a Task from Gmail Review"})});await load()}catch(err){alert(err.message);train.disabled=false;train.textContent="Not a Task — Train"}}if(dismiss){await api(`/api/gmail/suggestions/${dismiss.dataset.dismiss}/dismiss`,{method:"POST"});await load()}});
// Chat review.
el("chatSuggestionList").addEventListener("click",async e=>{
  const make=e.target.closest("[data-chat-approve]"),invoice=e.target.closest("[data-chat-invoice]"),snooze=e.target.closest("[data-chat-snooze]"),chain=e.target.closest("[data-chat-dismiss-chain]"),dismiss=e.target.closest("[data-chat-dismiss]");
  if(make){await api(`/api/chat/suggestions/${make.dataset.chatApprove}/approve`,{method:"POST"});state.tab="client";await load()}
  if(invoice){await api(`/api/chat/suggestions/${invoice.dataset.chatInvoice}/invoice`,{method:"POST"});state.tab="invoice";await load()}
  if(snooze){await api(`/api/chat/suggestions/${snooze.dataset.chatSnooze}/snooze`,{method:"POST"});await load()}
  if(chain){const r=await api(`/api/chat/suggestions/${chain.dataset.chatDismissChain}/dismiss-chain`,{method:"POST"});await load();alert(`Dismissed ${r.dismissed||0} older chat item(s) from this chain.`)}
  if(dismiss){await api(`/api/chat/suggestions/${dismiss.dataset.chatDismiss}/dismiss`,{method:"POST"});await load()}
});
// Sent follow-ups.
el("sentList").addEventListener("click",async e=>{const create=e.target.closest("[data-sent-create-task]"),dismiss=e.target.closest("[data-sent-dismiss]"),open=e.target.closest("[data-open-linked-task]");if(create){await api(`/api/sent-followups/${create.dataset.sentCreateTask}/create-task`,{method:"POST"});await load();state.tab="client";updateViews()}if(dismiss){await api(`/api/sent-followups/${dismiss.dataset.sentDismiss}/dismiss`,{method:"POST"});await load()}if(open){state.tab="client";state.quickFilter="";el("search").value="";updateViews();setTimeout(()=>{const node=el(`task-${open.dataset.openLinkedTask}`);if(node)node.scrollIntoView({behavior:"smooth",block:"start"})},100)}});
// Meeting recap review.
el("meetingList").addEventListener("click",async e=>{const add=e.target.closest("[data-meeting-add]"),dismiss=e.target.closest("[data-meeting-dismiss]");if(add){const id=Number(add.dataset.meetingAdd),selected=[...document.querySelectorAll(`[data-meeting-task="${id}"]:checked`)].map(cb=>{const index=Number(cb.dataset.taskIndex),input=document.querySelector(`[data-meeting-assignee="${id}"][data-task-index="${index}"]`);return{index,assignee:input?.value.trim()||""}});if(!selected.length)return;const status=el(`meeting-status-${id}`);add.disabled=true;status.textContent="Adding selected meeting tasks…";try{const r=await api(`/api/meetings/reviews/${id}/add`,{method:"POST",body:JSON.stringify({tasks:selected})});status.textContent=`Added ${r.added} task(s).`;await load();state.tab="client";updateViews()}catch(err){status.textContent=err.message}finally{add.disabled=false}}if(dismiss){await api(`/api/meetings/reviews/${dismiss.dataset.meetingDismiss}/dismiss`,{method:"POST"});await load()}});


el("invoiceSearch").addEventListener("input",renderInvoices);
el("paidAccountsSearch").addEventListener("input",renderPaidAccounts);
document.addEventListener("click",e=>{
  const age=e.target.closest("[data-invoice-age]");
  if(age){state.invoiceAge=age.dataset.invoiceAge;renderInvoices()}
});
el("clearInvoiceAge").addEventListener("click",()=>{state.invoiceAge="all";renderInvoices()});

el("invoiceView").addEventListener("click",async e=>{
  const edit=e.target.closest("[data-record-edit]"),paid=e.target.closest("[data-invoice-paid]"),del=e.target.closest("[data-invoice-delete]");
  if(edit)await openRecordDetail(Number(edit.dataset.recordEdit));
  if(paid){
    const t=getTask(Number(paid.dataset.invoicePaid));if(!t)return;
    if(confirm(`Mark invoice ${t.invoice_number||""} paid for the remaining ${money(t.amount,t.currency)}?`)){
      await api(`/api/tasks/${t.id}/mark-paid`,{method:"POST",body:JSON.stringify({paid_amount:Number(t.amount||0)})});
      const inv=String(t.invoice_number||"").trim().toLowerCase();
      state.payment=state.payment.filter(x=>x.id!==t.id&&(!inv||String(x.invoice_number||"").trim().toLowerCase()!==inv));
      await load()
    }
  }
  if(del&&confirm("Move this invoice to Deleted history?")){
    const id=Number(del.dataset.invoiceDelete),t=getTask(id),inv=String(t?.invoice_number||"").trim().toLowerCase();
    await api(`/api/invoices/${id}/delete`,{method:"POST"});
    state.payment=state.payment.filter(x=>x.id!==id&&(!inv||String(x.invoice_number||"").trim().toLowerCase()!==inv));
    await load()
  }
});
el("paidaccountsView").addEventListener("click",async e=>{
  const edit=e.target.closest("[data-record-edit]"),rec=e.target.closest("[data-account-reconciled]"),del=e.target.closest("[data-account-delete]");
  if(edit)await openRecordDetail(Number(edit.dataset.recordEdit));
  if(rec&&confirm("Mark this payment reconciled?")){await api(`/api/paid-accounts/${rec.dataset.accountReconciled}/reconcile`,{method:"POST"});await load()}
  if(del&&confirm("Move this reconciliation item to Deleted history?")){await api(`/api/paid-accounts/${del.dataset.accountDelete}/delete`,{method:"POST"});await load()}
});
el("recordDetailDialog").addEventListener("click",async e=>{
  const save=e.target.closest("[data-record-save]"),partial=e.target.closest("[data-record-partial]"),note=e.target.closest("[data-record-note]"),research=e.target.closest("[data-record-research]"),reply=e.target.closest("[data-record-reply]"),resolution=e.target.closest("[data-record-resolution]");
  if(note){
    const id=Number(note.dataset.recordNote),body=el("recordNoteText").value.trim();
    if(body){await api(`/api/tasks/${id}/notes`,{method:"POST",body:JSON.stringify({body})});await openRecordDetail(id)}
  }
  if(research){
    const id=Number(research.dataset.recordResearch),question=el("recordResearchText").value.trim();if(!question)return;
    const status=el("recordActionStatus");status.textContent="Searching email…";
    try{const r=await api(`/api/tasks/${id}/email-research`,{method:"POST",body:JSON.stringify({question})});status.textContent=r.answer||"No answer returned."}catch(err){status.textContent=err.message}
  }
  if(reply){
    const id=Number(reply.dataset.recordReply),t=await api(`/api/tasks/${id}`);
    state.currentReply=t;el("replyTo").value=t.email_to||"";el("replySubject").value=t.email_subject||`Re: ${t.title}`;el("replyBody").value=t.suggested_reply||"";el("replyStatus").textContent="";updateCompose();el("replyDialog").showModal()
  }
  if(resolution){
    const id=Number(resolution.dataset.recordResolution),status=el("recordActionStatus");status.textContent="Checking attached communication…";
    try{const r=await api(`/api/tasks/${id}/check-resolution`,{method:"POST"});status.textContent=r.assessment?.likely_resolved?`Possible resolution: ${r.assessment.summary||r.assessment.reason||""}`:(r.assessment?.reason||"No clear resolution yet.")}catch(err){status.textContent=err.message}
  }
  if(save){
    const id=Number(save.dataset.recordSave);
    await api(`/api/tasks/${id}`,{method:"PATCH",body:JSON.stringify({
      party:el("recordParty").value.trim(),
      invoice_number:el("recordInvoice").value.trim(),
      amount:Number(el("recordAmount").value||0),
      due_date:el("recordDue").value,
      detail:el("recordDetailText").value.trim()
    })});
    el("recordDetailDialog").close();await load()
  }
  if(partial){
    const id=Number(partial.dataset.recordPartial);
    const amount=Number(el("recordPartialAmount").value||0);
    const r=await api(`/api/invoices/${id}/partial-payment`,{method:"POST",body:JSON.stringify({
      amount,
      reference:el("recordPartialReference").value.trim(),
      note:el("recordPartialNote").value.trim()
    })});
    el("recordDetailDialog").close();await load();
    if(r.fully_paid)alert("The partial payment cleared the remaining balance, so the invoice was moved to Paid history.")
  }
});

// Full communications sync.
el("settingsBtn").addEventListener("click",()=>el("settingsDialog").showModal());
el("diagnosticsBtn").addEventListener("click",async()=>{await loadSystem();el("diagnosticsDialog").showModal()});
el("checkChat").addEventListener("click",runChatDiagnostics);
async function pollManualSync(){
  try{
    const s=await api("/api/communications/sync/status");
    if(s.running){
      el("syncAll").disabled=true;
      el("syncAll").textContent="Syncing…";
      el("syncStatus").textContent="Sync running in background…";
      state.syncPoll=setTimeout(pollManualSync,2500);
      return;
    }
    state.syncPoll=null;
    el("syncAll").disabled=false;
    el("syncAll").textContent="Sync";
    if(s.error){
      el("syncStatus").textContent=`Sync issue: ${s.error}`;
    }else if(s.finished_at){
      const r=s.result||{},g=r.gmail||{},sent=r.sent||{},chat=r.chat||{};
      el("syncStatus").textContent=`Synced ${safeDate(s.finished_at)} · ${g.added||0} mail · ${chat.added||0} chat · ${sent.new_monitors||0} follow-up`;
    }else{
      el("syncStatus").textContent="Ready";
    }
    await load();
  }catch(err){
    state.syncPoll=null;
    el("syncAll").disabled=false;
    el("syncAll").textContent="Sync";
    el("syncStatus").textContent=`Sync status error: ${err.message}`;
  }
}

el("syncAll").addEventListener("click",async()=>{
  el("syncAll").disabled=true;
  el("syncAll").textContent="Starting…";
  el("syncStatus").textContent="Starting sync…";
  try{
    const r=await api("/api/communications/sync",{method:"POST"});
    if(r.busy&&!r.running){
      el("syncStatus").textContent=r.message||"Scheduled sync is already running.";
      el("syncAll").disabled=false;
      el("syncAll").textContent="Sync";
      return;
    }
    if(state.syncPoll)clearTimeout(state.syncPoll);
    state.syncPoll=setTimeout(pollManualSync,800);
  }catch(err){
    el("syncAll").disabled=false;
    el("syncAll").textContent="Sync";
    el("syncStatus").textContent=`Could not start sync: ${err.message}`;
  }
});

// Reply dialog.
function updateCompose(){const to=encodeURIComponent(el("replyTo").value),su=encodeURIComponent(el("replySubject").value),body=encodeURIComponent(el("replyBody").value);el("openGmailCompose").href=`https://mail.google.com/mail/?view=cm&fs=1&to=${to}&su=${su}&body=${body}`}
["replyTo","replySubject","replyBody"].forEach(id=>el(id).addEventListener("input",updateCompose));
el("saveGmailDraft").addEventListener("click",async()=>{const t=state.currentReply;if(!t)return;el("replyStatus").textContent="Saving…";try{const r=await api(`/api/tasks/${t.id}/gmail/draft`,{method:"POST",body:JSON.stringify({to:el("replyTo").value,subject:el("replySubject").value,body:el("replyBody").value})});el("replyStatus").innerHTML=`Draft saved. <a href="${r.gmail_drafts_url}" target="_blank">Open Gmail Drafts</a>`}catch(err){el("replyStatus").textContent=err.message}});
el("sendGmailReply").addEventListener("click",async()=>{const t=state.currentReply;if(!t||!confirm(`Send now to ${el("replyTo").value}?`))return;el("replyStatus").textContent="Sending…";try{await api(`/api/tasks/${t.id}/gmail/send`,{method:"POST",body:JSON.stringify({to:el("replyTo").value,subject:el("replySubject").value,body:el("replyBody").value})});el("replyStatus").textContent="Sent. Status changed to Waiting.";setTimeout(async()=>{el("replyDialog").close();await load()},800)}catch(err){el("replyStatus").textContent=err.message}});

// Add task.
el("addTaskBtn").addEventListener("click",()=>el("newTaskPanel").classList.remove("hidden"));el("cancelNewTask").addEventListener("click",()=>el("newTaskPanel").classList.add("hidden"));
el("saveNewTask").addEventListener("click",async()=>{const title=el("newTitle").value.trim();if(!title)return alert("Enter a task.");await api("/api/tasks",{method:"POST",body:JSON.stringify({category:el("newCategory").value,party:el("newParty").value.trim()||"Unassigned",assignee:el("newAssignee").value.trim(),title,priority:el("newPriority").value,due_date:el("newDue").value,detail:el("newDetail").value.trim(),amount:Number(el("newAmount").value||0),invoice_number:el("newInvoiceNumber").value.trim(),invoice_sent:el("newInvoiceSent").checked,status:"Open"})});["newParty","newAssignee","newTitle","newDue","newDetail","newAmount","newInvoiceNumber"].forEach(id=>el(id).value="");el("newInvoiceSent").checked=false;el("newTaskPanel").classList.add("hidden");await load()});
el("search").addEventListener("input",renderTasks);el("statusFilter").addEventListener("change",renderTasks);el("sortBy").addEventListener("change",()=>{state.sortBy=el("sortBy").value;renderTasks()});

// Global Gmail discovery across full history unless the user's question limits dates.
el("gmailTalkBtn").addEventListener("click",()=>{el("discoveryResults").innerHTML="";el("discoveryStatus").textContent="";el("addSelectedDiscovery").classList.add("hidden");el("gmailDiscoveryDialog").showModal();setTimeout(()=>el("gmailDiscoveryQuery").focus(),100)});
function discoveryCard(t,index){const evidence=(t.evidence||[]).map(e=>`<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.subject||"Email")}</a>`).join(" · ");return `<article class="discovery-card"><label class="discovery-select"><input type="checkbox" data-discovery-index="${index}" checked><div><div class="task-head"><div><h3>${esc(t.title)}</h3><div class="party">${esc(t.party)}</div></div><div class="bubbles"><span class="bubble ${t.category==="payment"?"payment":"client"}">${t.category==="payment"?"PAYMENT":"CLIENT"}</span>${Number(t.recipient_count||0)>1?`<span class="bubble multi">MULTI-PERSON · ${t.recipient_count}</span>`:""}${t.gpt_can_help?`<span class="bubble gpt">GPT CAN HELP</span>`:""}<span class="bubble ${esc(t.priority)}">${esc((t.priority||"normal").toUpperCase())}</span>${t.due_date?`<span class="bubble future">Due ${esc(t.due_date)}</span>`:""}</div></div><div class="detail">${esc(t.summary)}</div>${t.category==="payment"?`<div class="payment-box"><div class="payment-field"><span>Amount</span><strong>${money(t.amount,t.currency)}</strong></div><div class="payment-field"><span>Invoice #</span><strong>${esc(t.invoice_number||"—")}</strong></div><div class="payment-field"><span>Invoice Sent</span><strong>${t.invoice_sent?"Yes":"No"}</strong></div></div>`:""}<div class="evidence">Evidence: ${evidence||"1 matching email"} · ${(t.confidence||"").toUpperCase()} confidence</div></div></label></article>`}
el("runDiscoveryBtn").addEventListener("click",async()=>{const query=el("gmailDiscoveryQuery").value.trim();if(!query)return;el("runDiscoveryBtn").disabled=true;el("discoveryStatus").textContent="Searching your Gmail history and building possible tasks…";el("discoveryResults").innerHTML="";try{const r=await api("/api/gmail/discover-tasks",{method:"POST",body:JSON.stringify({query})});state.discoveryTasks=r.tasks||[];el("discoveryStatus").textContent=`Gmail query: ${r.query}. Found ${state.discoveryTasks.length} possible task(s).`;el("discoveryResults").innerHTML=state.discoveryTasks.length?state.discoveryTasks.map(discoveryCard).join(""):`<div class="panel">No unresolved tasks were identified from those emails.</div>`;el("addSelectedDiscovery").classList.toggle("hidden",!state.discoveryTasks.length)}catch(err){el("discoveryStatus").textContent=`Search failed: ${err.message}`}finally{el("runDiscoveryBtn").disabled=false}});
el("addSelectedDiscovery").addEventListener("click",async()=>{const selected=[...document.querySelectorAll("[data-discovery-index]:checked")].map(cb=>state.discoveryTasks[Number(cb.dataset.discoveryIndex)]).filter(Boolean);if(!selected.length)return;el("addSelectedDiscovery").disabled=true;try{const r=await api("/api/gmail/discover-add",{method:"POST",body:JSON.stringify({tasks:selected})});el("discoveryStatus").textContent=`Added ${r.added} task(s) to the Action Center.`;await load();setTimeout(()=>el("gmailDiscoveryDialog").close(),700)}catch(err){el("discoveryStatus").textContent=`Could not add tasks: ${err.message}`}finally{el("addSelectedDiscovery").disabled=false}});
el("voiceInputBtn").addEventListener("click",()=>{const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SpeechRecognition){el("discoveryStatus").textContent="Voice input is not supported in this browser. Type your request instead.";return}const recognition=new SpeechRecognition();recognition.lang="en-US";recognition.interimResults=false;recognition.maxAlternatives=1;el("voiceInputBtn").classList.add("listening");el("voiceInputBtn").textContent="Listening…";recognition.onresult=event=>{el("gmailDiscoveryQuery").value=event.results[0][0].transcript};recognition.onerror=event=>{el("discoveryStatus").textContent=`Voice input error: ${event.error}`};recognition.onend=()=>{el("voiceInputBtn").classList.remove("listening");el("voiceInputBtn").textContent="🎤 Talk"};recognition.start()});

// Highly watched domains.
async function loadWatchDomains(){state.watchDomains=await api("/api/watch-domains");renderWatchDomains()}
function renderWatchDomains(){el("watchDomainList").innerHTML=state.watchDomains.length?state.watchDomains.map(d=>`<div class="watch-item"><div><div class="domain">${esc(d.domain)} ${d.enabled?'<span class="pill ok">Watching</span>':'<span class="pill neutral">Paused</span>'}</div>${d.label?`<div class="label">${esc(d.label)}</div>`:""}</div><div class="row"><button class="button secondary" data-watch-toggle="${d.id}" data-enabled="${d.enabled?1:0}">${d.enabled?"Pause":"Resume"}</button><button class="button secondary danger" data-watch-delete="${d.id}">Remove</button></div></div>`).join(""):`<div class="panel">No domains are on high watch yet.</div>`}
el("watchDomainsBtn").addEventListener("click",async()=>{el("settingsDialog").close();el("watchDomainStatus").textContent="";await loadWatchDomains();el("watchDomainsDialog").showModal()});
el("addWatchDomain").addEventListener("click",async()=>{const domain=el("watchDomainInput").value.trim(),label=el("watchDomainLabel").value.trim();if(!domain)return;try{await api("/api/watch-domains",{method:"POST",body:JSON.stringify({domain,label})});el("watchDomainInput").value="";el("watchDomainLabel").value="";el("watchDomainStatus").textContent="Domain added to high watch.";await loadWatchDomains()}catch(err){el("watchDomainStatus").textContent=err.message}});
el("watchDomainList").addEventListener("click",async e=>{const del=e.target.closest("[data-watch-delete]"),tog=e.target.closest("[data-watch-toggle]");if(del){await api(`/api/watch-domains/${del.dataset.watchDelete}`,{method:"DELETE"});await loadWatchDomains()}if(tog){const enabled=tog.dataset.enabled!=="1";await api(`/api/watch-domains/${tog.dataset.watchToggle}`,{method:"PATCH",body:JSON.stringify({enabled})});await loadWatchDomains()}});

// Dialog close buttons.
document.addEventListener("click",e=>{const b=e.target.closest("[data-close-dialog]");if(b)el(b.dataset.closeDialog).close()});


el("sendChatReply").addEventListener("click",async()=>{
  const t=state.currentChatReply;if(!t)return;
  const text=el("chatReplyBody").value.trim();if(!text)return alert("Enter a Chat reply.");
  if(!confirm(`Send this Google Chat reply for "${t.title}"?`))return;
  el("sendChatReply").disabled=true;el("chatReplyStatus").textContent="Sending…";
  try{
    const r=await api(`/api/tasks/${t.id}/chat/reply`,{method:"POST",body:JSON.stringify({text})});
    el("chatReplyStatus").textContent=r.resolution_assessment?.likely_resolved
      ?"Sent. This may resolve the task; a confirmation card was added."
      :"Sent. Task moved to Waiting and the reply was stored in the communication log.";
    setTimeout(async()=>{el("chatReplyDialog").close();await load()},900);
  }catch(err){el("chatReplyStatus").textContent=err.message}
  finally{el("sendChatReply").disabled=false}
});

load().catch(err=>{el("taskList").innerHTML=`<div class="panel">Could not load: ${esc(err.message)}</div>`});
setInterval(async()=>{if(state.system?.gmail_connected){try{await api("/api/communications/sync",{method:"POST"});await load()}catch(_){}}},10*60*1000);
