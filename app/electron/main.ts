import { app, BrowserWindow, ipcMain, dialog, safeStorage } from "electron";
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
import { registerOAuthIPC, handleOAuthCallback } from "./oauth";
import { getProvider, getAllProviders } from "./oauth-providers";

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

  if (isDev) {
    mainWindow.loadURL("http://localhost:5173");
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// Register compass:// protocol for OAuth callbacks
if (process.defaultApp) {
  // Dev mode: register with the path to electron
  if (process.argv.length >= 2) {
    app.setAsDefaultProtocolClient("compass", process.execPath, [
      path.resolve(process.argv[1]),
    ]);
  }
} else {
  app.setAsDefaultProtocolClient("compass");
}

app.whenReady().then(async () => {
  createWindow();
  registerOAuthIPC();
  registerProviderIPC();

  try {
    await startEngine();
  } catch (err) {
    console.error("[main] Failed to start engine:", err);
  }

  // Auto-updater: check for updates in packaged builds
  if (app.isPackaged) {
    autoUpdater.logger = console;
    autoUpdater.checkForUpdatesAndNotify();
  }
});

// Handle compass:// protocol on macOS (open-url event)
app.on("open-url", (_event, url) => {
  if (url.startsWith("compass://oauth/callback")) {
    handleOAuthCallback(url);
  }
});

// Handle compass:// protocol on Windows/Linux (second-instance event)
app.on("second-instance", (_event, commandLine) => {
  const url = commandLine.find((arg) => arg.startsWith("compass://"));
  if (url && url.startsWith("compass://oauth/callback")) {
    handleOAuthCallback(url);
  }
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
  stopEngine();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

// IPC: Native folder picker
ipcMain.handle("select-directory", async () => {
  const result = await dialog.showOpenDialog({
    properties: ["openDirectory"],
  });
  return result.canceled ? null : result.filePaths[0];
});

// IPC: Native file picker
ipcMain.handle("select-file", async (_event, filters) => {
  const result = await dialog.showOpenDialog({
    properties: ["openFile"],
    filters: filters || [],
  });
  return result.canceled ? null : result.filePaths[0];
});

// IPC: Save file dialog
ipcMain.handle("save-file", async (_event, defaultName: string, content: string) => {
  const result = await dialog.showSaveDialog({
    defaultPath: defaultName,
    filters: [{ name: "Markdown", extensions: ["md"] }],
  });
  if (result.canceled || !result.filePath) return null;
  const fs = await import("fs/promises");
  await fs.writeFile(result.filePath, content, "utf-8");
  return result.filePath;
});

// IPC: Export document to file
ipcMain.handle(
  "export-document",
  async (
    _event,
    defaultName: string,
    content: string,
    format: "md" | "html" | "pdf" | "docx",
  ) => {
    const filterMap: Record<string, Electron.FileFilter[]> = {
      md: [{ name: "Markdown", extensions: ["md"] }],
      html: [{ name: "HTML", extensions: ["html"] }],
      pdf: [{ name: "PDF", extensions: ["pdf"] }],
      docx: [{ name: "Word Document", extensions: ["docx"] }],
    };
    const result = await dialog.showSaveDialog({
      defaultPath: defaultName,
      filters: filterMap[format] || [],
    });
    if (result.canceled || !result.filePath) return null;

    if (format === "docx") {
      // Content is base64-encoded binary
      const fsPromises = await import("fs/promises");
      const buffer = Buffer.from(content, "base64");
      await fsPromises.writeFile(result.filePath, buffer);
    } else if (format === "pdf") {
      // Use a hidden BrowserWindow to render HTML then printToPDF
      const pdfWin = new BrowserWindow({
        show: false,
        width: 800,
        height: 600,
        webPreferences: { offscreen: true },
      });
      await pdfWin.loadURL(
        `data:text/html;charset=utf-8,${encodeURIComponent(content)}`,
      );
      const pdfData = await pdfWin.webContents.printToPDF({
        margins: { marginType: "default" },
        printBackground: true,
      });
      const fsPromises = await import("fs/promises");
      await fsPromises.writeFile(result.filePath, pdfData);
      pdfWin.destroy();
    } else {
      const fsPromises = await import("fs/promises");
      await fsPromises.writeFile(result.filePath, content, "utf-8");
    }
    return result.filePath;
  },
);

// IPC: Capture HTML as PNG screenshot
ipcMain.handle(
  "capture-html-png",
  async (_event, html: string, defaultName: string) => {
    const result = await dialog.showSaveDialog({
      defaultPath: defaultName,
      filters: [{ name: "PNG Image", extensions: ["png"] }],
    });
    if (result.canceled || !result.filePath) return null;

    const captureWin = new BrowserWindow({
      show: false,
      width: 1280,
      height: 800,
      webPreferences: { offscreen: true },
    });
    await captureWin.loadURL(
      `data:text/html;charset=utf-8,${encodeURIComponent(html)}`,
    );
    // Wait for content to render
    await new Promise((r) => setTimeout(r, 1000));
    const image = await captureWin.webContents.capturePage();
    const fsPromises = await import("fs/promises");
    await fsPromises.writeFile(result.filePath, image.toPNG());
    captureWin.destroy();
    return result.filePath;
  },
);

// IPC: Engine API call (proxied through main process via engine-bridge)
ipcMain.handle("engine-call", async (_event, endpoint: string, body?: unknown) => {
  try {
    return await engineFetch(endpoint, body ? { body } : undefined);
  } catch (err) {
    return { status: "error", message: String(err) };
  }
});

// IPC: Engine SSE stream (for chat streaming)
ipcMain.handle(
  "engine-stream",
  async (event, endpoint: string, body?: unknown) => {
    const port = process.env.COMPASS_ENGINE_PORT || "9811";
    const url = `http://localhost:${port}${endpoint}`;

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });

      if (!res.body) return { status: "error", message: "No response body" };

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const data = line.slice(6);
            event.sender.send("engine-stream-data", data);
          } else if (line.startsWith("event: ")) {
            const eventName = line.slice(7).trim();
            // next data line will contain the event data
          }
        }
      }

      event.sender.send("engine-stream-data", JSON.stringify({ done: true }));
      return { status: "ok" };
    } catch (err) {
      return { status: "error", message: String(err) };
    }
  }
);

