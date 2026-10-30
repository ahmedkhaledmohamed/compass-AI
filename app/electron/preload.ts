import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("compass", {
  ai: {
    status: () => ipcRenderer.invoke("ai-status"),
    list: () => ipcRenderer.invoke("ai-list"),
    signIn: (provider: string, id?: string) => ipcRenderer.invoke("ai-sign-in",provider,id),
    cancel: () => ipcRenderer.invoke("ai-cancel"),
    importSavedClaude: () => ipcRenderer.invoke("ai-import-saved-claude"),
    saveKey: (provider: string, key: string) => ipcRenderer.invoke("ai-save-key",provider,key),
    models: (id: string) => ipcRenderer.invoke("ai-models",id),
    routes: (id: string, model: string) => ipcRenderer.invoke("ai-routes",id,model),
    activate: (id: string, model: string, serving: string) => ipcRenderer.invoke("ai-activate",id,model,serving),
    disconnect: (id: string) => ipcRenderer.invoke("ai-disconnect",id),
    forget: (id: string) => ipcRenderer.invoke("ai-forget",id),
  },
  engine: {
    call: (endpoint: string, body?: unknown) =>
      ipcRenderer.invoke("engine-call", endpoint, body),
    health: () => ipcRenderer.invoke("engine-health"),
    restart: () => ipcRenderer.invoke("engine-restart"),
    stream: (endpoint: string, body?: unknown) =>
      ipcRenderer.invoke("engine-stream", endpoint, body),
    onStreamData: (callback: (data: string) => void) => {
      const handler = (_event: unknown, data: string) => callback(data);
      ipcRenderer.on("engine-stream-data", handler);
      return () => ipcRenderer.removeListener("engine-stream-data", handler);
    },
    onStatus: (callback: (data: { state: string; message: string }) => void) => {
      const handler = (_event: unknown, data: { state: string; message: string }) => callback(data);
      ipcRenderer.on("engine-status", handler);
      return () => ipcRenderer.removeListener("engine-status", handler);
    },
  },
  app: {
    selectDirectory: () => ipcRenderer.invoke("select-directory"),
    selectFile: (filters?: { name: string; extensions: string[] }[]) =>
      ipcRenderer.invoke("select-file", filters),
    saveFile: (defaultName: string, content: string) =>
      ipcRenderer.invoke("save-file", defaultName, content),
    exportDocument: (
      defaultName: string,
      content: string,
      format: "md" | "html" | "pdf" | "docx",
    ) => ipcRenderer.invoke("export-document", defaultName, content, format),
    captureHtmlPng: (html: string, defaultName: string) =>
      ipcRenderer.invoke("capture-html-png", html, defaultName),
  },
  secrets: {
    store: (key: string, value: string) =>
      ipcRenderer.invoke("secrets-store", key, value),
    load: (key: string) => ipcRenderer.invoke("secrets-load", key),
    delete: (key: string) => ipcRenderer.invoke("secrets-delete", key),
  },
  credentials: {
    store: (provider: string, credential: unknown) =>
      ipcRenderer.invoke("credential-store", provider, credential),
    load: (provider: string) => ipcRenderer.invoke("credential-load", provider),
    delete: (provider: string) =>
      ipcRenderer.invoke("credential-delete", provider),
    list: () => ipcRenderer.invoke("credential-list"),
  },
  oauth: {
    start: (providerConfig: unknown) =>
      ipcRenderer.invoke("oauth-start", providerConfig),
    refresh: (providerConfig: unknown, refreshToken: string) =>
      ipcRenderer.invoke("oauth-refresh", providerConfig, refreshToken),
  },
  providers: {
    get: (id: string) => ipcRenderer.invoke("provider-get", id),
    list: () => ipcRenderer.invoke("provider-list"),
  },
});
