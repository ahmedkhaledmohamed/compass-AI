/** One-attempt provider I/O. Credentials and provider authorization stay native. */
import type {AIConnection} from "./ai-vault";
export const MAX_RESPONSE_BYTES=2_000_000;
export class InferenceError extends Error {
 constructor(readonly code:string, readonly completion?:object){super(code);}
}
const failure=(status:number)=>new InferenceError(({401:"authentication",402:"quota",403:"permission",408:"timeout",429:"rate_limited",404:"unavailable"} as Record<number,string>)[status]||(status>=500?"unavailable":"invalid_request"));
function providerError(error:any){
 const code=error?.code??error?.type;
 if(typeof code==="number")return failure(code);
 return new InferenceError(({subscription_sharing_usage_limit_exceeded:"quota",insufficient_quota:"quota",rate_limit_exceeded:"rate_limited",model_not_found:"unavailable",invalid_api_key:"authentication",permission_denied:"permission"} as Record<string,string>)[code]||"invalid_response");
}
export function inferenceRequest(record:AIConnection,prompt:string,system:string,maxTokens:number){
 if(!record.accessToken||/[\r\n]/.test(record.accessToken))throw new InferenceError("authentication");
 if(!record.model||record.model.length>256||["auto","openrouter/auto"].includes(record.model.toLowerCase()))throw new InferenceError("invalid_request");
 const headers:Record<string,string>={"Content-Type":"application/json"};
 if(record.provider==="anthropic")return {url:"https://api.anthropic.com/v1/messages",headers:{...headers,"x-api-key":record.accessToken,"anthropic-version":"2023-06-01"},body:{model:record.model,max_tokens:maxTokens,system,messages:[{role:"user",content:prompt}]}};
 headers.Authorization=`Bearer ${record.accessToken}`;
 if(record.provider==="chatgpt")return {url:"https://api.openai.com/v1/responses",headers,body:{model:record.model,instructions:system,store:false,stream:true,input:[{role:"user",content:[{type:"input_text",text:prompt}]}]}};
 if(!/^[A-Za-z0-9][A-Za-z0-9_./-]{0,127}$/.test(record.servingProvider)||["auto","fastest","cheapest","preferred"].includes(record.servingProvider.toLowerCase()))throw new InferenceError("invalid_request");
 const body:any={model:record.model,max_tokens:maxTokens,stream:false,messages:[{role:"system",content:system},{role:"user",content:prompt}]};
 if(record.provider==="openrouter"){
  body.provider={only:[record.servingProvider],allow_fallbacks:false,data_collection:"deny",require_parameters:true};
  return {url:"https://openrouter.ai/api/v1/chat/completions",headers,body};
 }
 if(record.provider!=="huggingface"||record.model.includes(":"))throw new InferenceError("invalid_request");
 body.model+=`:${record.servingProvider}`;
 return {url:"https://router.huggingface.co/v1/chat/completions",headers,body};
}
function count(value:any){if(value===undefined||value===null)return null;if(!Number.isSafeInteger(value)||value<0)throw new InferenceError("invalid_response");return value;}
export function completionFor(data:any,record:AIConnection){
 if(!data||typeof data.model!=="string"||!data.model||data.model.length>256)throw new InferenceError("invalid_response");
 const usage=data.usage??{};
 if(typeof usage!=="object"||Array.isArray(usage))throw new InferenceError("invalid_response");
 const compatible=["openrouter","huggingface"].includes(record.provider);
 const metadata={model:data.model,provider:record.provider+(record.servingProvider?`:${record.servingProvider}`:""),input_tokens:count(usage[compatible?"prompt_tokens":"input_tokens"]),output_tokens:count(usage[compatible?"completion_tokens":"output_tokens"])};
 try{
  let text:string;
  if(record.provider==="anthropic"){
   if(data.stop_reason==="max_tokens")throw new InferenceError("output_limit");
   if(!["end_turn","stop_sequence"].includes(data.stop_reason))throw new InferenceError(data.stop_reason==="refusal"?"refused":"invalid_response");
   if(!Array.isArray(data.content)||data.content.some((b:any)=>b.type!=="text"||typeof b.text!=="string"))throw new InferenceError("invalid_response");
   text=data.content.map((b:any)=>b.text).join("");
  }else if(record.provider==="chatgpt"){
   if(data.status!=="completed"||!Array.isArray(data.output))throw new InferenceError("invalid_response");
   const blocks=data.output.flatMap((m:any)=>m.content||[]);
   if(blocks.some((b:any)=>b.type==="refusal"))throw new InferenceError("refused");
   if(blocks.some((b:any)=>b.type==="output_text"&&typeof b.text!=="string"))throw new InferenceError("invalid_response");
   text=blocks.filter((b:any)=>b.type==="output_text").map((b:any)=>b.text).join("");
  }else{
   if(!Array.isArray(data.choices)||data.choices.length!==1)throw new InferenceError("invalid_response");
   const choice=data.choices[0];
   if(choice.finish_reason==="length")throw new InferenceError("output_limit");
   if(choice.finish_reason==="content_filter"||choice.message?.refusal)throw new InferenceError("refused");
   if(choice.finish_reason!=="stop"||choice.message?.tool_calls)throw new InferenceError("invalid_response");
   text=choice.message?.content;
  }
  if(typeof text!=="string"||!text.trim())throw new InferenceError("invalid_response");
  return {text,...metadata};
 }catch(cause){if(cause instanceof InferenceError)throw new InferenceError(cause.code,metadata);throw new InferenceError("invalid_response",metadata);}
}
export async function infer(record:AIConnection,prompt:string,system:string,maxTokens:number,signal:AbortSignal,fetcher:typeof fetch=fetch){
 const request=inferenceRequest(record,prompt,system,maxTokens);
 let response:Response;
 try{response=await fetcher(request.url,{method:"POST",headers:request.headers,body:JSON.stringify(request.body),redirect:"error",signal});}
 catch{throw new InferenceError(signal.aborted?(signal.reason==="timeout"?"timeout":"cancelled"):"unavailable");}
 if(!response.ok){await response.body?.cancel();throw failure(response.status);}
 const reader=response.body?.getReader();if(!reader)throw new InferenceError("invalid_response");
 let size=0,buffer="";const decoder=new TextDecoder("utf-8",{fatal:true});
 try{
  if(record.provider==="chatgpt"&&!response.headers.get("content-type")?.includes("text/event-stream"))throw new InferenceError("invalid_response");
  while(true){
   const {value,done}=await reader.read();
   if(value){size+=value.length;if(size>MAX_RESPONSE_BYTES)throw new InferenceError("output_limit");buffer+=decoder.decode(value,{stream:true});}
   if(done)buffer+=decoder.decode();
   if(record.provider==="chatgpt"){
    buffer=buffer.replace(/\r\n/g,"\n");let index:number;
    while((index=buffer.indexOf("\n\n"))>=0){
     const frame=buffer.slice(0,index);buffer=buffer.slice(index+2);
     const text=frame.split("\n").filter(line=>line.startsWith("data:")).map(line=>line.slice(5).trimStart()).join("\n");
     if(!text||text==="[DONE]")continue;
     const event=JSON.parse(text);
     if(event.type==="response.completed")return completionFor(event.response,record);
     if(["response.failed","error"].includes(event.type))throw providerError(event.error||event.response?.error);
     if(event.type==="response.incomplete")throw new InferenceError("output_limit");
    }
   }
   if(done)break;
  }
  if(record.provider==="chatgpt")throw new InferenceError("invalid_response");
  const data=JSON.parse(buffer);if(data?.error)throw providerError(data.error);return completionFor(data,record);
 }catch(cause){if(cause instanceof InferenceError)throw cause;throw new InferenceError(signal.aborted?(signal.reason==="timeout"?"timeout":"cancelled"):"invalid_response");}
 finally{await reader.cancel().catch(()=>{});}
}
