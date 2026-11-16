const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, 'web/install.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, 'web/install.html'), 'utf8');
const key = 'jira-relay-install-invitation';
const code = 'a'.repeat(43);
const ready = {label:'Test invitation', expires:Date.now()/1000+3600, status:'ready', command:'powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand YQ==',agent_instructions:'PRIVATE TEST AGENT INSTRUCTIONS'};
const used = {...ready, status:'used', command:undefined, agent_instructions:undefined, connection:'离线'};
const settle = () => new Promise(resolve => setImmediate(resolve));

async function page({hash='#'+code, storage=new Map(), mode='live', response=ready, storageError=false, ignoreWrites=false}={}) {
  const nodes = new Map();
  for (const match of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
    const handlers = new Map();
    nodes.set(match[1], {hidden:/\bhidden\b/.test(match[0]), disabled:/\bdisabled\b/.test(match[0]), textContent:'',
      classList:{toggle(){},add(){}}, addEventListener:(name,fn)=>handlers.set(name,fn),
      fire:name=>handlers.get(name)?.({preventDefault(){}}), replaceChildren(){}, setAttribute(){},removeAttribute(){}});
  }
  const calls = [], downloads = [], savedUrls = [], copies = [];
  const location = {hash,pathname:'/relay-start/',search:'?view=install',href:'https://relay.example/relay-start/?view=install'+hash};
  const context = {URL,location,navigator:{userAgent:'Windows NT 10.0; Win64; x64'},
    document:{documentElement:{dataset:{mode}},hidden:false,addEventListener(){}},
    window:{addEventListener(){},RelayUI:{$:id=>{assert(nodes.has(id),'Missing HTML element: '+id);return nodes.get(id);},
      icons(){},icon(){return {firstChild:{}};},date:String,
      request:async(route,body)=>{calls.push({route,body});return typeof response==='function'?response():response;},
      copy:async field=>{copies.push(field.value);return true;},
      downloadLauncher:command=>downloads.push(command)}},
    sessionStorage:{getItem(name){if(storageError)throw Error('storage unavailable');return storage.get(name)??null;},
      setItem(name,value){if(storageError)throw Error('storage unavailable');if(!ignoreWrites)storage.set(name,value);},
      removeItem(name){if(storageError)throw Error('storage unavailable');storage.delete(name);}},
    history:{replaceState(_state,_title,url){savedUrls.push(url);location.hash='';}},setInterval(){return 1;},clearInterval(){}};
  Object.defineProperty(context,'localStorage',{get(){throw Error('Persistent storage must not be accessed');}});
  vm.runInNewContext(source,context,{filename:'install.js'});
  await settle();
  return {nodes,calls,downloads,savedUrls,copies,location,storage,click:async id=>{nodes.get(id).fire('click');await settle();}};
}

test('a live invitation survives refresh after the fragment is removed',async()=>{
  const first=await page();
  const saved=JSON.parse(first.storage.get(key));
  assert.equal(saved.code,code);
  assert(saved.expires>Date.now()&&saved.expires<=Date.now()+30*60*1000);
  assert.deepEqual(first.savedUrls,['/relay-start/?view=install']);
  const refreshed=await page({hash:'',storage:first.storage});
  assert.equal(refreshed.calls[0].body.code,code);
  assert.equal(refreshed.nodes.get('download').disabled,false);
  assert.equal(first.storage.get(key),JSON.stringify(saved),'refresh must not extend retention');
});

test('blocked or unsuccessful tab storage preserves the invitation fragment',async()=>{
  for(const options of [{storageError:true},{ignoreWrites:true}]) {
    const result=await page(options);
    assert.equal(result.location.hash,'#'+code);
    assert.equal(result.savedUrls.length,0);
    assert.equal(result.calls[0].body.code,code);
  }
});

test('an explicit invalid fragment rejects and clears an older saved invitation',async()=>{
  const storage=new Map([[key,JSON.stringify({code,expires:Date.now()+60000})]]);
  const invalid=await page({hash:'#invalid',storage});
  assert.equal(invalid.calls.length,0);
  assert.equal(invalid.nodes.get('download').disabled,true);
  assert.equal(storage.has(key),false);
  assert.equal((await page({hash:'',storage})).calls.length,0);
});

test('expired, malformed, or overlong-retention saved invitations are not restored',async()=>{
  for(const saved of ['{broken',JSON.stringify({code,expires:Date.now()-1}),JSON.stringify({code,expires:Date.now()+3600000}),JSON.stringify({code:'invalid',expires:Date.now()+60000})]) {
    const storage=new Map([[key,saved]]);
    const result=await page({hash:'',storage});
    assert.equal(result.calls.length,0);
    assert.equal(storage.has(key),false);
  }
});

test('demo mode never accesses invitation storage or downloads a launcher',async()=>{
  const result=await page({mode:'demo',storageError:true});
  assert.equal(result.savedUrls.length,0);
  assert.equal(result.calls.length,0);
  await result.click('download');
  assert.equal(result.nodes.get('download-stage').hidden,false);
  assert.equal(result.downloads.length,0);
});

test('used invitations offer original-file guidance and cannot download even after returning',async()=>{
  const result=await page({response:used});
  assert.equal(result.nodes.get('status-stage').hidden,false);
  assert.equal(result.nodes.get('used-invitation-note').hidden,false);
  for(const id of ['download','download-again']) {
    assert.equal(result.nodes.get(id).hidden,true);
    assert.equal(result.nodes.get(id).disabled,true);
  }
  await result.click('back-install');
  assert.equal(result.nodes.get('download-stage').hidden,false);
  assert.match(result.nodes.get('download-file-title').textContent,/原先下载/);
  assert.match(result.nodes.get('download-file-note').textContent,/管理员重新授权/);
  await result.click('download-again');
  await result.click('download');
  assert.deepEqual(result.downloads,[]);
  assert.match(html,/已安装电脑桌面运行“Jira转发-检查状态”/);
});

test('a ready invitation downloads once, then polling used status disables further downloads',async()=>{
  let current=ready;
  const result=await page({response:()=>current});
  await result.click('download');
  assert.deepEqual(result.downloads,[ready.command]);
  current=used;
  await result.click('check-connection');
  await result.click('back-install');
  await result.click('download-again');
  assert.deepEqual(result.downloads,[ready.command]);
  assert.equal(result.nodes.get('download-again').hidden,true);
});

test('a response without an installation command never reaches the launcher',async()=>{
  const result=await page({response:{...ready,command:undefined}});
  await result.click('download');
  await result.click('download-again');
  assert.equal(result.nodes.get('download').disabled,true);
  assert.deepEqual(result.downloads,[]);
});

test('agent instructions are copied only for a valid unused live invitation',async()=>{
  const result=await page();
  await result.click('copy-install-agent');
  assert.deepEqual(result.copies,[ready.agent_instructions]);
  for(const options of [{response:used},{mode:'demo'}]){
    const blocked=await page(options);await blocked.click('copy-install-agent');
    assert.deepEqual(blocked.copies,[]);
  }
});

test('a shared check result excludes the invitation, command and Agent instructions',async()=>{
  const result=await page({response:used});
  await result.click('copy-diagnostics');
  assert.equal(result.copies.length,1);
  for(const value of [code,ready.command,ready.agent_instructions])assert(!result.copies[0].includes(value));
  assert.match(result.copies[0],/Jira连接助手检查结果/);
});
