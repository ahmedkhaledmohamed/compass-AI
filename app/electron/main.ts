import { app, BrowserWindow, dialog, safeStorage, shell } from "electron";
import { pathToFileURL } from "url";
import path from "path";
import fs from "fs";
import { autoUpdater } from "electron-updater";

// Load .env file before any provider config is read
const envPath = path.join(__dirname, "..", ".env");
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, "utf-8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eqIdx = trimmed.indexOf("=");
    if (eqIdx > 0) {
      const key = trimmed.slice(0, eqIdx).trim();
      const val = trimmed.slice(eqIdx + 1).trim();
      if (!process.env[key]) process.env[key] = val;
    }
  }
}

import { startEngine, stopEngine, engineFetch } from "./engine-bridge";
import { handleMainIPC, trustMainWindow } from "./ipc";
import { requireReleaseEndpoint } from "./release-policy";
import { WorkspaceAccess } from "./workspace-access";
import { saveTaskExport } from "./task-export";
import { AIVault } from "./ai-vault";
import { AIConnections, engineOperation } from "./ai-connections";

let aiConnections: AIConnections | undefined;
let workspaceAccess: WorkspaceAccess | undefined;
const ownsInstance = app.requestSingleInstanceLock();
if (!ownsInstance) app.quit();

let mainWindow: BrowserWindow | null = null;

const isDev = !app.isPackaged;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: "hiddenInset",
    backgroundColor: "#0a0a0a",
    trafficLightPosition: { x: 16, y: 16 },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  trustMainWindow(mainWindow, isDev
    ? "http://localhost:5173"
    : pathToFileURL(path.join(__dirname, "../dist/index.html")).href);

  if (isDev) {
    mainWindow.loadURL("http://localhost:5173");
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  if (!ownsInstance) return;
  aiConnections = new AIConnections(new AIVault(path.join(app.getPath("userData"),"ai-connections.enc"), {
    available:hasSecureSecretStorage,
    encrypt:value => safeStorage.encryptString(value),
    decrypt:value => safeStorage.decryptString(value),
  }), url => shell.openExternal(url), (endpoint,body) => engineFetch(endpoint,body ? {body} : undefined), undefined, process.env.COMPASS_HF_CLIENT_ID || "");
  workspaceAccess = new WorkspaceAccess(path.join(app.getPath("userData"),"workspaces.json"),async kind=>{
    const result=await dialog.showOpenDialog({properties:kind==="directory"?["openDirectory"]:["openFile"]});
    return result.canceled?null:result.filePaths[0];
  },(endpoint,body)=>engineFetch(endpoint,body?{body}:undefined));
  createWindow();

  try {
    await startEngine();
    await aiConnections?.restore();
  } catch (err) {
    console.error("[main] Failed to start engine:", err);
  }

  // Auto-updater: check for updates in packaged builds
  if (app.isPackaged) {
    autoUpdater.logger = console;
    autoUpdater.checkForUpdatesAndNotify();
  }
});

