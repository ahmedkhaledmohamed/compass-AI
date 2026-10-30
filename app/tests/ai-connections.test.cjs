const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const crypto=require('node:crypto');
const {test}=require('node:test');
const {AIVault}=require('../dist-electron/ai-vault.js');
const {AIConnections}=require('../dist-electron/ai-connections.js');
const {authorize,validateIdentity,CONFIG}=require('../dist-electron/ai-auth.js');
const pair=crypto.generateKeyPairSync('rsa',{modulusLength:2048});
const jwk={...pair.publicKey.export({format:'jwk'}),kid:'fixture-key',alg:'RS256',use:'sig'};
function jwt(claims={}) {
 const header=Buffer.from(JSON.stringify({alg:'RS256',kid:'fixture-key'})).toString('base64url');
 const body=Buffer.from(JSON.stringify({iss:CONFIG.chatgpt.issuer,aud:'oaiapp_fixture',sub:'fictional-subject',name:'Fictional account',exp:Date.now()/1000+3600,...claims})).toString('base64url');
 return `${header}.${body}.${crypto.sign('RSA-SHA256',Buffer.from(`${header}.${body}`),pair.privateKey).toString('base64url')}`;
}
function fixture(t) {
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'compass-ai-test-'));
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const key=crypto.randomBytes(32);let available=true;
 const vault=new AIVault(path.join(dir,'ai.enc'),{
  available:()=>available,
  encrypt:value=>{const iv=crypto.randomBytes(12),cipher=crypto.createCipheriv('aes-256-gcm',key,iv);const body=Buffer.concat([cipher.update(value),cipher.final()]);return Buffer.concat([iv,cipher.getAuthTag(),body]);},
  decrypt:value=>{const decipher=crypto.createDecipheriv('aes-256-gcm',key,value.subarray(0,12));decipher.setAuthTag(value.subarray(12,28));return Buffer.concat([decipher.update(value.subarray(28)),decipher.final()]).toString();},
 });
 return {vault,file:path.join(dir,'ai.enc'),unavailable:()=>{available=false;}};
}
function seed(vault,extra={}) {
 const data=vault.read();
 const record={id:'a'.repeat(32),revision:1,provider:'chatgpt',label:'Fictional account',clientId:'oaiapp_fixture',issuer:CONFIG.chatgpt.issuer,subject:'fictional-subject',accessToken:'fixture-access',refreshToken:'fixture-refresh',scopes:['openid','profile','resource.invoke','chatgpt.tokens.use.direct'],expiresAt:Date.now()+3600000,inferenceEnabled:true,status:'connected',model:'fixture-model',servingProvider:'',...extra};
 data.connections.push(record);vault.write(data);return record;
}
const engine=async()=>({status:'ok',busy:false});

