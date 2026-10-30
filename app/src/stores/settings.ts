import { create } from "zustand";

export type LLMProvider = "compass" | "byok";

interface SettingsState {
  provider: LLMProvider;
  apiKey: string;
  model: string;
  tokenUsage: { input: number; output: number; total: number; cost: string };

  setProvider: (p: LLMProvider) => void;
  setApiKey: (k: string) => void;
  setModel: (m: string) => void;
  setTokenUsage: (u: { input: number; output: number; total: number; cost: string }) => void;
  loadSettings: () => Promise<void>;
  saveSettings: () => void;
  pushToEngine: () => Promise<void>;
  fetchUsage: () => Promise<void>;
}

const SETTINGS_KEY = "compass-settings";
const SECRET_KEY_NAME = "anthropic-api-key";

export const useSettingsStore = create<SettingsState>((set, get) => ({
  provider: "byok",
  apiKey: "",
  model: "claude-sonnet-4-20250514",
  tokenUsage: { input: 0, output: 0, total: 0, cost: "$0.00" },

  setProvider: (provider) => {
    set({ provider });
    get().saveSettings();
    get().pushToEngine();
  },
  setApiKey: (apiKey) => {
    set({ apiKey });
    get().saveSettings();
    get().pushToEngine();
  },
  setModel: (model) => {
    set({ model });
    get().saveSettings();
    get().pushToEngine();
  },
  setTokenUsage: (tokenUsage) => set({ tokenUsage }),

  loadSettings: async () => {
    try {
      if (window.compass?.ai) return;
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (raw) {
        const data = JSON.parse(raw);
        set({
          provider: data.provider || "byok",
          model: data.model || "claude-sonnet-4-20250514",
        });
      }
      // Load API key from OS keychain via safeStorage
      const savedKey = await window.compass?.secrets.load(SECRET_KEY_NAME);
      if (savedKey) {
        set({ apiKey: savedKey });
      } else {
        // Migrate from localStorage if present (one-time migration)
        const legacyKey = localStorage.getItem("compass-api-key");
        if (legacyKey) {
          set({ apiKey: legacyKey });
          await window.compass?.secrets.store(SECRET_KEY_NAME, legacyKey);
          localStorage.removeItem("compass-api-key");
        }
      }
    } catch {
      // ignore
    }
  },

  saveSettings: () => {
    const { provider, model, apiKey } = get();
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ provider, model }));
    // Persist API key to OS keychain when using BYOK
    if (provider === "byok" && apiKey) {
      window.compass?.secrets.store(SECRET_KEY_NAME, apiKey);
    }
  },

  pushToEngine: async () => {
    const { provider, apiKey, model } = get();
    try {
      if (window.compass?.ai) return;
      await window.compass?.engine.call("/configure", {
        api_key: provider === "byok" ? apiKey : "",
        model,
        provider: provider === "byok" ? "anthropic" : "compass",
      });
    } catch {
      // Engine may not be running yet — ignore silently
    }
  },

  fetchUsage: async () => {
    try {
      const res = (await window.compass?.engine.call("/usage")) as {
        status: string;
        session_tokens: { input: number; output: number };
        total_tokens: number;
        total_cost_estimate: string;
      };
      if (res?.status === "ok") {
        set({
          tokenUsage: {
            input: res.session_tokens.input,
            output: res.session_tokens.output,
            total: res.total_tokens,
            cost: res.total_cost_estimate,
          },
        });
      }
    } catch {
      // ignore
    }
  },
}));
