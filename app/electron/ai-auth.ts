/** Public-client browser authorization; credentials never cross the renderer boundary. */
import http from "http";
import { createHash, createPublicKey, randomBytes, timingSafeEqual, verify, JsonWebKey } from "crypto";
import type { AIConnection, AIProvider } from "./ai-vault";

export const CONFIG = {
  chatgpt: { issuer: "https://auth.openai.com", authorize: "https://auth.openai.com/api/accounts/authorize", token: "https://auth.openai.com/api/accounts/oauth/token", jwks: "https://auth.openai.com/.well-known/jwks.json", scope: "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct" },
  huggingface: { issuer: "https://huggingface.co", authorize: "https://huggingface.co/oauth/authorize", token: "https://huggingface.co/oauth/token", jwks: "https://huggingface.co/oauth/jwks", scope: "openid profile inference-api" },
};
export type OAuthProvider = "chatgpt" | "openrouter" | "huggingface";
export class ConnectionHTTPError extends Error {
  constructor(readonly retryable: boolean, message: string) { super(message); }
}
export type JSONFetch = (url: string, init?: RequestInit, limit?: number) => Promise<any>;
export async function requestJSON(url: string, init: RequestInit = {}, limit = 1_000_000): Promise<any> {
  let response: Response;
  try { response = await fetch(url, { ...init, redirect: "error", signal: init.signal ? AbortSignal.any([init.signal,AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000) }); }
  catch { throw new ConnectionHTTPError(true,"The provider could not be reached. Check your network and try again."); }
  if (!response.ok) throw new ConnectionHTTPError(response.status===429 || response.status>=500,response.status === 401 || response.status === 403 ? "Authorization was not accepted. Reconnect this provider account." : "The provider request failed. No other account or billing path was used.");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("The provider returned an empty response.");
  const chunks: Uint8Array[] = []; let length = 0;
  try {
    while (true) {
      const chunk = await reader.read(); if (chunk.done) break;
      length += chunk.value.length;
      if (length > limit) throw new Error("The provider response exceeded its size limit.");
      chunks.push(chunk.value);
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch { throw new Error("The provider returned an invalid or incomplete response."); }
  finally { await reader.cancel().catch(() => {}); }
}
export async function revokeSession(url: string, init: RequestInit): Promise<boolean> {
  try {
    const response = await fetch(url,{...init,redirect:"error",signal:AbortSignal.timeout(10_000)});
    await response.body?.cancel();
    return response.status===200;
  } catch { return false; }
}
export function same(a: string, b: string) {
  const left = Buffer.from(a), right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}
export async function validateIdentity(token: string, provider: "chatgpt" | "huggingface", clientId: string, nonce: string | undefined, fetchJSON: JSONFetch, requireNonce = true) {
  try {
    const parts = token.split(".");
    if (parts.length !== 3 || parts.some(part => !/^[A-Za-z0-9_-]+$/.test(part))) throw new Error();
    const header = JSON.parse(Buffer.from(parts[0],"base64url").toString());
    const claims = JSON.parse(Buffer.from(parts[1],"base64url").toString());
    if (header.alg !== "RS256" || typeof header.kid !== "string" || header.jku || header.x5u) throw new Error();
    const keys = await fetchJSON(CONFIG[provider].jwks);
    const key = keys.keys?.find((candidate: any) => candidate.kid === header.kid && candidate.kty === "RSA" && (!candidate.use || candidate.use === "sig") && (!candidate.alg || candidate.alg === "RS256"));
    if (!key || !verify("RSA-SHA256", Buffer.from(`${parts[0]}.${parts[1]}`), createPublicKey({ key: key as JsonWebKey, format: "jwk" }), Buffer.from(parts[2],"base64url"))) throw new Error();
    const now = Date.now()/1000;
    const audience = typeof claims.aud === "string" ? [claims.aud] : claims.aud;
    if (claims.iss !== CONFIG[provider].issuer || !Array.isArray(audience) || !audience.includes(clientId) || ((audience.length > 1 || claims.azp !== undefined) && claims.azp !== clientId) || typeof claims.sub !== "string" || !claims.sub || !Number.isFinite(claims.exp) || claims.exp <= now || (claims.nbf !== undefined && (!Number.isFinite(claims.nbf) || claims.nbf > now + 30)) || (nonce !== undefined && (requireNonce || claims.nonce !== undefined) && (typeof claims.nonce !== "string" || !same(claims.nonce,nonce)))) throw new Error();
    return { issuer: claims.iss as string, subject: claims.sub as string, label: String(claims.email || claims.preferred_username || claims.name || "Connected account").slice(0,160) };
  } catch { throw new Error("The provider identity could not be verified. The existing connection was kept."); }
}
export function tokenValues(data: any, provider: OAuthProvider, previous?: AIConnection) {
  if (typeof data.scope !== "string" || (data.token_type && String(data.token_type).toLowerCase() !== "bearer")) throw new Error("The provider did not return a verifiable permission grant.");
  const scopes = data.scope.split(/\s+/).filter(Boolean);
  const enabled = provider === "chatgpt" ? scopes.includes("chatgpt.tokens.use.direct") && scopes.includes("resource.invoke") : scopes.includes("inference-api");
  if (enabled && (typeof data.access_token !== "string" || !data.access_token)) throw new Error("The provider did not return an inference credential.");
  if (data.expires_in !== undefined && (!Number.isFinite(data.expires_in) || data.expires_in <= 0)) throw new Error("The provider returned an invalid credential lifetime.");
  return { accessToken: typeof data.access_token === "string" ? data.access_token : "", refreshToken: typeof data.refresh_token === "string" ? data.refresh_token : previous?.refreshToken,
    idToken: typeof data.id_token === "string" ? data.id_token : previous?.idToken,
    expiresAt: data.expires_in ? Date.now()+data.expires_in*1000 : undefined, scopes, inferenceEnabled: enabled };
}
export async function authorize(provider: OAuthProvider, hostId: string, openBrowser: (url: string) => Promise<unknown>, signal: AbortSignal, fetchJSON: JSONFetch = requestJSON, previous?: AIConnection, hfClientId = "") {
  const state = randomBytes(32).toString("base64url"), nonce = randomBytes(32).toString("base64url"), verifier = randomBytes(48).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  if (provider === "huggingface" && !hfClientId) throw new Error("Hugging Face browser sign-in is unavailable in this build. Connect with a personal token instead.");
  const operation = new AbortController();
  const attemptSignal = AbortSignal.any([signal,operation.signal]);
  const inAttempt: JSONFetch = (url,init={},limit) => fetchJSON(url,{...init,signal:attemptSignal},limit);
  const server = http.createServer();
  let redirect = "";
  let resolveCallback: (value: URL) => void = () => {};
  let rejectCallback: (reason: Error) => void = () => {};
  const callback = new Promise<URL>((resolve,reject) => { resolveCallback=resolve; rejectCallback=reject; });
  // Attach a rejection handler before browser launch or asynchronous listen errors.
  void callback.catch(() => {});
  const stop = () => rejectCallback(new Error("Provider sign-in was cancelled."));
  const timer = setTimeout(() => { operation.abort(); rejectCallback(new Error("Provider sign-in timed out. Start a new attempt.")); }, 300_000);
  signal.addEventListener("abort",stop,{once:true});
  server.on("request",(request,response) => {
    const reply = (status: number,text: string) => { response.writeHead(status,{"Content-Type":"text/plain; charset=utf-8","Cache-Control":"no-store","X-Content-Type-Options":"nosniff"}); response.end(text); };
    let url: URL;
    try { url = new URL(request.url || "",redirect); } catch { reply(400,"Invalid callback"); return; }
    if (request.method !== "GET" || request.headers.host !== new URL(redirect).host || url.origin !== new URL(redirect).origin || url.pathname !== "/auth/callback") { reply(404,"Not found"); return; }
    const deniedWithoutState = provider === "openrouter" && url.searchParams.has("error") && !url.searchParams.has("state");
    if (!deniedWithoutState && (url.searchParams.getAll("state").length !== 1 || !same(url.searchParams.get("state") || "",state))) { reply(400,"Invalid sign-in state"); return; }
    if (url.searchParams.getAll("code").length > 1 || url.searchParams.getAll("client_id").length > 1) { reply(400,"Invalid callback"); return; }
    reply(200,"Authorization received. Return to Compass."); resolveCallback(url);
  });
  try {
    if (signal.aborted) throw new Error("Provider sign-in was cancelled.");
    await new Promise<void>((resolve,reject) => { server.once("error",reject); server.listen(0,"127.0.0.1",resolve); });
    const address = server.address(); if (!address || typeof address === "string") throw new Error("Could not start the sign-in callback.");
    redirect = `http://127.0.0.1:${address.port}/auth/callback`;
    const clientId = previous?.clientId || (provider === "chatgpt" ? "dynamic_agent_client" : hfClientId);
    const url = new URL(provider === "openrouter" ? "https://openrouter.ai/auth" : CONFIG[provider].authorize);
    url.searchParams.set("state",state); url.searchParams.set("code_challenge",challenge); url.searchParams.set("code_challenge_method","S256");
    if (provider === "openrouter") { url.searchParams.set("callback_url",redirect); url.searchParams.set("key_label","Compass"); }
    else {
      for (const [key,value] of Object.entries({client_id:clientId,response_type:"code",redirect_uri:redirect,scope:CONFIG[provider].scope,nonce})) url.searchParams.set(key,value);
      if (provider === "chatgpt") {
        url.searchParams.set("ext_agent_host_id",hostId); url.searchParams.set("resource","https://api.openai.com/v1");
        if (!previous?.clientId) url.searchParams.set("agent_name_hint","Compass");
        if (previous?.idToken) url.searchParams.set("id_token_hint",previous.idToken);
        if (previous && !previous.inferenceEnabled) url.searchParams.set("prompt","consent");
      }
    }
    await openBrowser(url.href);
    const result = await callback;
    if (signal.aborted) throw new Error("Provider sign-in was cancelled.");
    if (result.searchParams.has("error")) throw new Error("Provider authorization was declined. The previous connection was kept.");
    const code = result.searchParams.get("code"); if (!code) throw new Error("The provider did not return an authorization code.");
    if (provider === "openrouter") {
      const data = await inAttempt("https://openrouter.ai/api/v1/auth/keys",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code,code_verifier:verifier,code_challenge_method:"S256"}),signal});
      if (typeof data.key !== "string" || !data.key) throw new Error("OpenRouter did not return an API key.");
      return { label:"OpenRouter", accessToken:data.key, scopes:[], inferenceEnabled:true };
    }
    const issued = result.searchParams.get("client_id") || clientId;
    if (issued === "dynamic_agent_client" || !issued || (previous?.clientId && issued !== previous.clientId)) throw new Error("The provider returned an unexpected client registration.");
    const form = new URLSearchParams({grant_type:"authorization_code",client_id:issued,code,code_verifier:verifier,redirect_uri:redirect});
    if (provider === "chatgpt") form.set("resource","https://api.openai.com/v1");
    const data = await inAttempt(CONFIG[provider].token,{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:form.toString(),signal});
    if (typeof data.id_token !== "string") throw new Error("The provider did not return an identity token.");
    const identity = await validateIdentity(data.id_token,provider,issued,nonce,inAttempt);
    if (previous && (previous.subject !== identity.subject || previous.issuer !== identity.issuer)) throw new Error("Sign-in returned a different account. Add it as a new connection instead.");
    return { ...identity, oidcNonce:nonce, clientId:issued, ...tokenValues(data,provider,previous) };
  } finally { clearTimeout(timer); signal.removeEventListener("abort",stop); server.closeAllConnections(); if(server.listening) server.close(); }
}
