/** Native-only, atomic encrypted records. This file is never exposed through IPC. */
import fs from "fs";
import path from "path";
import { randomUUID } from "crypto";

export type AIProvider = "chatgpt" | "openrouter" | "anthropic" | "huggingface";
export interface AIConnection {
  id: string;
  revision: number;
  provider: AIProvider;
  label: string;
  clientId?: string;
  issuer?: string;
  subject?: string;
  oidcNonce?: string;
  accessToken: string;
  refreshToken?: string;
  idToken?: string;
  expiresAt?: number;
  scopes: string[];
  inferenceEnabled: boolean;
  status: "connected" | "signed_out" | "reconnect";
  model: string;
  servingProvider: string;
}
export interface VaultData {
  version: 1;
  hostId: string;
  managed: boolean;
  active: string | null;
  connections: AIConnection[];
}
interface Encryption {
  available(): boolean;
  encrypt(value: string): Buffer;
  decrypt(value: Buffer): string;
}
export class AIVault {
  constructor(private file: string, private encryption: Encryption) {}
  requireEncryption() {
    if (!this.encryption.available()) throw new Error("Unlock your OS keychain before connecting an AI account. Secure storage is unavailable.");
  }
  read(): VaultData {
    this.requireEncryption();
    if (!fs.existsSync(this.file)) return { version: 1, hostId: `urn:uuid:${randomUUID()}`, managed: false, active: null, connections: [] };
    try {
      if (fs.lstatSync(this.file).isSymbolicLink() || fs.statSync(this.file).size > 1_000_000) throw new Error();
      const data = JSON.parse(this.encryption.decrypt(fs.readFileSync(this.file))) as VaultData;
      if (data.version !== 1 || !/^urn:uuid:[a-f0-9-]{36}$/.test(data.hostId) || !Array.isArray(data.connections) || data.connections.length > 32 || typeof data.managed !== "boolean") throw new Error();
      for (const record of data.connections) {
        if (!/^[a-f0-9]{32}$/.test(record.id) || !["chatgpt","openrouter","anthropic","huggingface"].includes(record.provider) || !Number.isSafeInteger(record.revision) || record.revision < 1 || typeof record.accessToken !== "string" || !Array.isArray(record.scopes)) throw new Error();
      }
      if (data.active !== null && !data.connections.some(record => record.id === data.active)) throw new Error();
      return data;
    } catch { throw new Error("Saved AI credentials could not be opened. Restore the encrypted credential store or unlock its original OS keychain; it has not been overwritten."); }
  }
  write(data: VaultData) {
    this.requireEncryption();
    const encrypted = this.encryption.encrypt(JSON.stringify(data));
    if(encrypted.length>1_000_000) throw new Error("The connection store reached its size limit. Forget an unused signed-out connection first.");
    fs.mkdirSync(path.dirname(this.file), { recursive: true, mode: 0o700 });
    const temporary = `${this.file}.${randomUUID()}.tmp`;
    let fd: number | undefined;
    try {
      fd = fs.openSync(temporary, "wx", 0o600);
      fs.writeFileSync(fd, encrypted);
      fs.fsyncSync(fd);
      fs.closeSync(fd); fd = undefined;
      fs.renameSync(temporary, this.file);
    } finally {
      if (fd !== undefined) fs.closeSync(fd);
      if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    }
  }
  update(id: string, revision: number, change: (record: AIConnection) => void): AIConnection {
    const data = this.read();
    const record = data.connections.find(item => item.id === id);
    if (!record || record.revision !== revision) throw new Error("This connection changed. Reload it before trying again.");
    change(record); record.revision++;
    this.write(data);
    return record;
  }
}
export function publicConnection(record: AIConnection) {
  return { id: record.id, provider: record.provider, label: record.label, status: record.status,
    inferenceEnabled: record.inferenceEnabled, model: record.model, servingProvider: record.servingProvider,
    billing: record.provider === "chatgpt" ? "ChatGPT plan permission" : record.provider === "anthropic" ? "Claude API account" : `${record.provider === "openrouter" ? "OpenRouter" : "Hugging Face"} account credits` };
}