// IPC: Engine health check
ipcMain.handle("engine-health", async () => {
  try {
    return await engineFetch("/health");
  } catch {
    return { status: "unavailable" };
  }
});

// IPC: Restart engine after crash
ipcMain.handle("engine-restart", async () => {
  try {
    stopEngine();
    await startEngine();
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

ipcMain.handle("secrets-store", async (_event, key: string, value: string) => {
  if (!hasSecureSecretStorage()) {
    throw new Error("Secure credential storage is unavailable. Unlock your OS keychain and try again.");
  }
  const store = loadSecretsFile();
  store[key] = safeStorage.encryptString(value).toString("base64");
  fs.writeFileSync(secretsPath, JSON.stringify(store), "utf-8");
  return true;
});

ipcMain.handle("secrets-load", async (_event, key: string) => {
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

ipcMain.handle("secrets-delete", async (_event, key: string) => {
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

ipcMain.handle(
  "credential-store",
  async (_event, provider: string, credential: unknown) => {
    const store = loadSecretsFile();
    const key = CREDENTIAL_PREFIX + provider;
    store[key] = encryptValue(JSON.stringify(credential));
    fs.writeFileSync(secretsPath, JSON.stringify(store), "utf-8");
    return true;
  }
);

ipcMain.handle("credential-load", async (_event, provider: string) => {
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

ipcMain.handle("credential-delete", async (_event, provider: string) => {
  const store = loadSecretsFile();
  const key = CREDENTIAL_PREFIX + provider;
  delete store[key];
  fs.writeFileSync(secretsPath, JSON.stringify(store), "utf-8");
  return true;
});

ipcMain.handle("credential-list", async () => {
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

function registerProviderIPC(): void {
  ipcMain.handle("provider-get", async (_event, id: string) => {
    return getProvider(id);
  });

  ipcMain.handle("provider-list", async () => {
    return getAllProviders();
  });
}
