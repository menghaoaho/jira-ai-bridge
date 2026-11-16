'use strict';
(() => {
  const {$,icon,icons,el,date,request,copy}=window.RelayUI;
  const demo=document.documentElement.dataset.mode==='demo';
  let report={devices:[],commands:[]}, view='overview', busy=false, selected=null, sequence=0;
  const now=()=>Date.now()/1000;
  const demoState={admin:'演示管理员',version:'1.1.0',devices:[
    {id:'demo-day',label:'研发同事 · 白班',hostname:'TEST-DAY',status:'正常转发',enabled:1,last_seen:now(),health_reason:'演示：已确认这台电脑可以访问 Jira',version:'1.1.0',created:now()-86400},
    {id:'demo-night',label:'测试同事 · 晚班',hostname:'TEST-NIGHT',status:'正常转发',enabled:1,last_seen:now(),health_reason:'演示：已确认这台电脑可以访问 Jira',version:'1.1.0',created:now()-86400},
    {id:'demo-backup',label:'备用电脑',hostname:'TEST-BACKUP',status:'离线',enabled:1,last_seen:now()-9000,health_reason:'请确认电脑已开机、唤醒并连接公司网络。',version:'1.1.0',created:now()-172800},
    {id:'demo-old',label:'旧电脑',hostname:'TEST-OLD',status:'已停用',enabled:0,last_seen:now()-172800,health_reason:'已被管理员停用',version:'1.0.0',created:now()-172800}],
    commands:[{id:'demo-invite-1',label:'新同事的办公电脑',status:'待安装',expires:now()+86400},{id:'demo-invite-2',label:'晚班备用电脑',status:'待安装',expires:now()+86400}]};
  const adminApi=(path,body)=>request('/relay-admin/api/'+path,body,true);
  async function api(path,body) {
    if (!demo) return adminApi(path,body);
    await new Promise(resolve=>setTimeout(resolve,120));
    if (path==='report') return {...structuredClone(demoState),now:now()};
    if (path==='commands' || path==='reauthorize') {
      const label=path==='commands'?body.label:demoState.devices.find(d=>d.id===body.id).label;
      const invitation={id:crypto.randomUUID(),label,status:'待安装',expires:now()+(body.hours||24)*3600};demoState.commands.unshift(invitation);
      const target=document.documentElement.dataset.static==='true'?'install.html':'../relay-start/';
      return {...invitation,share_url:new URL(target,location.href).href+'#demo',command:'演示环境不会生成可执行的安装授权。正式部署后，这里会提供专属命令。'};
    }
    if (path==='disable-device') { const d=demoState.devices.find(d=>d.id===body.id);d.enabled=0;d.status='已停用';d.health_reason='已被管理员停用'; }
    if (path==='revoke-command') demoState.commands.find(c=>c.id===body.id).status='已撤销';
    return {ok:true};
  }
  function note(text,error=false) { $('notice').hidden=!text;$('notice').textContent=text;$('notice').className='notice'+(error?' error':''); }
  function state(device) { if(!device.enabled)return ['已停用','disabled'];if(device.status==='正常转发')return ['正常连接','online'];if(device.status==='离线')return ['已离线','attention'];if(device.status==='内网不可达')return ['检查公司网络','attention'];return ['正在检查','pending']; }
  function badge(text,kind) { return el('span',text,'status-badge '+kind); }
  function renderDevices() {
    const query=$('device-search').value.trim().toLocaleLowerCase();const filter=$('status-filter').value;
    const items=report.devices.filter(d=>(d.label+' '+d.hostname).toLocaleLowerCase().includes(query)&&(view!=='devices'||filter==='all'||state(d)[1]===filter));
    const shown=view==='overview'?items.slice(0,6):items;
    $('devices').replaceChildren();
    for (const device of shown) {
      const row=el('tr');const name=el('td');const group=el('div',undefined,'device-name');const text=el('div');
      text.append(el('strong',device.label),el('small',device.hostname||'等待电脑登记'));group.append(icon('computer'),text);name.append(group);
      const status=el('td');status.append(badge(...state(device)));
      const activity=el('td',date(device.last_seen,true),'last-activity');const action=el('td',undefined,'action-column');
      const button=el('button',undefined,'icon-button');button.setAttribute('aria-label','查看 '+device.label+' 的详情');button.append(icon('more'));button.addEventListener('click',()=>openDevice(device));action.append(button);
      row.append(name,status,activity,action);$('devices').append(row);
    }
    $('devices-loading').hidden=shown.length>0;$('device-table').hidden=!shown.length;
    $('devices-loading').textContent=report.devices.length?(query?'没有找到匹配的电脑。':'此状态下没有电脑。'):'还没有电脑接入。点击“邀请同事安装”，开始连接第一台电脑。';
    $('device-total').textContent=query||view==='devices'?'显示 '+shown.length+' 台电脑':'共 '+report.devices.length+' 台已登记电脑';
  }
  function render() {
    $('account').textContent=report.admin||'管理员';$('version').textContent='连接助手 '+(report.version||'');
    $('stat-online').textContent=report.devices.filter(d=>state(d)[1]==='online').length;
    $('stat-attention').textContent=report.devices.filter(d=>state(d)[1]==='attention').length;
    const waiting=report.commands.filter(c=>c.status==='待安装').length;$('stat-waiting').textContent=waiting;$('invite-count').textContent=waiting;$('invite-count').hidden=!waiting;
    $('commands').replaceChildren();
    for(const invitation of report.commands) {
      const row=el('tr');row.append(el('td',invitation.label));const status=el('td');status.append(badge(invitation.status,invitation.status==='待安装'?'pending':'disabled'));row.append(status,el('td',date(invitation.expires,true)));
      const action=el('td');if(invitation.status==='待安装') {const button=el('button','撤销邀请','secondary');button.addEventListener('click',()=>run(button,async()=>{await api('revoke-command',{id:invitation.id});note('邀请已撤销，这个链接将不能再用于安装。');}));action.append(button);}else action.textContent='—';row.append(action);$('commands').append(row);
    }
    if(!report.commands.length){const row=el('tr'),cell=el('td','还没有安装邀请。创建一份邀请，再把链接发给同事。','empty');cell.colSpan=4;row.append(cell);$('commands').append(row);}
    $('updated').textContent='更新于 '+date(report.now,true)+'（北京时间）';renderDevices();
  }
  async function refresh() {const current=++sequence;const next=await api('report');if(current!==sequence)return;report=next;render();$('connection-label').replaceChildren(el('span',undefined,'dot'),document.createTextNode(demo?'演示数据 · 仅本页生效':'状态自动更新'));}
  function route() {
    const requested=location.hash.slice(1);if(requested==='main')return;view=['overview','devices','invitations','help'].includes(requested)?requested:'overview';
    const titles={overview:['连接概览','你管理授权，同事按引导完成安装。'],devices:['设备管理','查看每台电脑的连接情况，按需管理接入权限。'],invitations:['安装邀请','把专属链接发给同事，安装和连接交给引导。'],help:['使用帮助','遇到问题时，先从这里找到下一步。']};
    document.querySelector('.workspace').dataset.view=view;$('page-title').textContent=titles[view][0];$('page-subtitle').textContent=titles[view][1];
    document.querySelectorAll('[data-view]').forEach(a=>{a.classList.toggle('active',a.dataset.view===view);if(a.dataset.view===view)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    $('overview-stats').hidden=!['overview','devices'].includes(view);$('onboarding').hidden=view!=='overview';$('device-panel').hidden=!['overview','devices'].includes(view);$('invitation-panel').hidden=view!=='invitations';$('help-panel').hidden=view!=='help';$('tips').hidden=view!=='overview';$('status-filter').hidden=view!=='devices';$('view-all').hidden=view!=='overview';$('device-heading').textContent=view==='overview'?'最近设备':'全部设备';renderDevices();
  }
  async function run(button,work) {if(busy)return;busy=true;button.disabled=true;try{await work();await refresh();}catch(error){note(error.message||'网络连接失败，请稍后重试。',true);}finally{busy=false;button.disabled=false;}}
  function showShare(data) {
    $('copy-link').replaceChildren(icon('copy'),document.createTextNode('复制链接，发给同事'));$('share-caption').textContent=data.label+' · '+date(data.expires)+' 前有效';$('share-url').value=data.share_url;$('command').value=data.command;
    const link=new URL(data.share_url);if(link.hostname!==location.hostname || (!demo&&link.protocol!=='https:') || !link.pathname.endsWith('/relay-start/')&&!(demo&&link.pathname.endsWith('/install.html')))throw new Error('邀请地址与当前站点不一致，请联系管理员。');
    $('open-install').href=data.share_url;$('share-dialog').showModal();
  }
  function openDevice(device) {
    selected=device;$('detail-name').textContent=device.label;$('detail-host').textContent=device.hostname||'等待电脑登记';$('detail-error').hidden=true;
    const list=el('dl',undefined,'detail-list');
    for(const [title,text] of [['连接状态',state(device)[0]],['最近连接',date(device.last_seen)],['软件版本',device.version||'未读取'],['接入时间',date(device.created)],['状态说明',device.health_reason]]){const row=el('div');row.append(el('dt',title),el('dd',text));list.append(row);}
    $('detail-body').replaceChildren(list);$('device-action').textContent=device.enabled?'停用这台电脑':'重新生成安装邀请';$('device-action').className=device.enabled?'danger':'primary';$('device-dialog').showModal();
  }
  document.querySelectorAll('.invite-button').forEach(button=>button.addEventListener('click',()=>{$('create-error').hidden=true;$('invite-dialog').showModal();}));
  document.querySelectorAll('.close-dialog').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
  $('share-dialog').addEventListener('close',()=>{$('share-url').value='';$('command').value='';$('open-install').removeAttribute('href');});
  $('create').addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;const form=event.currentTarget,button=form.querySelector('[type=submit]');busy=true;button.disabled=true;$('create-error').hidden=true;
    try{const data=new FormData(form);const result=await api('commands',{label:data.get('label'),hours:Number(data.get('hours'))});$('invite-dialog').close();showShare(result);form.reset();await refresh();}catch(error){$('create-error').textContent=error.message;$('create-error').hidden=false;}finally{busy=false;button.disabled=false;}
  });
  $('device-action').addEventListener('click',async()=>{
    if(!selected)return;const target=selected;
    if(target.enabled){$('confirm-text').textContent=target.label;const dialog=$('confirm-dialog');dialog.returnValue='';const choice=new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue),{once:true}));dialog.showModal();if(await choice!=='disable')return;}
    await run($('device-action'),async()=>{if(target.enabled){await api('disable-device',{id:target.id});$('device-dialog').close();note('已停用这台电脑。通常60秒内退出转发，其他电脑继续工作。');}else{const result=await api('reauthorize',{id:target.id});$('device-dialog').close();showShare(result);}});
  });
  $('copy-link').addEventListener('click',async()=>{const ok=await copy($('share-url'));$('copy-link').textContent=ok?'链接已复制，可发给同事':'请按 Ctrl+C（Mac 为 ⌘C）复制选中链接';});
  $('copy-command').addEventListener('click',async()=>{if(demo){note('当前是交互演示，不会生成实际安装授权。');return;}const ok=await copy($('command'));note(ok?'完整命令已复制。':'请按 Ctrl+C（Mac 为 ⌘C）复制选中命令。');});
  $('device-search').addEventListener('input',renderDevices);$('status-filter').addEventListener('change',renderDevices);$('refresh').addEventListener('click',()=>refresh().catch(error=>note(error.message,true)));window.addEventListener('hashchange',route);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy)refresh().catch(error=>note(error.message,true));});setInterval(()=>{if(!document.hidden&&!busy)refresh().catch(error=>note(error.message,true));},15000);
  if(demo){$('demo-label').hidden=false;$('coworker-preview').hidden=false;$('coworker-preview').href=(document.documentElement.dataset.static==='true'?'install.html':'../relay-start/')+'#demo';}
  icons();route();refresh().catch(error=>{note(error.message,true);$('devices-loading').textContent='暂时无法读取设备。请确认已登录管理员账号，再点击刷新。';$('connection-label').textContent='连接暂不可用';});
})();
