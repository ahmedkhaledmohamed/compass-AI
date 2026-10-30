/** Native connection lifecycle and metadata-only IPC contract. */
import { randomUUID } from "crypto";
import { AIVault, AIConnection, AIProvider, publicConnection } from "./ai-vault";
import { authorize, CONFIG, ConnectionHTTPError, JSONFetch, OAuthProvider, requestJSON, revokeSession, tokenValues, validateIdentity } from "./ai-auth";

import {bindingFor,InferenceBroker} from "./inference-broker";

const uuid = () => randomUUID().replace(/-/g,"");
const validId = (id: unknown): string => { if (typeof id !== "string" || !/^[a-f0-9]{32}$/.test(id)) throw new Error("Invalid AI connection."); return id; };
const validProvider = (provider: unknown): AIProvider => { if (typeof provider !== "string" || !["chatgpt","openrouter","anthropic","huggingface"].includes(provider)) throw new Error("Unsupported AI provider."); return provider as AIProvider; };
export class AIConnections {
  private pending?: AbortController;
  private refreshes = new Map<string,Promise<AIConnection>>();
  private selection: Promise<unknown> = Promise.resolve();
  private broker: InferenceBroker;
  private jobs = new Map<string,{binding:string;token:string;jobId:string}>();
  constructor(private vault: AIVault, private openBrowser: (url: string) => Promise<unknown>, private engine: (endpoint: string, body?: unknown) => Promise<any>, private fetchJSON: JSONFetch = requestJSON, private hfClientId = "", private revoke: (url:string,init:RequestInit) => Promise<boolean> = revokeSession, inferenceFetch:typeof fetch=fetch) { this.broker=new InferenceBroker(inferenceFetch); }
  status() {
    const data = this.vault.read();
    const active = data.connections.find(item => item.id === data.active);
    return { managed:data.managed, active:active ? publicConnection(active) : null };
  }
  list() { return this.vault.read().connections.map(publicConnection); }
  private record(id: string) {
    const record = this.vault.read().connections.find(item => item.id === validId(id));
    if (!record) throw new Error("AI connection not found.");
    return record;
  }
  cancel() { this.pending?.abort(); return true; }
  async signIn(providerValue: unknown, id?: string) {
    const provider = validProvider(providerValue);
    if (provider === "anthropic") throw new Error("Connect Claude with an API key.");
    if (this.pending) throw new Error("Finish or cancel the current provider sign-in first.");
    const data = this.vault.read();
    const selected = id ? this.record(id) : undefined;
    if (selected && selected.provider !== provider) throw new Error("Connection provider mismatch.");
    // OpenRouter returns a new key without a verified stable user identity.
    // Keep it as a separate connection rather than replacing an active account.
    const previous = provider === "openrouter" ? undefined : selected;
    this.vault.write(data); // Persist the host identity before the browser opens.
    const controller = new AbortController(); this.pending=controller;
    try {
      const credentials = await authorize(provider as OAuthProvider,data.hostId,this.openBrowser,controller.signal,this.fetchJSON,previous,this.hfClientId);
      if (controller.signal.aborted) throw new Error("Provider sign-in was cancelled.");
      if (previous) {
        this.vault.update(previous.id,previous.revision,record => Object.assign(record,credentials,{status:"connected"}));
      } else {
        const latest = this.vault.read();
        if (latest.connections.length >= 32) throw new Error("Connection limit reached. Sign out and forget an unused connection first.");
        const record: AIConnection = {...credentials,id:uuid(),revision:1,provider,status:"connected",model:"",servingProvider:""};
        if (provider === "chatgpt") record.label += ` (${record.clientId?.slice(-6)})`;
        if (provider === "openrouter") record.label += ` (${record.id.slice(-6)})`;
        latest.connections.push(record); this.vault.write(latest);
      }
      return this.list();
    } catch(cause) {
      if(controller.signal.aborted) throw new Error("Provider sign-in was cancelled.");
      throw cause;
    } finally { if(this.pending===controller) this.pending=undefined; }
  }
  async saveKey(providerValue: unknown, value: unknown) {
    const provider = validProvider(providerValue);
    if (provider === "chatgpt" || typeof value !== "string" || !value.trim() || value.length > 8192 || /[\r\n]/.test(value)) throw new Error("Enter a valid provider API key.");
    this.vault.requireEncryption();
    const key = value.trim();
    if (provider === "anthropic") await this.fetchJSON("https://api.anthropic.com/v1/models",{headers:{"x-api-key":key,"anthropic-version":"2023-06-01"}});
    else if (provider === "openrouter") await this.fetchJSON("https://openrouter.ai/api/v1/key",{headers:{Authorization:`Bearer ${key}`}});
    else await this.fetchJSON("https://huggingface.co/api/whoami-v2",{headers:{Authorization:`Bearer ${key}`}});
    const data = this.vault.read();
    if (data.connections.length >= 32) throw new Error("Connection limit reached.");
    data.connections.push({id:uuid(),revision:1,provider,label:provider === "anthropic" ? "Claude API" : provider === "openrouter" ? "OpenRouter API" : "Hugging Face token",accessToken:key,scopes:[],inferenceEnabled:true,status:"connected",model:"",servingProvider:""});
    this.vault.write(data); return this.list();
  }
  private async credential(id: string): Promise<AIConnection> {
    const allowed = (record: AIConnection) => {
      if (record.status !== "connected" || !record.inferenceEnabled || !record.accessToken) throw new Error("Reconnect this account and authorize inference before using it.");
      return record;
    };
    const record = allowed(this.record(id));
    if (!record.expiresAt || record.expiresAt > Date.now()+30_000) return record;
    const existing = this.refreshes.get(id); if(existing) return existing.then(allowed);
    const promise = this.refresh(record); this.refreshes.set(id,promise);
    try { return allowed(await promise); } finally { if(this.refreshes.get(id)===promise) this.refreshes.delete(id); }
  }
  private async refresh(record: AIConnection) {
    if (!record.refreshToken || !record.clientId || !["chatgpt","huggingface"].includes(record.provider)) throw new Error("This account needs browser sign-in again.");
    const provider = record.provider as "chatgpt" | "huggingface";
    const form = new URLSearchParams({grant_type:"refresh_token",client_id:record.clientId,refresh_token:record.refreshToken});
    if (provider === "chatgpt") form.set("resource","https://api.openai.com/v1");
    try {
      const data = await this.fetchJSON(CONFIG[provider].token,{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:form.toString()});
      if (provider === "chatgpt" && (typeof data.refresh_token !== "string" || !data.refresh_token)) throw new Error("Refresh did not return a replacement credential.");
      if (data.scope === undefined) data.scope = record.scopes.join(" ");
      if (data.id_token) {
        const identity = await validateIdentity(data.id_token,provider,record.clientId,record.oidcNonce,this.fetchJSON,false);
        if (identity.subject !== record.subject || identity.issuer !== record.issuer) throw new Error("Refresh returned a different identity.");
      }
      const tokens = tokenValues(data,provider,record);
      return this.vault.update(record.id,record.revision,current => Object.assign(current,tokens));
    } catch (cause) {
      if(cause instanceof ConnectionHTTPError && cause.retryable) throw new Error("The provider could not refresh this connection right now. Try again; the saved connection was kept.");
      // CAS prevents an old refresh from resurrecting a disconnected credential.
      try { this.vault.update(record.id,record.revision,current => { current.status="reconnect"; current.accessToken=""; }); } catch { /* A newer user operation wins. */ }
      throw new Error("This AI account could not be refreshed. Sign in again; no other account was used.");
    }
  }
  async models(id: string) {
    const record = await this.credential(validId(id));
    let data: any;
    if (record.provider === "chatgpt") data = await this.fetchJSON("https://api.openai.com/v1/models",{headers:{Authorization:`Bearer ${record.accessToken}`}},8_000_000);
    else if (record.provider === "anthropic") data = await this.fetchJSON("https://api.anthropic.com/v1/models",{headers:{"x-api-key":record.accessToken,"anthropic-version":"2023-06-01"}},8_000_000);
    else data = await this.fetchJSON(record.provider === "openrouter" ? "https://openrouter.ai/api/v1/models" : "https://router.huggingface.co/v1/models",{headers:{Authorization:`Bearer ${record.accessToken}`}},8_000_000);
    const entries = record.provider === "chatgpt" ? data.models : data.data;
    if (!Array.isArray(entries)) throw new Error("The provider did not return a model catalog.");
    return entries.filter((item: any) => record.provider !== "chatgpt" || item.visibility === "list").map((item: any) => ({id:String(record.provider === "chatgpt" ? item.slug : item.id),label:String(item.display_name || item.name || item.id || item.slug)})).filter((item: any) => item.id && item.id.length <= 256).slice(0,2000);
  }
  async routes(id: string, model: string) {
    const record = await this.credential(validId(id));
    if (!["openrouter","huggingface"].includes(record.provider)) return [];
    const parts = model.split("/");
    if (parts.length !== 2 || parts.some(part => !/^[A-Za-z0-9_.:-]{1,160}$/.test(part))) throw new Error("Choose a model from the provider catalog.");
    const encoded = parts.map(encodeURIComponent).join("/");
    if (record.provider === "openrouter") {
      const data = await this.fetchJSON(`https://openrouter.ai/api/v1/models/${encoded}/endpoints`,{headers:{Authorization:`Bearer ${record.accessToken}`}});
      if (!Array.isArray(data.data?.endpoints)) throw new Error("No serving-provider catalog was returned.");
      return data.data.endpoints.filter((entry: any) => typeof entry.tag === "string").map((entry: any) => ({id:entry.tag,label:String(entry.provider_name || entry.name || entry.tag)}));
    }
    const data = await this.fetchJSON(`https://huggingface.co/api/models/${encoded}?expand=inferenceProviderMapping`);
    return Object.entries(data.inferenceProviderMapping || {}).filter(([,entry]: any) => entry.status === "live" && entry.task === "conversational").map(([key]) => ({id:key,label:key}));
  }
  private serial<T>(operation: () => Promise<T>): Promise<T> {
    const next = this.selection.then(operation,operation);
    this.selection = next.catch(() => {}); return next;
  }
  private async idle() {
    const result = await this.engine("/connections/readiness");
    if (result.status!=="ok" || typeof result.busy!=="boolean") throw new Error("The engine is not ready to change AI connections. Restart it and try again.");
    if (result.busy || this.broker.busy()) throw new Error("Finish or cancel generation before changing the active AI connection.");
  }
  activate(id: string, model: string, serving: string) {
    return this.serial(async () => {
      validId(id);
      if(typeof model !== "string" || !model || model.length>256 || typeof serving !== "string") throw new Error("Choose a model and serving provider.");
      const choices = await this.models(id);
      if(!choices.some((item: any) => item.id===model)) throw new Error("That model is not available to the selected connection.");
      const record = await this.credential(id);
      if (["openrouter","huggingface"].includes(record.provider)) {
        const routes = await this.routes(id,model);
        if (!routes.some((route: any) => route.id===serving)) throw new Error("Choose an available serving provider.");
      } else serving="";
      await this.idle();
      const latest = this.vault.read();
      const current = latest.connections.find(item => item.id===id);
      if (!current || current.revision!==record.revision) throw new Error("This connection changed. Select it again.");
      current.model=model; current.servingProvider=serving; current.revision++;
      latest.active=id; latest.managed=true; this.vault.write(latest);

      return this.status();
    });
  }
  private async prepareSelected() {
    const data = this.vault.read();
    if (!data.managed) throw new Error("Choose an AI connection before generating a draft.");
    if (!data.active) { await this.engine("/connections/clear",{}); throw new Error("Choose an AI connection before generating a draft."); }
    const record = await this.credential(data.active);
    if (!record.model) throw new Error("Choose a model before generating a draft.");
    return record;
  }
  prepare() { return this.serial(() => this.prepareSelected()); }
  runGeneration(endpoint: string, body: unknown) {
    return this.serial(async () => {
      if(endpoint!=="/tasks/jobs/start")throw new Error("Use a durable generation job.");
      for(const [key,entry] of this.jobs)if(!this.broker.has(entry.token))this.jobs.delete(key);
      const binding=bindingFor(body);
      const key=JSON.stringify([binding.workspace_path,binding.request_id]);
      const previous=this.jobs.get(key);
      if(previous){
        if(previous.binding!==JSON.stringify(binding))throw new Error("Request ID was already used for a different operation.");
        return this.observeGeneration("/tasks/jobs/status",{workspace_path:binding.workspace_path,task_id:binding.task_id,job_id:previous.jobId});
      }
      const record=await this.prepareSelected();
      const grant=await this.broker.issue(body,record,async()=>{if(this.record(record.id).revision!==record.revision)throw new Error("This connection changed before inference.");return this.credential(record.id);});
      try {
        const result=await this.engine(endpoint,{...(body as object),native_inference:grant});
        if(result.status!=="ok" || !["queued","running"].includes(result.job?.state))this.broker.revoke(grant.token);
        else this.jobs.set(key,{binding:JSON.stringify(binding),token:grant.token,jobId:result.job.id});
        if(result.status!=="ok")return {status:"error",message:typeof result.detail==="string"?result.detail:"The generation job could not be started. Reload the task and try again."};
        return result;
      } catch(cause){this.broker.revoke(grant.token);throw cause;}
    });
  }
  async observeGeneration(endpoint:string,body:any){
    const result=await this.engine(endpoint,body);
    if(result.status==="ok"&&result.job&&!['queued','running'].includes(result.job.state)){
      for(const [key,entry] of this.jobs){
        const binding=JSON.parse(entry.binding);
        if(entry.jobId===result.job.id&&binding.workspace_path===body?.workspace_path&&binding.task_id===body?.task_id){this.broker.revoke(entry.token);this.jobs.delete(key);}
      }
    }
    return result;
  }
  resetInference() { this.broker.reset();this.jobs.clear(); }
  async close() { await this.broker.close(); }
  async restore() {
    this.resetInference();
    const data = this.vault.read(); if(!data.managed) { await this.engine("/connections/clear",{}); return; }
    if (!data.active) { await this.engine("/connections/clear",{}); return; }
    try { await this.prepare(); } catch { await this.engine("/connections/clear",{}); throw new Error("Reconnect the selected AI account before generating a draft."); }
  }
  forget(id: string) {
    const data=this.vault.read();
    const record=data.connections.find(item=>item.id===validId(id));
    if(!record || record.status!=="signed_out" || record.accessToken || record.refreshToken || data.active===id) throw new Error("Sign out before forgetting this connection.");
    data.connections=data.connections.filter(item=>item.id!==id);this.vault.write(data);return this.list();
  }
  disconnect(id: string) {
    return this.serial(async () => {
      validId(id);
      const activeData = this.vault.read();
      if(activeData.active===id) await this.idle();
      const refreshing = this.refreshes.get(id);
      if(refreshing) await refreshing.catch(() => {});
      const record = this.record(id);
      this.vault.update(id,record.revision,current => { current.accessToken=""; current.refreshToken=undefined; current.idToken=undefined; current.expiresAt=undefined; current.status="signed_out"; current.inferenceEnabled=false; });
      const latest=this.vault.read();
      if(latest.active===id) { latest.active=null; this.vault.write(latest); await this.engine("/connections/clear",{}); }
      let remoteRevoked = false;
      if(record.provider==="chatgpt" && record.refreshToken && record.clientId) {
        try {
          const metadata = await this.fetchJSON("https://auth.openai.com/.well-known/openid-configuration");
          const endpoint = new URL(metadata.revocation_endpoint);
          if(endpoint.origin!=="https://auth.openai.com" || endpoint.username || endpoint.password) throw new Error();
          const body = new URLSearchParams({token:record.refreshToken,token_type_hint:"refresh_token",client_id:record.clientId});
          // Revocation success has an empty body; do not parse it as JSON.
          remoteRevoked=await this.revoke(endpoint.href,{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:body.toString()});
        } catch { /* Local sign-out still removes credentials; report remote status. */ }
      }
      return {connections:this.list(),remoteRevoked};
    });
  }
}

export function engineOperation(endpoint: unknown): "native-only" | "generation" | "other" {
  if(typeof endpoint!=="string" || endpoint.length>2048) throw new Error("Invalid engine endpoint.");
  let pathname: string;
  try { pathname=decodeURIComponent(new URL(endpoint,"http://127.0.0.1/").pathname).replace(/\/+$/,""); }
  catch { throw new Error("Invalid engine endpoint."); }
  if(["/configure","/connections/clear"].includes(pathname)) return "native-only";
  return ["/tasks/jobs/start","/tasks/generate"].includes(pathname)?"generation":"other";
}
