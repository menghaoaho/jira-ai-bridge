'use strict';
(() => {
  const {$,icons}=window.RelayUI;
  const demo=document.documentElement.dataset.mode==='demo';
  if(demo){
    $('welcome-demo').hidden=false;
    for(const id of ['welcome-jira','welcome-workbench']){$(id).removeAttribute('href');$(id).setAttribute('aria-disabled','true');}
  }else{
    $('welcome-workbench').href=new URL(location.protocol+'//'+location.hostname+':8090/').href;
  }
  $('open-invitation').addEventListener('submit',event=>{
    event.preventDefault();$('welcome-error').hidden=true;
    try{
      const link=new URL($('invitation-link').value.trim());
      if(demo)throw new Error('演示页无法办理真实安装，请打开管理员发来的专属链接。');
      if(link.origin!==location.origin||link.username||link.password||link.pathname!=='/relay-start/'||link.search||!/^#[A-Za-z0-9_-]{43}$/.test(link.hash))throw new Error('请粘贴当前站点管理员发来的完整邀请链接。');
      location.assign(link.href);
    }catch(e){$('welcome-error').textContent=e instanceof TypeError?'链接不完整，请重新复制管理员发来的完整邀请。':e.message;$('welcome-error').hidden=false;}
  });
  icons();
})();
