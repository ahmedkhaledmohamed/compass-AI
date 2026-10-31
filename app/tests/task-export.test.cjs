const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {saveTaskExport}=require('../dist-electron/task-export.js');
const request={workspace_path:'ws_'+'a'.repeat(32),task_id:'b'.repeat(32),expected_revision:2,kind:'brief'};
function fixture(t){const directory=fs.mkdtempSync(path.join(os.tmpdir(),'compass-export-test-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));return {directory,destination:path.join(directory,'reviewed.md')};}
const result={status:'ok',filename:`compass-brief-${request.task_id}.md`,markdown:'# Corrected brief\n\nFictional quoted evidence.\n'};

test('native export writes canonical reviewed Markdown privately and returns no local path',async t=>{
 const {destination}=fixture(t);let calls=0;
 const access={request:async(endpoint,body)=>{calls++;assert.equal(endpoint,'/tasks/export');assert.deepEqual(body,request);return result;}};
 const response=await saveTaskExport(access,request,async name=>{assert.equal(name,result.filename);return destination;});
 assert.equal(calls,2);assert.equal(fs.readFileSync(destination,'utf8'),result.markdown);assert.equal(fs.statSync(destination).mode&0o777,0o600);
 assert.deepEqual(response,{status:'saved',filename:'reviewed.md'});assert.equal(JSON.stringify(response).includes(destination),false);
});
test('cancelled destination and forged workspace/task requests cannot write a file',async t=>{
 const {directory}=fixture(t);let picks=0;
 assert.deepEqual(await saveTaskExport({request:async()=>result},request,async()=>null),{status:'cancelled'});
 for(const invalid of [{...request,workspace_path:'/fictional/raw-path'},{...request,task_id:'../outside'},{...request,markdown:'forged'}])await assert.rejects(saveTaskExport({request:async()=>{throw Error('Must not call engine');}},invalid,async()=>{picks++;return null;}),/saved task revision/);
 assert.equal(picks,0);assert.deepEqual(fs.readdirSync(directory),[]);
});
test('changes while the save dialog is open preserve an existing destination',async t=>{
 const {destination,directory}=fixture(t);fs.writeFileSync(destination,'Existing export');let calls=0;
 await assert.rejects(saveTaskExport({request:async()=>++calls===1?result:{status:'error',detail:'Task changed. Reload before exporting.'}},request,async()=>destination),/Task changed/);
 assert.equal(fs.readFileSync(destination,'utf8'),'Existing export');assert.deepEqual(fs.readdirSync(directory),['reviewed.md']);
});
test('non-Markdown destinations and invalid engine payloads fail without replacing data',async t=>{
 const {directory}=fixture(t);const database=path.join(directory,'tasks.sqlite3');fs.writeFileSync(database,'Sentinel database');
 await assert.rejects(saveTaskExport({request:async()=>result},request,async()=>database),/Markdown/);
 await assert.rejects(saveTaskExport({request:async()=>({...result,filename:'../../outside.md'})},request,async()=>database),/invalid handoff/);
 assert.equal(fs.readFileSync(database,'utf8'),'Sentinel database');
});
test('replacing a user-selected symlink does not overwrite its external target',async t=>{
 const {directory,destination}=fixture(t);const outside=path.join(directory,'outside.md');fs.writeFileSync(outside,'Outside sentinel');fs.symlinkSync(outside,destination);
 await saveTaskExport({request:async()=>result},request,async()=>destination);
 assert.equal(fs.readFileSync(outside,'utf8'),'Outside sentinel');assert.equal(fs.lstatSync(destination).isSymbolicLink(),false);
});

test('failed atomic replacement preserves the existing export and removes its temporary file',async t=>{
 const {directory,destination}=fixture(t);fs.writeFileSync(destination,'Previous reviewed export');
 const promises=require('node:fs/promises'),rename=promises.rename;
 promises.rename=async()=>{throw Error('Fictional disk failure');};
 try{await assert.rejects(saveTaskExport({request:async()=>result},request,async()=>destination),/disk failure/);}
 finally{promises.rename=rename;}
 assert.equal(fs.readFileSync(destination,'utf8'),'Previous reviewed export');assert.deepEqual(fs.readdirSync(directory),['reviewed.md']);
});
test('renderer generic engine bridge cannot invoke native-only reviewed export',()=>{
 const {requireReleaseEndpoint}=require('../dist-electron/release-policy.js');
 assert.throws(()=>requireReleaseEndpoint('/tasks/export'),/not available/);
});
