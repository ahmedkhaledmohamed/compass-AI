/** Private loopback capabilities authorize one task request, never general HTTP. */
import http from "http";
import {randomBytes} from "crypto";
import type {AIConnection} from "./ai-vault";
import {infer,InferenceError} from "./native-inference";
export interface Binding {workspace_path:string;task_id:string;expected_revision:number;kind:"brief"|"handoff";request_id:string;}
export function bindingFor(body:any):Binding {
 if(!body||typeof body!=="object"||body.native_inference!==undefined)throw new Error("Invalid native generation request.");
 const {workspace_path,task_id,expected_revision,kind,request_id}=body;
 if(typeof workspace_path!=="string"||!workspace_path||workspace_path.length>4096||typeof task_id!=="string"||!task_id||task_id.length>128||!Number.isSafeInteger(expected_revision)||expected_revision<1||!["brief","handoff"].includes(kind)||typeof request_id!=="string"||!request_id||request_id.length>128)throw new Error("Invalid native generation request.");
 return {workspace_path,task_id,expected_revision,kind,request_id};
}
interface Grant {binding:Binding;record:AIConnection;credential:()=>Promise<AIConnection>;expires:number;timer:NodeJS.Timeout;controller?:AbortController;}
export class InferenceBroker {
 private server?:http.Server;
 private starting?:Promise<void>;
 private url="";
 private grants=new Map<string,Grant>();
 constructor(private fetcher:typeof fetch=fetch){}
 private async start(){
  if(this.server)return;
  if(this.starting)return this.starting;
  this.starting=new Promise<void>((resolve,reject)=>{
   const server=http.createServer((req,res)=>{void this.handle(req,res);});
   server.requestTimeout=10_000;server.headersTimeout=5_000;server.maxHeadersCount=16;
   server.on("clientError",(_error,socket)=>socket.destroy());
   server.once("error",reject);
   server.listen(0,"127.0.0.1",()=>{const address=server.address();if(!address||typeof address==="string")return reject(new Error("Native inference unavailable."));this.server=server;this.url=`http://127.0.0.1:${address.port}/complete`;server.unref();resolve();});
  });
  try{await this.starting;}finally{this.starting=undefined;}
 }
 async issue(body:unknown,record:AIConnection,credential:()=>Promise<AIConnection>){
  const binding=bindingFor(body);await this.start();
  if(this.grants.size>=8)throw new Error("The native inference queue is full. Wait for a request to finish.");
  const token=randomBytes(32).toString("hex");
  const timer=setTimeout(()=>this.revoke(token),300_000);timer.unref();
  this.grants.set(token,{binding,record:{...record},credential,expires:Date.now()+300_000,timer});
  return {url:this.url,token,connection:{id:record.id,revision:record.revision,provider:record.provider,model:record.model,serving_provider:record.servingProvider}};
 }
 revoke(token:string){const grant=this.grants.get(token);if(grant){clearTimeout(grant.timer);grant.controller?.abort();this.grants.delete(token);}}
 reset(){for(const token of this.grants.keys())this.revoke(token);}
 has(token:string){return this.grants.has(token);}
 busy(){return this.grants.size>0;}
 async close(){this.reset();const server=this.server;this.server=undefined;if(server){server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}}
 private async handle(req:http.IncomingMessage,res:http.ServerResponse){
  const send=(status:number,value:object)=>{if(!res.destroyed){res.writeHead(status,{"Content-Type":"application/json","Cache-Control":"no-store"});res.end(JSON.stringify(value));}};
  if(req.method!=="POST"||req.url!=="/complete"||req.headers.origin!==undefined){send(403,{code:"permission"});return;}
  const token=req.headers.authorization?.match(/^Bearer ([a-f0-9]{64})$/)?.[1];
  const grant=token?this.grants.get(token):undefined;
  if(!token||!grant||grant.controller||grant.expires<=Date.now()){send(403,{code:"permission"});return;}
  // Consume before reading any body. A malformed or interrupted call cannot replay.
  const controller=new AbortController();grant.controller=controller;
  res.on("close",()=>controller.abort());
  let timer:NodeJS.Timeout|undefined;
  try{
   const chunks:Buffer[]=[];let size=0;
   for await(const chunk of req){size+=chunk.length;if(size>300_000)throw new InferenceError("invalid_request");chunks.push(chunk);}
   const data=JSON.parse(Buffer.concat(chunks).toString("utf8"));
   const binding=bindingFor(data.binding);
   if(JSON.stringify(binding)!==JSON.stringify(grant.binding)||typeof data.prompt!=="string"||typeof data.system!=="string"||[...data.prompt].length+[...data.system].length>60_000||!Number.isSafeInteger(data.max_tokens)||data.max_tokens<1||data.max_tokens>16_384||typeof data.timeout_seconds!=="number"||data.timeout_seconds<=0||data.timeout_seconds>300)throw new InferenceError("permission");
   timer=setTimeout(()=>controller.abort("timeout"),Math.min(data.timeout_seconds*1000,grant.expires-Date.now()));timer.unref();
   const record=await grant.credential();
   if(controller.signal.aborted)throw new InferenceError(controller.signal.reason==="timeout"?"timeout":"cancelled");
   if(record.id!==grant.record.id||record.subject!==grant.record.subject||record.issuer!==grant.record.issuer||record.clientId!==grant.record.clientId||record.provider!==grant.record.provider||record.model!==grant.record.model||record.servingProvider!==grant.record.servingProvider||record.status!=="connected"||!record.inferenceEnabled)throw new InferenceError("authentication");
   const result=await infer(record,data.prompt,data.system,data.max_tokens,controller.signal,this.fetcher);
   if(controller.signal.aborted)throw new InferenceError(controller.signal.reason==="timeout"?"timeout":"cancelled");
   send(200,result);
  }catch(cause){const error=cause instanceof InferenceError?cause:new InferenceError("invalid_request");send(422,{code:error.code,...(error.completion?{completion:error.completion}:{})});}
  finally{if(timer)clearTimeout(timer);this.revoke(token);}
 }
}
