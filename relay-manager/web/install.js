'use strict';
(() => {
  const {$,icons,icon,request,date,downloadLauncher}=window.RelayUI;
  const demo=document.documentElement.dataset.mode==='demo';
  const codePattern=/^[A-Za-z0-9_-]{43}$/, storageKey='jira-relay-install-invitation', retentionMs=30*60*1000;
  let code=location.hash.slice(1), invitation=null, stage=1, downloaded=false, demoTicks=0, timer=null, checking=false, pollCount=0;
  if(!demo) {
    if(location.hash) {
      if(codePattern.test(code)) {
        try {
          const saved=JSON.stringify({code,expires:Date.now()+retentionMs});
          sessionStorage.setItem(storageKey,saved);
          if(sessionStorage.getItem(storageKey)===saved)history.replaceState(null,'',location.pathname+location.search);
        } catch { /* Keep the fragment when this tab cannot save the invitation. */ }
      } else {
        // An explicit invalid invitation must never fall back to a previous one.
        try{sessionStorage.removeItem(storageKey);}catch{}
      }
    } else {
      try {
        const saved=JSON.parse(sessionStorage.getItem(storageKey));
        if(saved&&codePattern.test(saved.code)&&Number.isFinite(saved.expires)&&saved.expires>Date.now()&&saved.expires<=Date.now()+retentionMs)code=saved.code;
        else sessionStorage.removeItem(storageKey);
      } catch {
        try{sessionStorage.removeItem(storageKey);}catch{}
      }
    }
  }
  const windows=/Windows NT (10\.0|11\.0)/.test(navigator.userAgent)&&!(/ARM64|aarch64/i.test(navigator.userAgent));
  function error(text) {$('install-error').textContent=text;$('install-error').hidden=!text;}
  function go(next) {
    stage=next;$('check-stage').hidden=next!==1;$('download-stage').hidden=next!==2;$('status-stage').hidden=next!==3;
    for(let i=1;i<=3;i++){$('step-'+i).classList.toggle('current',i===next);$('step-'+i).classList.toggle('done',i<next);}
  }
  function statusIcon(name,spin=false) {const node=$('status-icon');node.replaceChildren(icon(name).firstChild);node.classList.toggle('spinner',spin);}
  function state(data) {
    const connected=data.connection==='正常转发';const disabled=data.connection==='已停用';
    $('status-title').textContent=connected?'连接成功，已经准备好了':disabled?'这台电脑已被停用':data.status==='ready'?'还没有检测到安装':'已检测到电脑，正在确认连接';
    $('status-detail').textContent=connected?'这台电脑已通过云端检查，可以正常连接 Jira。保持电脑开机、唤醒并连接公司网络即可。':disabled?'请联系管理员获取新的安装邀请。':data.status==='ready'?'请先打开已下载的安装文件，确认系统授权。这里会在接入后自动更新。':data.connection==='内网不可达'?'电脑已接入，但还不能访问公司 Jira。请检查公司网络，或运行桌面的“Jira转发-检查状态”。':data.connection==='离线'?'电脑已经登记，目前处于离线状态。请确认安装工具正在运行，并检查公司网络。':'正在等待安装工具自检完成，请保持此页面打开。';
    $('status-ready').hidden=!connected;$('retry-status').hidden=connected||disabled;statusIcon(connected?'check':disabled?'lock':'refresh',!connected&&!disabled);
    if(connected||disabled){clearInterval(timer);timer=null;}
  }
  async function getInvitation() {
    if(demo) return {label:'演示同事 · 办公电脑',expires:Date.now()/1000+86400,status:downloaded?'used':'ready',connection:demoTicks>=2?'正常转发':'正在检查',detail:'演示连接流程'};
    if(!codePattern.test(code))throw new Error('缺少有效的安装邀请。请打开管理员发来的完整专属链接。');
    return request('/relay-api/v1/invitation',{code});
  }
  async function poll() {
    if(checking||document.hidden||stage!==3)return;
    if(++pollCount>120){clearInterval(timer);timer=null;$('status-title').textContent='等待时间较长，请检查安装窗口';$('status-detail').textContent='先查看安装工具提示；确认网络后，点击“重新检查连接”。';statusIcon('help');return;}
    checking=true;
    try{if(demo)demoTicks++;const data=await getInvitation();updateInvitation(data);state(data);}catch(e){$('status-title').textContent='暂时无法确认连接';$('status-detail').textContent=e.message;statusIcon('help');clearInterval(timer);timer=null;}finally{checking=false;}
  }
  function startStatus() {go(3);pollCount=0;clearInterval(timer);timer=setInterval(poll,5000);poll();}
  function canDownload() {return demo||!!(invitation&&invitation.status==='ready'&&typeof invitation.command==='string'&&invitation.command);}
  function updateInvitation(data) {
    invitation=data;
    const used=!demo&&data.status==='used';
    $('download').hidden=used;$('download-again').hidden=used;
    $('download').disabled=!canDownload()||(!windows&&!demo);$('download-again').disabled=!canDownload()||(!windows&&!demo);
    $('used-invitation-note').hidden=!used;
    $('back-install').textContent=used?'查看原安装文件说明':'返回安装说明';
    if(used) {
      $('download-message').textContent='这份邀请已经用于登记电脑';
      $('download-title').textContent='运行原安装文件，或检查桌面工具';
      $('download-file-title').textContent='打开原先下载的 Jira-Connect-Setup.cmd';
      $('download-file-note').textContent='如果原文件已丢失，请联系管理员重新授权。';
    }
  }
  function download() {
    if(!canDownload())return;
    try{if(!demo)downloadLauncher(invitation.command);downloaded=true;go(2);}catch(e){error(e.message);go(1);}
  }
  $('download').addEventListener('click',download);$('download-again').addEventListener('click',download);
  $('check-connection').addEventListener('click',startStatus);$('retry-status').addEventListener('click',startStatus);$('back-install').addEventListener('click',()=>{clearInterval(timer);timer=null;go(2);});
  $('help-toggle').addEventListener('click',event=>{event.preventDefault();$('install-help-panel').hidden=!$('install-help-panel').hidden;if(!$('install-help-panel').hidden)$('install-help-panel').scrollIntoView({behavior:'smooth',block:'start'});});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&stage===3&&timer)poll();});window.addEventListener('pagehide',()=>clearInterval(timer));
  $('os-label').textContent=windows?'Windows 电脑':'请在 Windows 电脑上安装';$('os-note').textContent=windows?'支持 Windows 10 / 11，安装时会进一步检查64位系统。':'这台设备可以查看引导，实际安装需要 Windows 公司电脑。';$('os-state').textContent=windows?'已识别':'需要换电脑';$('os-state').classList.toggle('good',windows);
  $('open-jira').href=new URL('/',location.href).href;
  if(demo){$('demo-label').hidden=false;$('back-console').hidden=false;$('back-console').href=document.documentElement.dataset.static==='true'?'index.html':'../relay-admin/';$('demo-download-note').hidden=false;$('download-message').textContent='演示：安装文件准备好后，会看到以下步骤';$('open-jira').textContent='演示已完成';$('open-jira').removeAttribute('href');$('open-jira').setAttribute('aria-disabled','true');}
  icons();
  getInvitation().then(data=>{
    updateInvitation(data);$('invite-label').textContent=data.label;$('invite-expiry').textContent='邀请有效期至 '+date(data.expires);$('invite-state').textContent=data.status==='ready'?'邀请有效':'已使用';$('invite-state').classList.add('good');
    if(data.status==='used'){startStatus();return;}
    $('download-label').textContent=demo?'体验安装引导':windows?'下载专属安装文件':'请在 Windows 电脑上打开此链接';
    if(demo)$('download-note').textContent='点击体验完整流程，不会下载或安装软件。';
  }).catch(e=>{$('invite-label').textContent='暂时无法使用这份邀请';$('invite-expiry').textContent='请联系管理员';$('invite-state').textContent='未通过';$('download-label').textContent='需要有效的安装邀请';error(e.message);});
})();
