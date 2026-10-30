import { ipcMain, type BrowserWindow, type IpcMainInvokeEvent } from "electron";

let trustedWindow: BrowserWindow | null = null;
let rendererURL: URL | null = null;

export function isMainRendererURL(value: string): boolean {
  if (!rendererURL) return false;
  try {
    const candidate = new URL(value);
    if (candidate.origin !== rendererURL.origin || candidate.protocol !== rendererURL.protocol) return false;
    if (rendererURL.protocol === "file:") {
      return candidate.host === rendererURL.host && candidate.pathname === rendererURL.pathname;
    }
    return candidate.protocol === "http:" || candidate.protocol === "https:";
  } catch {
    return false;
  }
}

export function trustMainWindow(window: BrowserWindow, url: string): void {
  trustedWindow = window;
  rendererURL = new URL(url);
  const contents = window.webContents;
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-navigate", (event, target) => {
    if (!isMainRendererURL(target)) event.preventDefault();
  });
  contents.on("will-redirect", (event, target) => {
    if (!isMainRendererURL(target)) event.preventDefault();
  });
  contents.on("destroyed", () => {
    if (trustedWindow === window) {
      trustedWindow = null;
      rendererURL = null;
    }
  });
}

function isTrustedSender(event: IpcMainInvokeEvent): boolean {
  // Electron may destroy native window/frame wrappers while queued IPC is delivered.
  try {
    if (!trustedWindow || trustedWindow.webContents.isDestroyed() || !event.senderFrame) return false;
    const contents = trustedWindow.webContents;
    if (event.sender.id !== contents.id) return false;
    const frame = event.senderFrame;
    const main = contents.mainFrame;
    // Electron serializes a local file frame's origin as file://, while URL.origin is null.
    const expectedOrigin = rendererURL?.protocol === "file:" ? "file://" : rendererURL?.origin;
    return frame.processId === main.processId && frame.routingId === main.routingId &&
      frame.origin === expectedOrigin && isMainRendererURL(frame.url);
  } catch {
    return false;
  }
}

export function handleMainIPC(channel: string, listener: Parameters<typeof ipcMain.handle>[1]): void {
  ipcMain.handle(channel, (event, ...args) => {
    if (!isTrustedSender(event)) throw new Error("Unauthorized IPC sender");
    return listener(event, ...args);
  });
}
