const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'web/welcome.js'),'utf8');
function page(link,mode='live'){
  const nodes=new Map();let submit;const navigations=[];
  const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:true,removeAttribute(){},setAttribute(){},addEventListener(_event,fn){submit=fn;}});return nodes.get(id);};
  get('invitation-link').value=link;
  vm.runInNewContext(source,{URL,TypeError,document:{documentElement:{dataset:{mode}}},location:{protocol:'https:',hostname:'relay.example',origin:'https://relay.example',assign:link=>navigations.push(link)},window:{RelayUI:{$:get,icons(){}}}});
  submit({preventDefault(){}});return {nodes,navigations};
}
test('a complete invite opens the installation page while keeping authorization in the fragment',()=>{
  const link='https://relay.example/relay-start/#'+'a'.repeat(43);
  assert.deepEqual(page(link).navigations,[link]);
});
test('foreign origins, credentials, query tokens and malformed links never navigate',()=>{
  for(const link of ['https://evil.example/relay-start/#'+'a'.repeat(43),'https://name:password@relay.example/relay-start/#'+'a'.repeat(43),'https://relay.example/relay-start/?code=anything','https://relay.example/other/#'+'a'.repeat(43),'not a link']){
    const result=page(link);assert.deepEqual(result.navigations,[]);assert.equal(result.nodes.get('welcome-error').hidden,false);
  }
});
test('the static demo cannot turn an invitation into a live install',()=>{
  const result=page('https://relay.example/relay-start/#'+'a'.repeat(43),'demo');
  assert.deepEqual(result.navigations,[]);assert.match(result.nodes.get('welcome-error').textContent,/演示页/);
});

async function adminPage({mode='live',isStatic=false,response}={}){
  const html=fs.readFileSync(path.join(__dirname,'web/index.html'),'utf8');
  const source=fs.readFileSync(path.join(__dirname,'web/app.js'),'utf8');
  const node=()=>({value:'',hidden:false,dataset:{},classList:{toggle(){}},addEventListener(){},setAttribute(){},removeAttribute(){},append(){},replaceChildren(){}});
  const nodes=new Map([...html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)].map(match=>[match[1],{...node(),hidden:/\bhidden\b/.test(match[0])}]));
  const calls=[];
  const context={URL,structuredClone,setTimeout:resolve=>resolve(),setInterval(){},
    location:{hash:'',hostname:'relay.example',href:'https://relay.example:8090/relay-admin/'},
    document:{documentElement:{dataset:{mode,static:String(isStatic)}},hidden:false,addEventListener(){},querySelector:()=>node(),querySelectorAll:()=>[],createTextNode:node},
    window:{addEventListener(){},RelayUI:{$:id=>{assert(nodes.has(id),'Missing HTML element: '+id);return nodes.get(id);},icons(){},el:node,date:String,
      request:async route=>{calls.push(route);return response;}}}};
  vm.runInNewContext(source,context,{filename:'app.js'});
  await new Promise(resolve=>setImmediate(resolve));
  return {nodes,calls};
}
test('the admin coworker entry uses the public origin returned by its report',async()=>{
  const result=await adminPage({response:{devices:[],commands:[],welcome_url:'https://relay.example/relay/'}});
  assert.deepEqual(result.calls,['/relay-admin/api/report']);
  assert.equal(result.nodes.get('user-entry').href,'https://relay.example/relay/');
  assert.equal(result.nodes.get('user-entry').hidden,false);
});
test('explicit hosted and static admin demos keep their coworker entry destinations',async()=>{
  for(const isStatic of [false,true]){
    const result=await adminPage({mode:'demo',isStatic});
    assert.deepEqual(result.calls,[]);
    assert.equal(result.nodes.get('user-entry').href,isStatic?'welcome.html':'/relay/');
    assert.equal(result.nodes.get('user-entry').hidden,false);
  }
});
