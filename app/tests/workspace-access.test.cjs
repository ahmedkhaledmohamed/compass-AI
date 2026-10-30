const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {WorkspaceAccess}=require('../dist-electron/workspace-access.js');
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'compass-access-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const file=path.join(root,'native','workspaces.json'),choices=[],calls=[],configs=new Map();
 let busy=false;
 const engine=async(endpoint,body)=>{
  calls.push({endpoint,body});
  if(endpoint==='/connections/readiness')return {status:'ok',busy};
  if(endpoint==='/init'){
   const dir=path.join(body.workspace_path,'.compass');fs.mkdirSync(dir,{recursive:true});
   fs.writeFileSync(path.join(dir,'compass.yaml'),'fixture original config',{flag:'wx'});
   configs.set(body.workspace_path,{name:body.name,description:body.description,sources:[]});return {status:'ok'};
  }
  if(endpoint==='/workspace/info')return {status:'ok',...configs.get(body.workspace_path)};
  if(endpoint==='/connect'){
   const config=configs.get(body.workspace_path);config.sources=config.sources.filter(s=>s.name!==body.name);
   config.sources.push({name:body.name,type:body.source_type,path:body.path,url:null,options:{},auth:null});return {status:'ok',accessible:true};
  }
  return {status:'ok',body};
 };
 const pick=async()=>choices.shift()??null;
 const access=new WorkspaceAccess(file,pick,engine);
 const folder=name=>{const p=path.join(root,name);fs.mkdirSync(p);return fs.realpathSync(p);};
 const create=async()=>{const folderPath=folder('product');choices.push(folderPath);const selection=await access.choose('workspace','directory');const record=await access.create(selection.id,'Product','Description');return {folderPath,record};};
 return {root,file,choices,calls,configs,access,folder,create,engine,pick,setBusy:value=>{busy=value;}};
}
test('native selections create durable opaque workspace IDs, not renderer paths',async t=>{
 const f=fixture(t),{folderPath,record}=await f.create();
 assert.match(record.id,/^ws_[a-f0-9]{32}$/);assert.equal(record.path,record.id);
 assert.ok(!JSON.stringify(f.access.list()).includes(folderPath));
 const restarted=new WorkspaceAccess(f.file,f.pick,f.engine);
 const result=await restarted.request('/tasks/list',{workspace_path:record.id});
 assert.equal(result.body.workspace_path,fs.realpathSync(folderPath));
 assert.equal(fs.statSync(f.file).mode&0o777,0o600);
 for(const input of [folderPath,'../../outside','ws_'+ 'a'.repeat(32),null])await assert.rejects(restarted.request('/tasks/list',{workspace_path:input}),/picker|registered|workspace/);
});
test('cancelled, forged and wrong-purpose selections never initialize a workspace',async t=>{
 const f=fixture(t);assert.equal(await f.access.choose('workspace','directory'),null);
 await assert.rejects(f.access.create('/tmp/arbitrary','Name',''),/selection|picker/);
 const source=f.folder('source');f.choices.push(source);const selection=await f.access.choose('source','directory');
 await assert.rejects(f.access.create(selection.id,'Name',''),/selection|picker/);
 assert.equal(f.calls.length,0);assert.equal(fs.existsSync(f.file),false);
});
test('existing workspace opens without config changes and keeps its stable ID',async t=>{
 const f=fixture(t),{folderPath,record}=await f.create();
 const original=fs.readFileSync(path.join(folderPath,'.compass','compass.yaml'));
 f.choices.push(folderPath);const reopened=await f.access.open();assert.equal(reopened.id,record.id);
 assert.deepEqual(fs.readFileSync(path.join(folderPath,'.compass','compass.yaml')),original);
 assert.equal(f.calls.filter(c=>c.endpoint==='/init').length,1);
});
test('workspace identity replacement and symlink storage fail before transport',async t=>{
 const f=fixture(t),{folderPath,record}=await f.create();
 fs.renameSync(folderPath,folderPath+'-old');fs.mkdirSync(folderPath);
 const before=f.calls.length;
 await assert.rejects(f.access.request('/tasks/list',{workspace_path:record.id}),/identity/);
 assert.equal(f.calls.length,before);
 const g=fixture(t),created=await g.create();const outside=g.folder('outside');
 fs.rmSync(path.join(created.folderPath,'.compass'),{recursive:true});fs.symlinkSync(outside,path.join(created.folderPath,'.compass'));
 await assert.rejects(g.access.request('/tasks/list',{workspace_path:created.record.id}),/symbolic/);
});
test('source picker approval is consumed, persisted, and checked before every import',async t=>{
 const f=fixture(t),{folderPath,record}=await f.create();const docs=f.folder('docs');fs.writeFileSync(path.join(docs,'strategy.md'),'Original evidence');
 f.choices.push(docs);const selected=await f.access.choose('source','directory');
 await f.access.request('/connect',{workspace_path:record.id,source_type:'docs',name:'Strategy',path:selected.id});
 const info=await f.access.request('/workspace/info',{workspace_path:record.id});assert.equal(info.sources[0].path,null);
 const restarted=new WorkspaceAccess(f.file,f.pick,f.engine);
 await restarted.request('/ingest',{workspace_path:record.id});
 assert.equal(f.calls.at(-1).endpoint,'/ingest');assert.equal(f.calls.at(-1).body.workspace_path,folderPath);
 await assert.rejects(restarted.request('/connect',{workspace_path:record.id,source_type:'docs',name:'Other',path:selected.id}),/selection/);
 fs.symlinkSync('/private/tmp',path.join(docs,'outside'));
 const before=f.calls.filter(c=>c.endpoint==='/ingest').length;
 await assert.rejects(restarted.request('/ingest',{workspace_path:record.id}),/symbolic/);
 assert.equal(f.calls.filter(c=>c.endpoint==='/ingest').length,before);
});
test('legacy and edited source configurations do not acquire import permission',async t=>{
 const f=fixture(t),{folderPath,record}=await f.create();const docs=f.folder('docs');
 f.configs.get(folderPath).sources.push({name:'Unapproved',type:'docs',path:docs});
 await assert.rejects(f.access.request('/ingest',{workspace_path:record.id}),/Select each source again/);
 assert.equal(f.calls.filter(c=>c.endpoint==='/ingest').length,0);
});
test('raw source paths, remote types, options and symlinks are rejected',async t=>{
 const f=fixture(t),{record}=await f.create();const docs=f.folder('docs');
 await assert.rejects(f.access.request('/connect',{workspace_path:record.id,source_type:'docs',name:'Docs',path:docs}),/selection|picker/);
 f.choices.push(docs);const selected=await f.access.choose('source','directory');
 for(const extra of [{source_type:'github'},{url:'https://example.com'},{options:{token:'fictional'}},{auth:{method:'oauth'}}])await assert.rejects(f.access.request('/connect',{workspace_path:record.id,source_type:'docs',name:'Docs',path:selected.id,...extra}),/supported local/);
 fs.symlinkSync('/private/tmp',path.join(docs,'link'));
 await assert.rejects(f.access.request('/connect',{workspace_path:record.id,source_type:'docs',name:'Docs',path:selected.id}),/symbolic/);
 assert.equal(f.calls.filter(c=>c.endpoint==='/connect').length,0);
});
test('forgetting revokes registration while preserving workspace files',async t=>{
 const f=fixture(t),{folderPath,record}=await f.create();await f.access.forget(record.id);
 assert.equal(f.access.list().activeWorkspaceId,null);assert.ok(fs.existsSync(path.join(folderPath,'.compass','compass.yaml')));
 await assert.rejects(f.access.request('/tasks/list',{workspace_path:record.id}),/registered/);
});
test('corrupt registry is preserved and cannot silently become an empty registry',async t=>{
 const f=fixture(t);await f.create();fs.writeFileSync(f.file,'corrupt original');
 assert.throws(()=>f.access.list(),/preserved/);assert.equal(fs.readFileSync(f.file,'utf8'),'corrupt original');
});

