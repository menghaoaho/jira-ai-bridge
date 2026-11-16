'use strict';
const $ = id => document.getElementById(id);
let busy = false;
const timeText = value => value ? new Date(value * 1000).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '尚未连接';
function note(text, error = false) { $('notice').textContent = text; $('notice').className = error ? 'error' : ''; }
function element(tag, text, className) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e; }
async function api(path, body) {
  const response = await fetch('/relay-admin/api/' + path, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: body === undefined ? {} : {'Content-Type': 'application/json', 'X-Jira-Relay-Admin': '1'},
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '操作未完成，请稍后重试');
  return data;
}
function showCommand(data) {
  $('command').value = data.command;
  $('command-caption').textContent = `${data.label} · 北京时间 ${timeText(data.expires)} 前有效。完整命令仅在生成时展示。`;
  $('command-result').hidden = false;
  $('command-result').scrollIntoView({behavior: 'smooth', block: 'nearest'});
}
async function action(button, work) {
  if (busy) return;
  busy = true; button.disabled = true;
  try { await work(); await refresh(); } catch (e) { note(e.message || '网络连接失败，请稍后重试', true); }
  finally { busy = false; button.disabled = false; }
}
async function confirmDisable(device) {
  const dialog = $('confirm'); $('confirm-text').textContent = device.label;
  dialog.returnValue = '';
  const choice = new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue), {once: true}));
  dialog.showModal();
  return (await choice) === 'disable';
}
function renderDevice(device) {
  const card = element('article', undefined, 'device');
  const head = element('div', undefined, 'section-head');
  head.append(element('h3', device.label), element('span', device.status, 'badge ' + (device.status === '正常转发' ? 'ok' : device.status === '内网不可达' ? 'bad' : '')));
  card.append(head, element('p', `${device.hostname || '电脑名待读取'} · 版本 ${device.version || '待确认'}`),
    element('p', '最近连接：' + timeText(device.last_seen)), element('p', device.health_reason));
  const button = element('button', device.enabled ? '停用这台电脑' : '重新生成安装命令', 'quiet');
  button.addEventListener('click', async () => {
    if (device.enabled && !(await confirmDisable(device))) return;
    await action(button, async () => {
      if (device.enabled) { await api('disable-device', {id: device.id}); note('已停用。此电脑将在60秒内退出转发，其他电脑继续工作。'); }
      else { showCommand(await api('reauthorize', {id: device.id})); note('已生成新的安装授权，旧凭证仍保持失效。'); }
    });
  });
  card.append(button); return card;
}
async function refresh() {
  const report = await api('report');
  $('account').textContent = report.admin + ' · 管理员';
  $('device-count').textContent = report.devices.length;
  $('devices').replaceChildren(...report.devices.map(renderDevice));
  if (!report.devices.length) $('devices').append(element('p', '还没有电脑接入。生成第一条安装命令后，就可以发给同事了。', 'empty'));
  $('commands').replaceChildren();
  for (const code of report.commands) {
    const row = element('tr'); row.append(element('td', code.label), element('td', code.status), element('td', timeText(code.expires)));
    const cell = element('td');
    if (code.status === '待安装') {
      const button = element('button', '撤销命令', 'quiet');
      button.addEventListener('click', () => action(button, async () => { await api('revoke-command', {id: code.id}); note('安装命令已撤销。'); }));
      cell.append(button);
    } else cell.textContent = '—';
    row.append(cell); $('commands').append(row);
  }
  if (!report.commands.length) { const row = element('tr'); const cell = element('td', '尚未生成安装命令'); cell.colSpan = 4; row.append(cell); $('commands').append(row); }
  $('updated').textContent = '状态更新于 ' + timeText(report.now) + '（北京时间）';
}
$('create').addEventListener('submit', event => {
  event.preventDefault(); const form = event.currentTarget;
  action(form.querySelector('button'), async () => {
    const body = new FormData(form);
    showCommand(await api('commands', {label: body.get('label'), hours: Number(body.get('hours'))}));
    note('安装命令已生成，请复制后私下发给对应同事。');
  });
});
$('copy-command').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('command').value); note('完整命令已复制。'); }
  catch { $('command').focus(); $('command').select(); note('请按 Ctrl+C（Mac 为 ⌘C）复制选中的完整命令。'); }
});
$('clear-command').addEventListener('click', () => { $('command').value = ''; $('command-result').hidden = true; });
$('refresh').addEventListener('click', () => refresh().catch(e => note(e.message, true)));
document.addEventListener('visibilitychange', () => { if (!document.hidden && !busy) refresh().catch(e => note(e.message, true)); });
setInterval(() => { if (!document.hidden && !busy) refresh().catch(e => note(e.message, true)); }, 15000);
refresh().catch(e => note(e.message, true));