test('encrypted atomic vault persists a host and credentials without plaintext fallback',t=>{
 const {vault,file,unavailable}=fixture(t);const record=seed(vault);
 assert.equal(vault.read().connections[0].accessToken,'fixture-access');
 assert.equal(fs.readFileSync(file).includes(Buffer.from('fixture-access')),false);
 assert.equal(fs.statSync(file).mode&0o777,0o600);
 const host=vault.read().hostId;vault.write(vault.read());assert.equal(vault.read().hostId,host);
 unavailable();assert.throws(()=>vault.read(),/OS keychain/);assert.throws(()=>vault.update(record.id,1,()=>{}),/OS keychain/);
});
test('corrupt vault is preserved instead of replaced with an empty connection set',t=>{
 const {vault,file}=fixture(t);seed(vault);fs.writeFileSync(file,'broken ciphertext');
 assert.throws(()=>vault.read(),/not been overwritten/);assert.equal(fs.readFileSync(file,'utf8'),'broken ciphertext');
});
for(const change of [{iss:'https://wrong.invalid'},{aud:'wrong-client'},{exp:1},{nonce:'wrong'},{nbf:Date.now()/1000+1000},{aud:['oaiapp_fixture','other'],azp:'other'}]) {
 test('signed ID token rejects invalid claims '+Object.keys(change).join(','),async()=>{
  await assert.rejects(validateIdentity(jwt({nonce:'expected',...change}),'chatgpt','oaiapp_fixture','expected',async()=>({keys:[jwk]})),/identity could not be verified/);
 });
}
test('signature validation rejects an untrusted signing key',async()=>{
 await assert.rejects(validateIdentity(jwt({nonce:'expected'}),'chatgpt','oaiapp_fixture','expected',async()=>({keys:[]})),/identity could not be verified/);
});
test('OpenRouter uses S256 and checks callback state before exchanging one code',async()=>{
 let authorization;let exchanged=0;
 const json=async(url,init)=>{
  assert.equal(url,'https://openrouter.ai/api/v1/auth/keys');exchanged++;
  const body=JSON.parse(init.body);
  assert.equal(body.code,'fixture-code');assert.equal(body.code_challenge_method,'S256');
  assert.equal(crypto.createHash('sha256').update(body.code_verifier).digest('base64url'),authorization.searchParams.get('code_challenge'));
  return {key:'fixture-openrouter-key'};
 };
 const result=await authorize('openrouter','urn:uuid:fixture',async raw=>{
  authorization=new URL(raw);const callback=new URL(authorization.searchParams.get('callback_url'));
  callback.searchParams.set('code','fixture-code');callback.searchParams.set('state','wrong');
  assert.equal((await fetch(callback)).status,400);assert.equal(exchanged,0);
  callback.searchParams.set('state',authorization.searchParams.get('state'));
  assert.equal((await fetch(callback)).status,200);
 },new AbortController().signal,json);
 assert.equal(result.accessToken,'fixture-openrouter-key');assert.equal(exchanged,1);
});
test('ChatGPT dynamic registration validates nonce and keeps inference permission separate',async()=>{
 let authorization;
 const json=async(url,init)=>{
  if(url===CONFIG.chatgpt.jwks)return {keys:[jwk]};
  assert.equal(url,CONFIG.chatgpt.token);const form=new URLSearchParams(init.body);
  assert.equal(form.get('client_id'),'oaiapp_fixture');assert.equal(form.get('resource'),'https://api.openai.com/v1');
  return {id_token:jwt({nonce:authorization.searchParams.get('nonce')}),access_token:'identity-only-token',token_type:'Bearer',scope:'openid profile',expires_in:3600};
 };
 const result=await authorize('chatgpt','urn:uuid:fixture',async raw=>{
  authorization=new URL(raw);assert.equal(authorization.searchParams.get('client_id'),'dynamic_agent_client');assert.equal(authorization.searchParams.get('agent_name_hint'),'Compass');
  const callback=new URL(authorization.searchParams.get('redirect_uri'));callback.searchParams.set('state',authorization.searchParams.get('state'));callback.searchParams.set('code','fixture-code');callback.searchParams.set('client_id','oaiapp_fixture');await fetch(callback);
 },new AbortController().signal,json);
 assert.equal(result.subject,'fictional-subject');assert.equal(result.inferenceEnabled,false);
});
test('returning ChatGPT registration does not accept a different callback client ID',async()=>{
 const previous={clientId:'oaiapp_original',subject:'fictional-subject',issuer:CONFIG.chatgpt.issuer};
 await assert.rejects(authorize('chatgpt','urn:uuid:fixture',async raw=>{
  const url=new URL(raw);assert.equal(url.searchParams.get('client_id'),'oaiapp_original');assert.equal(url.searchParams.has('agent_name_hint'),false);
  const callback=new URL(url.searchParams.get('redirect_uri'));callback.searchParams.set('state',url.searchParams.get('state'));callback.searchParams.set('code','fixture');callback.searchParams.set('client_id','wrong-client');await fetch(callback);
 },new AbortController().signal,async()=>{throw Error('Must not exchange');},previous),/unexpected client registration/);
});
test('native sign-in cancellation closes its loopback listener',async()=>{
 const controller=new AbortController();let ready;const opened=new Promise(resolve=>{ready=resolve;});let callback;
 const operation=authorize('openrouter','urn:uuid:fixture',async raw=>{callback=new URL(raw).searchParams.get('callback_url');ready();},controller.signal,async()=>{throw Error('No exchange');});
 await opened;controller.abort();await assert.rejects(operation,/cancelled/);await assert.rejects(fetch(callback));
});
test('saved keys are returned only as connection metadata',async t=>{
 const {vault}=fixture(t);const calls=[];
 const service=new AIConnections(vault,async()=>{},async(...args)=>{calls.push(args);return {status:'ok',busy:false};},async url=>url.endsWith('/models')?{data:[{id:'fixture-model',display_name:'Fixture'}]}:{});
 const list=await service.saveKey('anthropic','fixture-api-key');
 assert.equal(JSON.stringify(list).includes('fixture-api-key'),false);
 await service.activate(list[0].id,'fixture-model','');
 assert.equal(service.status().active.id,list[0].id);
 assert.equal(calls.some(call=>call[0]==='/configure'),false);
 assert.equal(JSON.stringify(calls).includes('fixture-api-key'),false);
 assert.equal(JSON.stringify(service.status()).includes('fixture-api-key'),false);
});
test('concurrent refreshes rotate one token set and never return tokens to the renderer',async t=>{
 const {vault}=fixture(t);const record=seed(vault,{expiresAt:1});let refreshes=0;
 const service=new AIConnections(vault,async()=>{},engine,async url=>{
  if(url===CONFIG.chatgpt.token){refreshes++;await new Promise(resolve=>setTimeout(resolve,20));return {access_token:'replacement-access',refresh_token:'replacement-refresh',scope:record.scopes.join(' '),token_type:'Bearer',expires_in:3600};}
  return {models:[{slug:'fixture-model',display_name:'Fixture',visibility:'list'}]};
 });
 const [first,second]=await Promise.all([service.models(record.id),service.models(record.id)]);
 assert.equal(refreshes,1);assert.deepEqual(first,second);assert.equal(vault.read().connections[0].refreshToken,'replacement-refresh');
 assert.equal(JSON.stringify(service.list()).includes('replacement-access'),false);
});
test('disconnect waits for an in-flight refresh then clears its replacement token',async t=>{
 const {vault}=fixture(t);const record=seed(vault,{provider:'huggingface',issuer:CONFIG.huggingface.issuer,expiresAt:1,scopes:['openid','profile','inference-api']});
 let release,started;const began=new Promise(resolve=>{started=resolve;});
 const service=new AIConnections(vault,async()=>{},engine,async url=>{
  if(url===CONFIG.huggingface.token){started();return new Promise(resolve=>{release=()=>resolve({access_token:'rotated-access',refresh_token:'rotated-refresh',scope:record.scopes.join(' '),expires_in:3600});});}
  return {data:[]};
 });
 const models=service.models(record.id);await began;const disconnect=service.disconnect(record.id);release();await Promise.all([models,disconnect]);
 const saved=vault.read().connections[0];assert.equal(saved.status,'signed_out');assert.equal(saved.accessToken,'');assert.equal(saved.refreshToken,undefined);
});
test('active account changes are blocked during generation and cannot fall back after sign-out',async t=>{
 const {vault}=fixture(t);const record=seed(vault);const data=vault.read();data.active=record.id;data.managed=true;vault.write(data);
 const service=new AIConnections(vault,async()=>{},async()=>({status:'ok',busy:true}),async()=>({}));
 await assert.rejects(service.disconnect(record.id),/Finish or cancel/);assert.equal(vault.read().connections[0].accessToken,'fixture-access');
});
test('old CAS revisions cannot resurrect a signed-out connection',t=>{
 const {vault}=fixture(t);const record=seed(vault);vault.update(record.id,record.revision,item=>{item.accessToken='';item.status='signed_out';});
 assert.throws(()=>vault.update(record.id,record.revision,item=>{item.accessToken='late-token';}),/changed/);assert.equal(vault.read().connections[0].accessToken,'');
});
test('OpenRouter reauthorization adds a distinct connection and leaves the active account unchanged',async t=>{
 const {vault}=fixture(t);const original=seed(vault,{provider:'openrouter',clientId:undefined,issuer:undefined,subject:undefined});const data=vault.read();data.active=original.id;data.managed=true;vault.write(data);
 const service=new AIConnections(vault,async raw=>{const authorization=new URL(raw);const callback=new URL(authorization.searchParams.get('callback_url'));callback.searchParams.set('code','fixture');callback.searchParams.set('state',authorization.searchParams.get('state'));await fetch(callback);},engine,async()=>({key:'new-fixture-key'}));
 await service.signIn('openrouter',original.id);
 assert.equal(service.list().length,2);assert.equal(service.status().active.id,original.id);assert.equal(vault.read().connections[0].accessToken,'fixture-access');
});
test('forgetting removes only signed-out metadata, not a live credential',async t=>{
 const {vault}=fixture(t);const record=seed(vault,{provider:'anthropic'});const service=new AIConnections(vault,async()=>{},engine,async()=>({}));
 assert.throws(()=>service.forget(record.id),/Sign out/);await service.disconnect(record.id);assert.deepEqual(service.forget(record.id),[]);
});
test('encoded and query-bearing endpoints cannot bypass native configuration ownership',()=>{
 const {engineOperation}=require('../dist-electron/ai-connections.js');
 for(const endpoint of ['/configure','/configure/','/%63onfigure?ignored=true','/connections/clear/','/x/../configure'])assert.equal(engineOperation(endpoint),'native-only');
 for(const endpoint of ['/tasks/jobs/start?ignored=true','/tasks/generate/','/tasks%2Fjobs%2Fstart'])assert.equal(engineOperation(endpoint),'generation');
 assert.throws(()=>engineOperation({}),/Invalid/);
});
test('temporary refresh failures preserve credentials for an explicit retry',async t=>{
 const {vault}=fixture(t);const record=seed(vault,{expiresAt:1});const {ConnectionHTTPError}=require('../dist-electron/ai-auth.js');
 const service=new AIConnections(vault,async()=>{},engine,async()=>{throw new ConnectionHTTPError(true,'Network unavailable');});
 await assert.rejects(service.models(record.id),/Try again/);
 const saved=vault.read().connections[0];assert.equal(saved.status,'connected');assert.equal(saved.refreshToken,'fixture-refresh');
});
test('refresh identity tokens cannot change the original OIDC nonce',async t=>{
 const {vault}=fixture(t);const record=seed(vault,{expiresAt:1,oidcNonce:'original'});
 const service=new AIConnections(vault,async()=>{},engine,async url=>url===CONFIG.chatgpt.jwks?{keys:[jwk]}:{id_token:jwt({nonce:'wrong'}),access_token:'replacement',refresh_token:'replacement-refresh',scope:record.scopes.join(' '),expires_in:3600});
 await assert.rejects(service.models(record.id),/Sign in again/);assert.equal(vault.read().connections[0].status,'reconnect');
});
test('a refreshed grant without inference permission cannot send a model request',async t=>{
 const {vault}=fixture(t);const record=seed(vault,{expiresAt:1});let catalogs=0;
 const service=new AIConnections(vault,async()=>{},engine,async url=>{
  if(url===CONFIG.chatgpt.token)return {access_token:'identity-only',refresh_token:'rotated-refresh',scope:'openid profile',expires_in:3600};
  catalogs++;return {models:[]};
 });
 await assert.rejects(service.models(record.id),/authorize inference/);assert.equal(catalogs,0);assert.equal(vault.read().connections[0].inferenceEnabled,false);
});
test('selection is held until job submission captures the active connection',async t=>{
 const {vault}=fixture(t);const record=seed(vault);const data=vault.read();data.active=record.id;data.managed=true;vault.write(data);
 let started,acknowledge;const entered=new Promise(resolve=>{started=resolve;});let busy=false;
 const service=new AIConnections(vault,async()=>{},async endpoint=>{
  if(endpoint==='/tasks/jobs/start'){started();return new Promise(resolve=>{acknowledge=()=>{busy=true;resolve({status:'ok',job:{id:'fixture-job',state:'queued'}});};});}
  return {status:'ok',busy};
 },async()=>({}));
 const run=service.runGeneration('/tasks/jobs/start',{workspace_path:'/fictional/workspace',task_id:'fixture-task',expected_revision:1,kind:'brief',request_id:'fixture-request'});await entered;const disconnect=service.disconnect(record.id);
 await new Promise(resolve=>setImmediate(resolve));assert.equal(vault.read().connections[0].accessToken,'fixture-access');
 acknowledge();await run;await assert.rejects(disconnect,/Finish or cancel/);assert.equal(service.status().active.id,record.id);
});

