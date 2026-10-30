import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Settings, Key, Cpu, BarChart3, RotateCcw, Shield, LinkIcon } from "lucide-react";
import { clsx } from "clsx";
import { useSettingsStore } from "../stores/settings";
import { resetOnboarding } from "./OnboardingPage";
import ConnectedAccounts from "../components/settings/ConnectedAccounts";

const MODELS = [
  { id: "claude-sonnet-4-20250514", label: "Claude Sonnet 4" },
  { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" },
  { id: "claude-opus-4-6", label: "Claude Opus 4.6" },
];

export default function SettingsPage() {
  const navigate = useNavigate();
  const { apiKey, model, tokenUsage, setApiKey, setModel, fetchUsage, loadSettings } =
    useSettingsStore();

  useEffect(() => {
    loadSettings();
    fetchUsage();
  }, []);

  return (
    <div className="p-8 max-w-2xl">
      <div className="flex items-center gap-3 mb-8">
        <Settings className="w-6 h-6 text-compass-accent" />
        <h1 className="text-2xl font-semibold text-compass-text">Settings</h1>
      </div>

      {/* Token usage */}
      <Section icon={BarChart3} title="Session Usage">
        <div className="grid grid-cols-3 gap-3">
          <UsageStat label="Input Tokens" value={tokenUsage.input.toLocaleString()} />
          <UsageStat label="Output Tokens" value={tokenUsage.output.toLocaleString()} />
          <UsageStat label="Est. Cost" value={tokenUsage.cost} />
        </div>
        <button
          onClick={fetchUsage}
          className="mt-3 text-xs text-compass-accent hover:text-compass-accent-hover transition-colors"
        >
          Refresh
        </button>
      </Section>

      {/* API Key */}
      <Section icon={Key} title="Anthropic API Key">
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="sk-ant-..."
          className="w-full px-3 py-2 rounded-lg bg-compass-card border border-compass-border text-sm text-compass-text placeholder:text-neutral-600 focus:outline-none focus:ring-1 focus:ring-compass-accent font-mono"
        />
        <div className="flex items-start gap-2 mt-2">
          <Shield className="w-3.5 h-3.5 text-green-400 mt-0.5 shrink-0" />
          <p className="text-xs text-neutral-400">
            Encrypted with your OS keychain. Sent directly to Anthropic — never to Compass servers.
          </p>
        </div>
      </Section>

      {/* Connected Accounts */}
      <Section icon={LinkIcon} title="Connected Accounts">
        <ConnectedAccounts />
      </Section>

      {/* Reset onboarding */}
      <Section icon={RotateCcw} title="Onboarding">
        <button
          onClick={() => {
            resetOnboarding();
            navigate("/onboarding");
          }}
          className="px-4 py-2 rounded-lg border border-compass-border text-sm text-compass-muted hover:text-compass-text hover:border-compass-accent/20 transition-colors"
        >
          Reset Onboarding Wizard
        </button>
      </Section>

      {/* Model selector */}
      <Section icon={Cpu} title="Model">
        <div className="space-y-2">
          {MODELS.map((m) => (
            <button
              key={m.id}
              onClick={() => setModel(m.id)}
              className={clsx(
                "w-full flex items-center gap-3 px-4 py-3 rounded-lg border text-left text-sm transition-colors",
                model === m.id
                  ? "border-compass-accent/50 bg-compass-accent/5 text-compass-text"
                  : "border-compass-border bg-compass-card text-compass-muted hover:text-compass-text hover:border-compass-accent/20"
              )}
            >
              <div
                className={clsx(
                  "w-3 h-3 rounded-full border-2",
                  model === m.id ? "border-compass-accent bg-compass-accent" : "border-compass-border"
                )}
              />
              {m.label}
              <span className="text-xs text-compass-muted ml-auto font-mono">{m.id}</span>
            </button>
          ))}
        </div>
      </Section>
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-8">
      <div className="flex items-center gap-2 mb-3">
        <Icon className="w-4 h-4 text-compass-muted" />
        <h2 className="text-sm font-medium text-compass-text">{title}</h2>
      </div>
      {children}
    </div>
  );
}

function UsageStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-compass-card border border-compass-border p-3">
      <p className="text-lg font-semibold text-compass-text">{value}</p>
      <p className="text-xs text-compass-muted">{label}</p>
    </div>
  );
}

