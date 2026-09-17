// Offline checks: no real accounts, provider requests, or environment files.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const folder = __dirname;
let scripts = 0;
for (const name of fs.readdirSync(folder)) {
  if (!/\.(html|js)$/.test(name)) continue;
  const text = fs.readFileSync(path.join(folder, name), 'utf8');
  const blocks = name.endsWith('.js') ? [text] : [...text.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(match => match[1]);
  for (const block of blocks) { new vm.Script(block, {filename: name}); scripts++; }
}
const overview = fs.readFileSync(path.join(folder, 'overview.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
function element() {
  return {textContent: '', children: [], addEventListener(){}, replaceChildren(...items){this.children=items;}, append(...items){this.children.push(...items);}};
}
async function scenario(responses) {
  const nodes = new Map();
  let redirect = '';
  const context = vm.createContext({
    document: {getElementById(id){if(!nodes.has(id)) nodes.set(id, element()); return nodes.get(id);}, createElement: element},
    fetch: async url => {
      const result = responses[url];
      if (result instanceof Error) throw result;
      return {status: result?.status || 200, ok: !result?.status || result.status === 200, json: async () => result};
    },
    location: {replace(url){redirect=url;}}
  });
  vm.runInContext(overview, context);
  await new Promise(resolve => setImmediate(resolve));
  return {nodes, redirect};
}
(async () => {
  const stats = {requests: 3, errors: 1, p95_latency_ms: 1500};
  let result = await scenario({'/api/chat/list': [{title:'<script>unsafe</script>',updated_at:'2026-09-16'}], '/api/live/list': [], '/api/providers/telemetry': stats});
  assert.equal(result.nodes.get('chats').textContent, 1);
  assert.equal(result.nodes.get('latency').textContent, '1.50s');
  assert.equal(result.nodes.get('recent').children[0].children[0].textContent, '<script>unsafe</script>');
  assert.equal(result.nodes.get('refresh').disabled, false);
  result = await scenario({'/api/chat/list': [], '/api/live/list': [], '/api/providers/telemetry': {requests:0,errors:0,p95_latency_ms:null}});
  assert.equal(result.nodes.get('latency').textContent, '—');
  assert.match(result.nodes.get('recent').children[0].textContent, /No saved conversations/);
  result = await scenario({'/api/chat/list': new Error('Offline')});
  assert.equal(result.nodes.get('status').textContent, 'Offline');
  assert.equal(result.nodes.get('chats').textContent, '—');
  assert.equal(result.nodes.get('refresh').disabled, false);
  result = await scenario({'/api/chat/list': {status:401}});
  assert.equal(result.redirect, '/login');
  console.log(`${scripts} script syntax checks and 4 overview behavior scenarios passed.`);
})().catch(error => {console.error(error); process.exitCode=1;});
