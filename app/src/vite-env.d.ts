/// <reference types="vite/client" />

interface CompassEngine {
  call: (endpoint: string, body?: unknown) => Promise<unknown>;
  health: () => Promise<{ status: string; version: string }>;
  restart: () => Promise<{ status: string; message?: string }>;
  stream: (endpoint: string, body?: unknown) => Promise<{ status: string }>;
  onStreamData: (callback: (data: string) => void) => () => void;
  onStatus: (callback: (data: { state: string; message: string }) => void) => () => void;
}

interface CompassApp {
  selectDirectory: () => Promise<string | null>;
  selectFile: (filters?: { name: string; extensions: string[] }[]) => Promise<string | null>;
  saveFile: (defaultName: string, content: string) => Promise<string | null>;
  exportDocument: (
    defaultName: string,
    content: string,
    format: "md" | "html" | "pdf" | "docx",
  ) => Promise<string | null>;
  captureHtmlPng: (html: string, defaultName: string) => Promise<string | null>;
}

interface CompassSecrets {
  store: (key: string, value: string) => Promise<boolean>;
  load: (key: string) => Promise<string | null>;
  delete: (key: string) => Promise<boolean>;
}

interface CredentialObject {
  provider: string;
  method: "oauth" | "api_key" | "pat";
  access_token: string;
  refresh_token?: string;
  expires_at?: number;
  scopes?: string[];
  metadata?: Record<string, string>;
}

interface CredentialInfoObject {
  provider: string;
  method: string;
  status: "connected" | "expired" | "disconnected";
  scopes?: string[];
  expires_at?: number;
  metadata?: Record<string, string>;
}

interface CompassCredentials {
  store: (provider: string, credential: CredentialObject) => Promise<boolean>;
  load: (provider: string) => Promise<CredentialObject | null>;
  delete: (provider: string) => Promise<boolean>;
  list: () => Promise<CredentialInfoObject[]>;
}

interface OAuthProviderConfig {
  id: string;
  name: string;
  auth_url: string;
  token_url: string;
  client_id: string;
  scopes: string[];
  icon?: string;
  extra_auth_params?: Record<string, string>;
  api_key_only?: boolean;
}

interface OAuthResultObject {
  provider: string;
  access_token: string;
  refresh_token?: string;
  expires_at?: number;
  scopes: string[];
}

interface CompassOAuth {
  start: (providerConfig: OAuthProviderConfig) => Promise<OAuthResultObject>;
  refresh: (
    providerConfig: OAuthProviderConfig,
    refreshToken: string
  ) => Promise<{ access_token: string; refresh_token: string; expires_at?: number }>;
}

interface CompassProviders {
  get: (id: string) => Promise<OAuthProviderConfig | null>;
  list: () => Promise<OAuthProviderConfig[]>;
}

interface NativeAIConnection {
  id: string; provider: "chatgpt" | "openrouter" | "anthropic" | "huggingface";
  label: string; status: string; inferenceEnabled: boolean; model: string; servingProvider: string; billing: string;
}
interface NativeAI {
  status(): Promise<{managed:boolean;active:NativeAIConnection|null}>;
  list(): Promise<NativeAIConnection[]>;
  signIn(provider:string,id?:string): Promise<NativeAIConnection[]>;
  cancel(): Promise<boolean>;
  importSavedClaude(): Promise<NativeAIConnection[]>;
  saveKey(provider:string,key:string): Promise<NativeAIConnection[]>;
  models(id:string): Promise<Array<{id:string;label:string}>>;
  routes(id:string,model:string): Promise<Array<{id:string;label:string}>>;
  activate(id:string,model:string,serving:string): Promise<{managed:boolean;active:NativeAIConnection|null}>;
  forget(id:string): Promise<NativeAIConnection[]>;
  disconnect(id:string): Promise<{connections:NativeAIConnection[];remoteRevoked:boolean}>;
}

interface NativeWorkspace {
  id:string; path:string; name:string; description:string; created_at:string; last_opened:string;
}
interface NativeWorkspaces {
  list():Promise<{workspaces:NativeWorkspace[];activeWorkspaceId:string|null}>;
  choose(purpose:"workspace"|"source",kind:"directory"|"file"):Promise<{id:string;label:string}|null>;
  create(selection:string,name:string,description:string):Promise<NativeWorkspace>;
  open():Promise<NativeWorkspace|null>;
  activate(id:string):Promise<NativeWorkspace>;
  forget(id:string):Promise<{workspaces:NativeWorkspace[];activeWorkspaceId:string|null}>;
}

interface Window {
  compass: {
    ai: NativeAI;
    workspaces:NativeWorkspaces;
    engine: CompassEngine;
    app: CompassApp;
    secrets: CompassSecrets;
    credentials: CompassCredentials;
    oauth: CompassOAuth;
    providers: CompassProviders;
  };
}