test('generation sends an ephemeral grant instead of credentials and reuses a live idempotent submission',async t=>{
 const {vault}=fixture(t);const record=seed(vault);const data=vault.read();data.active=record.id;data.managed=true;vault.write(data);
 const calls=[];let state='running';
 const service=new AIConnections(vault,async()=>{},async(endpoint,body)=>{calls.push({endpoint,body});return {status:'ok',busy:false,job:{id:'native-job',state}};},async()=>({}));t.after(()=>service.close());
 const body={workspace_path:'/fictional/workspace',task_id:'task',expected_revision:1,kind:'brief',request_id:'one-request'};
 await service.runGeneration('/tasks/jobs/start',body);
 const sent=calls.find(call=>call.endpoint==='/tasks/jobs/start').body;
 assert.equal(sent.native_inference.connection.id,record.id);assert.match(sent.native_inference.token,/^[a-f0-9]{64}$/);
 assert.equal(JSON.stringify(calls).includes('fixture-access'),false);assert.equal(JSON.stringify(calls).includes('fixture-refresh'),false);
 await service.runGeneration('/tasks/jobs/start',body);assert.equal(calls.filter(call=>call.endpoint==='/tasks/jobs/start').length,1);
 await assert.rejects(service.runGeneration('/tasks/jobs/start',{...body,expected_revision:2}),/already used/);
 await assert.rejects(service.disconnect(record.id),/Finish or cancel/);
 state='cancelled';await service.observeGeneration('/tasks/jobs/cancel',{workspace_path:body.workspace_path,task_id:body.task_id,job_id:'native-job'});
 await service.disconnect(record.id);assert.equal(vault.read().connections[0].accessToken,'');
});