test('busy generation keeps its workspace registration available for cancellation',async t=>{
 const f=fixture(t),{record}=await f.create();f.setBusy(true);
 await assert.rejects(f.access.forget(record.id),/generation/);
 assert.equal(f.access.list().workspaces[0].id,record.id);
});

test('expired source selections cannot authorize a later import',async t=>{
 const f=fixture(t),{record}=await f.create();const source=f.folder('source');f.choices.push(source);
 const selected=await f.access.choose('source','directory');
 const future=Date.now()+16*60_000;t.mock.method(Date,'now',()=>future);
 await assert.rejects(f.access.request('/connect',{workspace_path:record.id,source_type:'docs',name:'Docs',path:selected.id}),/expired/);
 assert.equal(f.calls.filter(c=>c.endpoint==='/connect').length,0);
});
test('corrupt registry blocks creation before touching a newly selected workspace',async t=>{
 const f=fixture(t);await f.create();fs.writeFileSync(f.file,'original broken registry');const folder=f.folder('other');f.choices.push(folder);
 const selection=await f.access.choose('workspace','directory');const before=f.calls.length;
 await assert.rejects(f.access.create(selection.id,'Other',''),/preserved/);
 assert.equal(f.calls.length,before);assert.equal(fs.existsSync(path.join(folder,'.compass')),false);
});

test('legacy evidence symlinks cannot migrate outside data through an approved workspace',async t=>{
 const f=fixture(t),{folderPath,record}=await f.create();const dir=path.join(folderPath,'.compass','knowledge');fs.mkdirSync(dir);
 const outside=path.join(f.root,'outside.json');fs.writeFileSync(outside,'unapproved');fs.symlinkSync(outside,path.join(dir,'evidence_store.json'));
 const before=f.calls.length;
 await assert.rejects(f.access.request('/tasks/list',{workspace_path:record.id}),/symbolic/);
 assert.equal(f.calls.length,before);
});
