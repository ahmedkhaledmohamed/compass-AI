import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Settings, BarChart3, RotateCcw } from "lucide-react";
import { useSettingsStore } from "../stores/settings";
import { resetOnboarding } from "./OnboardingPage";
import AIConnections from "../components/settings/AIConnections";

export default function SettingsPage() {
  const navigate = useNavigate();
  const { tokenUsage, fetchUsage, loadSettings } =
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

      <AIConnections />

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