app.on("second-instance", () => {
  // Focus existing window
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.on("window-all-closed", () => {
  stopEngine();
  app.quit();
});

app.on("before-quit", () => {
  aiConnections?.cancel();
  void aiConnections?.close();
  stopEngine();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

// IPC: Save file dialog
handleMainIPC("save-file", async (_event, defaultName: string, content: string) => {
  const result = await dialog.showSaveDialog({
    defaultPath: defaultName,
    filters: [{ name: "Markdown", extensions: ["md"] }],
  });
  if (result.canceled || !result.filePath) return null;
  const fs = await import("fs/promises");
  await fs.writeFile(result.filePath, content, "utf-8");
  return result.filePath;
});

// IPC: Engine API call (proxied through main process via engine-bridge)
handleMainIPC("engine-call", async (_event, endpoint: string, body?: unknown) => {
  try {
    requireReleaseEndpoint(endpoint);
    const operation=engineOperation(endpoint);
    if(operation === "native-only" && aiConnections) return {status:"error",message:"Use AI connections to change this provider or model."};
    return await nativeWorkspaces().request(endpoint,body,(target,request)=>{
      if(operation === "generation" && aiConnections)return aiConnections.runGeneration(target,request);
      if(["/tasks/jobs/status","/tasks/jobs/latest","/tasks/jobs/cancel"].includes(target)&&aiConnections)return aiConnections.observeGeneration(target,request);
      return engineFetch(target,request?{body:request}:undefined);
    });
  } catch (err) {
    return { status: "error", message: String(err) };
  }
});

// IPC: Engine health check
handleMainIPC("engine-health", async () => {
  try {
    return await engineFetch("/health");
  } catch {
    return { status: "unavailable" };
  }
});

// IPC: Restart engine after crash
handleMainIPC("engine-restart", async () => {
  try {
    aiConnections?.resetInference();
    stopEngine();
    await startEngine();
    await aiConnections?.restore();
    return { status: "ok" };
  } catch (err) {
    return { status: "error", message: String(err) };
  }
});

// IPC: Secure secret storage via OS keychain (Electron safeStorage)
// Secrets are encrypted with the OS keychain and stored in a local file.
// Persistent credentials require an available OS-backed encryption service.
const secretsPath = path.join(app.getPath("userData"), "secrets.enc");

function hasSecureSecretStorage(): boolean {
  return safeStorage.isEncryptionAvailable() &&
    (process.platform !== "linux" || safeStorage.getSelectedStorageBackend() !== "basic_text");
}

handleMainIPC("secrets-store", async (_event, key: string, value: string) => {
  if (!hasSecureSecretStorage()) {
    throw new Error("Secure credential storage is unavailable. Unlock your OS keychain and try again.");
  }
  const store = loadSecretsFile();
  store[key] = safeStorage.encryptString(value).toString("base64");
  fs.writeFileSync(secretsPath, JSON.stringify(store), "utf-8");
  return true;
});

handleMainIPC("secrets-load", async (_event, key: string) => {
  if(aiConnections && key === "anthropic-api-key") return null;
  const store = loadSecretsFile();
  const encrypted = store[key];
  if (!encrypted) return null;
  if (!hasSecureSecretStorage()) return null;
  try {
    return safeStorage.decryptString(Buffer.from(encrypted, "base64"));
  } catch {
    return null; // Corrupted or re-keyed — user will need to re-enter
  }
});

handleMainIPC("secrets-delete", async (_event, key: string) => {
  const store = loadSecretsFile();
  delete store[key];
  fs.writeFileSync(secretsPath, JSON.stringify(store), "utf-8");
  return true;
});

function loadSecretsFile(): Record<string, string> {
  try {
    return JSON.parse(fs.readFileSync(secretsPath, "utf-8"));
  } catch {
    return {};
  }
}

// --- Credential Vault ---
// Structured credential storage for OAuth tokens and API keys.
// Credentials are stored as encrypted JSON blobs keyed by "credential:{provider}".
// This is separate from the flat secrets store to support structured credential objects.

const CREDENTIAL_PREFIX = "credential:";

function encryptValue(value: string): string {
  if (!hasSecureSecretStorage()) {
    throw new Error("Secure credential storage is unavailable. Unlock your OS keychain and try again.");
  }
  return safeStorage.encryptString(value).toString("base64");
}

function decryptValue(encrypted: string): string | null {
  if (!hasSecureSecretStorage()) return null;
  try {
    return safeStorage.decryptString(Buffer.from(encrypted, "base64"));
  } catch {
    return null;
  }
}

handleMainIPC(
  "credential-store",
  async (_event, provider: string, credential: unknown) => {
    const store = loadSecretsFile();
    const key = CREDENTIAL_PREFIX + provider;
    store[key] = encryptValue(JSON.stringify(credential));
    fs.writeFileSync(secretsPath, JSON.stringify(store), "utf-8");
    return true;
  }
);

handleMainIPC("credential-load", async (_event, provider: string) => {
  const store = loadSecretsFile();
  const key = CREDENTIAL_PREFIX + provider;
  const encrypted = store[key];
  if (!encrypted) return null;
  const decrypted = decryptValue(encrypted);
  if (!decrypted) return null;
  try {
    return JSON.parse(decrypted);
  } catch {
    return null;
  }
});

handleMainIPC("credential-delete", async (_event, provider: string) => {
  const store = loadSecretsFile();
  const key = CREDENTIAL_PREFIX + provider;
  delete store[key];
  fs.writeFileSync(secretsPath, JSON.stringify(store), "utf-8");
  return true;
});

handleMainIPC("credential-list", async () => {
  const store = loadSecretsFile();
  const credentials: Array<{
    provider: string;
    method: string;
    status: string;
    scopes?: string[];
    expires_at?: number;
    metadata?: Record<string, string>;
  }> = [];

  for (const key of Object.keys(store)) {
    if (!key.startsWith(CREDENTIAL_PREFIX)) continue;
    const provider = key.slice(CREDENTIAL_PREFIX.length);
    const decrypted = decryptValue(store[key]);
    if (!decrypted) continue;

    try {
      const cred = JSON.parse(decrypted);
      const now = Date.now();
      const isExpired = cred.expires_at && cred.expires_at < now;
      credentials.push({
        provider,
        method: cred.method || "oauth",
        status: isExpired ? "expired" : "connected",
        scopes: cred.scopes,
        expires_at: cred.expires_at,
        metadata: cred.metadata,
      });
    } catch {
      // Skip corrupted entries
    }
  }

  return credentials;
});

// --- OAuth Provider Registry IPC ---

function nativeAI(): AIConnections {
  if(!aiConnections) throw new Error("AI connections are still starting.");
  return aiConnections;
}
handleMainIPC("ai-status", () => nativeAI().status());
handleMainIPC("ai-list", () => nativeAI().list());
handleMainIPC("ai-sign-in", (_event, provider: unknown, id?: string) => nativeAI().signIn(provider,id));
handleMainIPC("ai-cancel", () => nativeAI().cancel());
handleMainIPC("ai-save-key", (_event, provider: unknown, key: unknown) => nativeAI().saveKey(provider,key));
handleMainIPC("ai-models", (_event, id: string) => nativeAI().models(id));
handleMainIPC("ai-routes", (_event, id: string, model: string) => nativeAI().routes(id,model));
handleMainIPC("ai-activate", (_event, id: string, model: string, serving: string) => nativeAI().activate(id,model,serving));
handleMainIPC("ai-disconnect", (_event, id: string) => nativeAI().disconnect(id));

handleMainIPC("ai-forget", (_event, id: string) => nativeAI().forget(id));

handleMainIPC("ai-import-saved-claude", async () => {
  const encrypted=loadSecretsFile()["anthropic-api-key"];
  const key=encrypted ? decryptValue(encrypted) : null;
  if(!key) throw new Error("No saved Claude key is available. Connect with your current API key.");
  return nativeAI().saveKey("anthropic",key);
});

function nativeWorkspaces():WorkspaceAccess {
  if(!workspaceAccess)throw new Error("Workspace access is still starting.");
  return workspaceAccess;
}
handleMainIPC("workspaces-list",()=>nativeWorkspaces().list());
handleMainIPC("workspaces-choose",(_event,purpose,kind)=>nativeWorkspaces().choose(purpose,kind));
handleMainIPC("workspaces-create",(_event,selection,name,description)=>nativeWorkspaces().create(selection,name,description));
handleMainIPC("workspaces-open",()=>nativeWorkspaces().open());
handleMainIPC("workspaces-activate",(_event,id)=>nativeWorkspaces().activate(id));
handleMainIPC("workspaces-forget",(_event,id)=>nativeWorkspaces().forget(id));


handleMainIPC("task-export",async(_event,request)=>saveTaskExport(nativeWorkspaces(),request,async name=>{
  const result=await dialog.showSaveDialog({defaultPath:name,filters:[{name:"Markdown",extensions:["md"]}],message:"Save the accepted artifact, cited passages and source manifest. Exported copies remain separate from Compass."});
  return result.canceled?null:result.filePath||null;
}));
