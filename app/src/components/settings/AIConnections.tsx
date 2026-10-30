import { useEffect, useRef, useState } from "react";

const field="w-full rounded-md border border-compass-border bg-compass-card p-2.5 focus:outline-none focus:ring-2 focus:ring-compass-accent";
const button="rounded-md border border-compass-border px-3 py-2 text-sm hover:bg-compass-card focus-visible:outline focus-visible:outline-2 focus-visible:outline-compass-accent disabled:opacity-40";
export default function AIConnections() {
  const [connections,setConnections]=useState<NativeAIConnection[]>([]);
  const [active,setActive]=useState<NativeAIConnection|null>(null);
  const [selected,setSelected]=useState("");
  const [models,setModels]=useState<Array<{id:string;label:string}>>([]);
  const [routes,setRoutes]=useState<Array<{id:string;label:string}>>([]);
  const [model,setModel]=useState("");
  const [route,setRoute]=useState("");
  const [provider,setProvider]=useState("anthropic");
  const [key,setKey]=useState("");
  const [busy,setBusy]=useState("");
  const [message,setMessage]=useState("");
  const [error,setError]=useState("");
  const live=useRef(true), authorizing=useRef(false), selection=useRef(0);
  async function load() {
    const [list,status]=await Promise.all([window.compass.ai.list(),window.compass.ai.status()]);
    if(live.current) {setConnections(list);setActive(status.active);}
  }
  useEffect(()=>{
    live.current=true;
    void load().catch(cause=>{if(live.current)setError(String(cause));});
    return()=>{live.current=false;selection.current++;if(authorizing.current)void window.compass.ai.cancel();};
  },[]);
  async function run(label:string,operation:()=>Promise<unknown>) {
    setBusy(label);setError("");setMessage("");
    try {await operation();await load();}
    catch(cause) {if(live.current)setError(cause instanceof Error?cause.message:"The connection could not be updated.");}
    finally {if(live.current)setBusy("");}
  }
  async function signIn(name:string,id?:string) {
    authorizing.current=true;
    try {await run("Authorizing in your browser…",()=>window.compass.ai.signIn(name,id));}
    finally {authorizing.current=false;}
  }
  async function choose(record:NativeAIConnection) {
    const token=++selection.current;
    setSelected(record.id);setModels([]);setRoutes([]);setModel("");setRoute("");
    await run("Loading available models…",async()=>{
      const available=await window.compass.ai.models(record.id);
      if(live.current&&selection.current===token)setModels(available);
    });
  }
  async function chooseModel(value:string) {
    const token=++selection.current;
    setModel(value);setRoute("");setRoutes([]);
    if(!value)return;
    await run("Loading serving providers…",async()=>{
      const available=await window.compass.ai.routes(selected,value);
      if(live.current&&selection.current===token)setRoutes(available);
    });
  }
  const record=connections.find(item=>item.id===selected);
  const needsRoute=record&&["openrouter","huggingface"].includes(record.provider);
  return <section aria-label="AI connections" className="mb-8 space-y-4 text-compass-text">
    <h2 className="text-lg font-semibold">AI connections</h2>
    <p className="text-sm text-neutral-400">Connect your own account, then choose the model for Work. Selected task evidence is sent to that provider when you generate a draft. Billing stays with your selected account.</p>
    {active&&<p className="border-l-2 border-compass-accent pl-3 text-sm">Active: {active.label}. {active.model}. {active.billing}.</p>}
    <div className="flex flex-wrap gap-2">
      <button className={button} disabled={!!busy} onClick={()=>void signIn("chatgpt")}>Continue with ChatGPT</button>
      <button className={button} disabled={!!busy} onClick={()=>void signIn("openrouter")}>Connect OpenRouter</button>
      <button className={button} disabled={!!busy} onClick={()=>void signIn("huggingface")}>Connect Hugging Face</button>
    </div>
    {busy.startsWith("Authorizing")&&<button className={button} onClick={()=>void window.compass.ai.cancel()}>Cancel sign-in</button>}
    <details className="border-y border-compass-border py-3"><summary className="cursor-pointer text-sm">Connect with an API key or personal token</summary>
      <button className={`${button} mt-3`} disabled={!!busy} onClick={()=>void run("Checking saved Claude key…",()=>window.compass.ai.importSavedClaude())}>Use saved Claude key</button>
      <form className="mt-3 space-y-3" onSubmit={event=>{event.preventDefault();void run("Checking and saving connection…",async()=>{await window.compass.ai.saveKey(provider,key);if(live.current)setKey("");});}}>
        <label className="block text-sm space-y-1"><span>Provider</span><select className={field} disabled={!!busy} value={provider} onChange={event=>setProvider(event.target.value)}><option value="anthropic">Claude API</option><option value="openrouter">OpenRouter API</option><option value="huggingface">Hugging Face token</option></select></label>
        <label className="block text-sm space-y-1"><span>API key or token</span><input type="password" autoComplete="off" spellCheck={false} className={field} required maxLength={8192} disabled={!!busy} value={key} onChange={event=>setKey(event.target.value)} /></label>
        <button className={button} disabled={!!busy||!key.trim()}>Check and save connection</button>
      </form>
    </details>
    <p role="status" aria-live="polite" className="text-sm text-neutral-400">{busy||message}</p>
    {error&&<p role="alert" className="text-sm text-red-300">{error}</p>}
    <div className="space-y-2">{connections.map(item=><div key={item.id} className="border-b border-compass-border py-3 space-y-2">
      <div className="flex justify-between gap-3"><span className="font-medium">{item.label}</span><span className="text-sm text-neutral-400">{item.status}{!item.inferenceEnabled&&item.status==="connected"?" · inference permission not enabled":""}</span></div>
      <p className="text-sm text-neutral-400">{item.billing}</p>
      <div className="flex flex-wrap gap-2">
        <button className={button} disabled={!!busy||!item.inferenceEnabled||item.status!=="connected"} onClick={()=>void choose(item)}>Choose model</button>
        {item.provider!=="anthropic"&&<button className={button} disabled={!!busy} onClick={()=>void signIn(item.provider,item.id)}>Sign in again</button>}
        {item.status==="signed_out"&&<button className={button} disabled={!!busy} onClick={()=>void run("Forgetting connection…",()=>window.compass.ai.forget(item.id))}>Forget connection</button>}
        {item.status!=="signed_out"&&<button className={button} disabled={!!busy} onClick={()=>void run("Signing out…",async()=>{const result=await window.compass.ai.disconnect(item.id);if(live.current){setMessage(result.remoteRevoked?"Signed out; the renewable session was revoked.":"Credentials removed from Compass. Provider-side access was not confirmed revoked; remove it in your provider account if needed.");setSelected("");}})}>Sign out</button>}
      </div>
    </div>)}</div>
    {record&&<fieldset disabled={!!busy} className="space-y-3 border border-compass-border rounded-md p-4"><legend className="px-2 text-sm">Model for {record.label}</legend>
      <label className="block text-sm space-y-1"><span>Model available to this connection</span><select className={field} value={model} onChange={event=>void chooseModel(event.target.value)}><option value="">Choose a model</option>{models.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      {needsRoute&&<label className="block text-sm space-y-1"><span>Serving provider</span><select className={field} value={route} onChange={event=>setRoute(event.target.value)}><option value="">Choose a serving provider</option>{routes.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
      <p className="text-xs text-neutral-400">A model or provider failure stops the request. Compass will not switch your account, provider or billing path.</p>
      <button className={button} disabled={!model||(!!needsRoute&&!route)} onClick={()=>void run("Activating connection…",async()=>{await window.compass.ai.activate(selected,model,route);})}>Use for Work</button>
    </fieldset>}
  </section>;
}
