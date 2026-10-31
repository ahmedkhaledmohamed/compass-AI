const {test}=require('node:test');
const assert=require('node:assert/strict');
const {InferenceBroker}=require('../dist-electron/inference-broker.js');
const {infer,inferenceRequest,completionFor}=require('../dist-electron/native-inference.js');
const record={id:'a'.repeat(32),revision:1,provider:'openrouter',accessToken:'fictional-native-secret',model:'fixture/model',servingProvider:'fixture-serving',status:'connected',inferenceEnabled:true};
const binding={workspace_path:'/fictional/workspace',task_id:'fixture-task',expected_revision:1,kind:'brief',request_id:'fixture-request'};
const output={model:'actual-model',usage:{prompt_tokens:5,completion_tokens:8},choices:[{finish_reason:'stop',message:{content:'{"summary":"fictional"}'}}]};
const request=body=>({method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+body.token},body:JSON.stringify({binding,prompt:'Fictional task',system:'Use saved evidence',max_tokens:100,timeout_seconds:2})});

test('grant binds selected route, is single-use and never returns provider credentials',async t=>{
 let sent;const broker=new InferenceBroker(async(url,init)=>{sent={url,init};return Response.json(output);});t.after(()=>broker.close());
 const grant=await broker.issue(binding,record,async()=>record);
 assert.equal(JSON.stringify(grant).includes(record.accessToken),false);
 const response=await fetch(grant.url,request(grant));assert.equal(response.status,200);
 const result=await response.json();assert.equal(result.model,'actual-model');assert.equal(result.provider,'openrouter:fixture-serving');assert.equal(result.input_tokens,5);
 assert.equal(JSON.stringify(result).includes(record.accessToken),false);
 assert.equal(sent.url,'https://openrouter.ai/api/v1/chat/completions');
 assert.equal(sent.init.headers.Authorization,'Bearer '+record.accessToken);
 assert.deepEqual(JSON.parse(sent.init.body).provider.only,['fixture-serving']);assert.equal(JSON.parse(sent.init.body).provider.allow_fallbacks,false);
 assert.equal((await fetch(grant.url,request(grant))).status,403);assert.equal(broker.busy(),false);
});
test('foreign task scope, browser Origin and forged grants cannot trigger inference',async t=>{
 let calls=0;const broker=new InferenceBroker(async()=>{calls++;return Response.json(output);});t.after(()=>broker.close());
 const grant=await broker.issue(binding,record,async()=>record);
 const options=request(grant);options.headers.Origin='https://untrusted.invalid';assert.equal((await fetch(grant.url,options)).status,403);
 const wrong=request(grant);wrong.body=JSON.stringify({...JSON.parse(wrong.body),binding:{...binding,task_id:'other'}});
 assert.equal((await fetch(grant.url,wrong)).status,422);assert.equal(calls,0);
 assert.equal((await fetch(grant.url,request({...grant,token:'b'.repeat(64)}))).status,403);
 assert.equal((await fetch(grant.url,request(grant))).status,403);
});
test('reset revokes unused grants and aborts in-flight provider I/O',async t=>{
 let entered;const started=new Promise(resolve=>entered=resolve);let aborted=false;
 const broker=new InferenceBroker(async(_url,init)=>{entered();return new Promise((_resolve,reject)=>init.signal.addEventListener('abort',()=>{aborted=true;reject(Error('fictional abort'));}));});t.after(()=>broker.close());
 const grant=await broker.issue(binding,record,async()=>record);const response=fetch(grant.url,request(grant));await started;broker.reset();
 assert.equal((await response).status,422);assert.equal(aborted,true);assert.equal(broker.busy(),false);
 const unused=await broker.issue(binding,record,async()=>record);broker.reset();assert.equal((await fetch(unused.url,request(unused))).status,403);
});
test('closing the engine HTTP request aborts native provider I/O',async t=>{
 let entered,stopped;const started=new Promise(resolve=>entered=resolve),closed=new Promise(resolve=>stopped=resolve);
 const broker=new InferenceBroker(async(_url,init)=>{entered();return new Promise((_resolve,reject)=>init.signal.addEventListener('abort',()=>{stopped();reject(Error('abort'));}));});t.after(()=>broker.close());
 const grant=await broker.issue(binding,record,async()=>record),controller=new AbortController();
 const response=fetch(grant.url,{...request(grant),signal:controller.signal});await started;controller.abort();await assert.rejects(response);await closed;
});
test('provider errors are sanitized; missing usage is unknown and truncation retains metadata',async()=>{
 const signal=new AbortController().signal;
 await assert.rejects(infer(record,'p','s',100,signal,async()=>new Response('fictional-native-secret',{status:401})),error=>error.code==='authentication'&&!error.message.includes(record.accessToken));
 assert.equal(completionFor({...output,usage:{}},record).input_tokens,null);
 assert.throws(()=>completionFor({...output,choices:[{finish_reason:'length'}]},record),error=>error.code==='output_limit'&&error.completion.model==='actual-model');
 await assert.rejects(infer(record,'p','s',100,signal,async()=>new Response('x'.repeat(2_000_001))),error=>error.code==='output_limit');
});
test('all provider wires use fixed origins; ChatGPT requires terminal SSE and HF explicit route',async()=>{
 for(const [provider,url] of [['anthropic','https://api.anthropic.com/v1/messages'],['chatgpt','https://api.openai.com/v1/responses'],['huggingface','https://router.huggingface.co/v1/chat/completions']])assert.equal(inferenceRequest({...record,provider},'p','s',100).url,url);
 const hf=inferenceRequest({...record,provider:'huggingface'},'p','s',100);assert.equal(hf.body.model,'fixture/model:fixture-serving');
 const chat={...record,provider:'chatgpt',servingProvider:''};
 const result=await infer(chat,'p','s',100,new AbortController().signal,async()=>new Response('data: '+JSON.stringify({type:'response.completed',response:{model:'actual',status:'completed',output:[{content:[{type:'output_text',text:'{}'}]}]}})+'\n\n',{headers:{'Content-Type':'text/event-stream'}}));
 assert.equal(result.text,'{}');assert.equal(result.input_tokens,null);
 await assert.rejects(infer(chat,'p','s',100,new AbortController().signal,async()=>new Response('data: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}})),error=>error.code==='invalid_response');
});

test('native total deadline aborts provider I/O once with a typed timeout',async t=>{
 let attempts=0;
 const broker=new InferenceBroker(async(_url,init)=>{attempts++;return new Promise((_resolve,reject)=>init.signal.addEventListener('abort',()=>reject(Error('deadline'))));});t.after(()=>broker.close());
 const grant=await broker.issue(binding,record,async()=>record),options=request(grant);
 options.body=JSON.stringify({...JSON.parse(options.body),timeout_seconds:.02});
 const result=await fetch(grant.url,options);assert.equal(result.status,422);assert.equal((await result.json()).code,'timeout');assert.equal(attempts,1);assert.equal(broker.busy(),false);
});
