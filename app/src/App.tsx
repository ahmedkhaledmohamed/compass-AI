import { useEffect, useState } from "react";
import { HashRouter, Routes, Route, Navigate } from "react-router-dom";
import AppLayout from "./components/layout/AppLayout";
import ErrorBoundary from "./components/ErrorBoundary";
import WorkPage from "./pages/WorkPage";
import WorkspacePage from "./pages/WorkspacePage";
import EvidencePage from "./pages/EvidencePage";
import ConflictsPage from "./pages/ConflictsPage";
import DiscoverPage from "./pages/DiscoverPage";
import ChatPage from "./pages/ChatPage";
import DashboardPage from "./pages/DashboardPage";
import DocumentsPage from "./pages/DocumentsPage";
import PresentationsPage from "./pages/PresentationsPage";
import PrototypesPage from "./pages/PrototypesPage";
import SettingsPage from "./pages/SettingsPage";
import OnboardingPage, { isOnboardingComplete } from "./pages/OnboardingPage";
import LoginPage from "./pages/LoginPage";
import { useSettingsStore } from "./stores/settings";

type EngineState = "starting" | "setup" | "ready" | "error" | "crashed" | null;

function EngineStatusBanner() {
  const [engineState, setEngineState] = useState<EngineState>(null);
  const [message, setMessage] = useState("");
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    const cleanup = window.compass?.engine.onStatus?.((data) => {
      setEngineState(data.state as EngineState);
      setMessage(data.message);
      if (data.state === "ready") {
        // Auto-dismiss after 2 seconds
        setTimeout(() => setEngineState(null), 2000);
      }
    });
    return cleanup;
  }, []);

  async function handleRestart() {
    setRestarting(true);
    setEngineState("starting");
    setMessage("Restarting engine...");
    try {
      await window.compass?.engine.restart();
    } catch {
      setEngineState("error");
      setMessage("Failed to restart engine.");
    } finally {
      setRestarting(false);
    }
  }

  if (!engineState || engineState === "ready") return null;

  const isError = engineState === "crashed" || engineState === "error";

  return (
    <div
      className={`fixed top-0 left-0 right-0 z-50 px-4 py-2.5 text-center text-sm font-medium ${
        isError
          ? "bg-red-500/90 text-white"
          : "bg-compass-accent/90 text-white"
      }`}
    >
      <span>{message}</span>
      {isError && (
        <button
          onClick={handleRestart}
          disabled={restarting}
          className="ml-3 px-3 py-0.5 rounded bg-white/20 hover:bg-white/30 text-white text-xs font-medium transition-colors disabled:opacity-50"
        >
          {restarting ? "Restarting..." : "Restart Engine"}
        </button>
      )}
    </div>
  );
}

function AppWithStartup() {
  const loadSettings = useSettingsStore((s) => s.loadSettings);
  const pushToEngine = useSettingsStore((s) => s.pushToEngine);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    loadSettings().then(() => pushToEngine()).finally(() => setReady(true));
  }, [loadSettings, pushToEngine]);

  if (!ready) return null;

  const needsOnboarding = !isOnboardingComplete();

  return (
    <Routes>
      <Route path="/onboarding" element={<OnboardingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route element={<AppLayout />}>
        <Route path="/" element={<Navigate to={needsOnboarding ? "/onboarding" : "/workspace"} replace />} />
        <Route path="/work/:taskId?" element={<WorkPage />} />
        <Route path="/workspace" element={<WorkspacePage />} />
        <Route path="/evidence" element={<EvidencePage />} />
        <Route path="/conflicts" element={<ConflictsPage />} />
        <Route path="/discover" element={<DiscoverPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/presentations" element={<PresentationsPage />} />
        <Route path="/prototypes" element={<PrototypesPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <HashRouter>
        <EngineStatusBanner />
        <AppWithStartup />
      </HashRouter>
    </ErrorBoundary>
  );
}
