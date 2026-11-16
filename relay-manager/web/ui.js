'use strict';
(() => {
  const paths = {
    home:'<path d="m3 10 9-7 9 7M5 9v11h5v-7h4v7h5V9"/>',
    computer:'<rect x="4" y="4" width="16" height="12" rx="2"/><path d="M2 20h20M9 16v4m6-4v4"/>',
    link:'<path d="m10 14 4-4m-5-2 2-2a5 5 0 0 1 7 7l-2 2M8 9l-2 2a5 5 0 0 0 7 7l2-2"/>',
    help:'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4 2c-1.5 1-1.5 1.5-1.5 2M12 16h.01"/>',
    chevron:'<path d="m9 5 7 7-7 7"/>', arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
    plus:'<path d="M12 5v14M5 12h14"/>', alert:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4h.01"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    refresh:'<path d="M20 7v5h-5M4 17v-5h5M5.5 7a8 8 0 0 1 13-2L20 7M4 17l1.5 2a8 8 0 0 0 13-2"/>',
    wifi:'<path d="M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0m-11 4a6 6 0 0 1 8 0m-4 4h.01"/>',
    lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>', check:'<path d="m5 12 4 4L19 6"/>',
    copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  };
  const $ = id => document.getElementById(id);
  function icon(name) {
    const span = document.createElement('span'); span.dataset.icon = name; span.setAttribute('aria-hidden','true');
    span.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + (paths[name] || paths.help) + '</svg>';
    return span;
  }
  function icons(root = document) { root.querySelectorAll('[data-icon]').forEach(node => { if (!node.firstChild) node.append(icon(node.dataset.icon).firstChild); }); }
  function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
  function date(value, short = false) { if (!value) return '尚未连接'; return new Date(value*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,...(short?{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}:{})}); }
  async function request(path, body, admin = false) {
    const response = await fetch(path,{method:body===undefined?'GET':'POST',credentials:admin?'same-origin':'omit',cache:'no-store',referrerPolicy:'no-referrer',
      headers:body===undefined?{}:{'Content-Type':'application/json',...(admin?{'X-Jira-Relay-Admin':'1'}:{})},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
    let data; try { data=await response.json(); } catch { throw new Error('服务暂时不可用，请稍后重试。'); }
    if (!response.ok) throw new Error(data.detail || '操作未完成，请稍后重试。');
    return data;
  }
  async function copy(field) { try { await navigator.clipboard.writeText(field.value); return true; } catch { field.hidden=false;field.focus();field.select();return false; } }
  function downloadLauncher(command) {
    if (!/^powershell\.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand [A-Za-z0-9+/=]+$/.test(command)) throw new Error('安装信息无效，请联系管理员重新发送邀请。');
    const blob=new Blob(['@echo off\r\n'+command+'\r\n'],{type:'application/octet-stream'});
    const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='Jira-Connect-Setup.cmd';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  window.RelayUI = {$,icon,icons,el,date,request,copy,downloadLauncher};
})();
