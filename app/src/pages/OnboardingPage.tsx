import WorkspaceRecovery from "../components/workspace/WorkspaceRecovery";
import { useState } from "react";
import {
  Compass,
  ArrowRight,
  FolderOpen,
  Loader2,
} from "lucide-react";
import { clsx } from "clsx";
import { useWorkspaceStore } from "../stores/workspace";
import { useWorkspaceManager } from "../stores/workspaceManager";
import { useNavigate } from "react-router-dom";

type Step = "welcome" | "setup" | "launching";

const ONBOARDING_KEY = "compass-onboarding-complete";

export function isOnboardingComplete(): boolean {
  return localStorage.getItem(ONBOARDING_KEY) === "true";
}

export function resetOnboarding(): void {
  localStorage.removeItem(ONBOARDING_KEY);
}

function completeOnboarding(): void {
  localStorage.setItem(ONBOARDING_KEY, "true");
}

export default function OnboardingPage() {
  const [step, setStep] = useState<Step>("welcome");
  const [productName, setProductName] = useState("");
  const [productDesc, setProductDesc] = useState("");
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [workspaceLabel,setWorkspaceLabel]=useState("");
  const [error, setError] = useState("");

  const setWorkspace = useWorkspaceStore((s) => s.setWorkspace);
  const addWorkspaceEntry = useWorkspaceManager((s) => s.addWorkspace);
  const navigate = useNavigate();

  const stepIndex = ["welcome", "setup"].indexOf(step);
  const totalSteps = 2;

  async function handleOpenExisting(){
    setError("");
    try {const selected=await useWorkspaceManager.getState().pickWorkspace();if(!selected)return;
      setWorkspace(selected.id,selected.name,selected.description);completeOnboarding();navigate("/workspace",{replace:true});
    }catch(cause){setError(cause instanceof Error?cause.message:"This workspace could not be opened.");}
  }

  async function handlePickFolder() {
    setError("");
    try {
      const selected = await window.compass.workspaces.choose("workspace","directory");
      if (selected) {setWorkspacePath(selected.id);setWorkspaceLabel(selected.label);}
    } catch {setError("The folder picker could not be opened. Try again.");}
  }

  async function handleFinish() {
    if (!workspacePath || !productName.trim()) return;
    setError(""); setStep("launching");
    try {
      const result=await window.compass.workspaces.create(workspacePath,productName.trim(),productDesc.trim());
      await addWorkspaceEntry({name:result.name,description:result.description,path:result.id});
      setWorkspace(result.id,result.name,result.description);
      completeOnboarding(); navigate("/workspace",{replace:true});
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Workspace could not be created. Try again."); setStep("setup");
    }
  }

  return (
    <div className="min-h-screen bg-compass-bg flex items-center justify-center p-8">
      <div className="w-full max-w-lg">
        {/* Progress */}
        {step !== "launching" && (
          <div className="flex items-center gap-2 mb-8 justify-center">
            {Array.from({ length: totalSteps }, (_, i) => (
              <div
                key={i}
                className={clsx(
                  "w-2.5 h-2.5 rounded-full transition-colors",
                  i <= stepIndex ? "bg-compass-accent" : "bg-compass-border"
                )}
              />
            ))}
          </div>
        )}

        {/* Step: Welcome */}
        {step === "welcome" && (
          <div className="text-center">
            <Compass className="w-16 h-16 text-compass-accent mx-auto mb-6" />
            <h1 className="text-3xl font-bold text-compass-text mb-3">
              Welcome to Compass
            </h1>
            <p className="text-neutral-400 mb-2">
              Evidence, a reviewed decision, then a handoff.
            </p>
            <p className="text-sm text-neutral-500 mb-8 max-w-sm mx-auto">
              Choose a local workspace, import evidence, and connect your own AI account when you are ready to draft.
            </p>
            <button
              onClick={() => setStep("setup")}
              className="inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-compass-accent hover:bg-compass-accent-hover text-white font-medium transition-colors"
            >
              Get Started
              <ArrowRight className="w-4 h-4" />
            </button>
            <button onClick={handleOpenExisting} className="block mx-auto mt-4 text-sm text-compass-accent underline">Open existing workspace</button>
            <WorkspaceRecovery workspace={null} onRestored={completeOnboarding}/>
            {error && <p role="alert" className="mt-4 text-red-400 text-sm">{error}</p>}
          </div>
        )}

        {/* Step: Setup (name + folder) */}
        {step === "setup" && (
          <div>
            <div className="text-center mb-6">
              <FolderOpen className="w-12 h-12 text-compass-accent mx-auto mb-4" />
              <h2 className="text-xl font-semibold text-compass-text mb-2">
                Name Your Product
              </h2>
            </div>

            <div className="space-y-4">
              <div>
                <label htmlFor="product-name" className="block text-sm text-neutral-400 mb-1">
                  Product name
                </label>
                <input
                  id="product-name"
                  type="text"
                  value={productName}
                  onChange={(e) => setProductName(e.target.value)}
                  placeholder="e.g. Acme Dashboard"
                  className="w-full px-3 py-2 rounded-lg bg-compass-card border border-compass-border text-compass-text text-sm placeholder:text-neutral-600 focus:outline-none focus:border-compass-accent"
                />
              </div>
              <div>
                <label htmlFor="product-description" className="block text-sm text-neutral-400 mb-1">
                  Short description
                </label>
                <textarea id="product-description"
                  value={productDesc}
                  onChange={(e) => setProductDesc(e.target.value)}
                  placeholder="What does this product do?"
                  rows={2}
                  className="w-full px-3 py-2 rounded-lg bg-compass-card border border-compass-border text-compass-text text-sm placeholder:text-neutral-600 focus:outline-none focus:border-compass-accent resize-none"
                />
              </div>
              <div>
                <label className="block text-sm text-neutral-400 mb-1">
                  Workspace folder
                </label>
                <button
                  onClick={handlePickFolder}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded-lg bg-compass-card border border-compass-border text-sm hover:border-compass-accent transition-colors"
                >
                  <FolderOpen className="w-4 h-4 text-compass-muted" />
                  <span className={workspacePath ? "text-compass-text" : "text-neutral-600"}>
                    {workspaceLabel || "Select a folder..."}
                  </span>
                </button>
              </div>
            </div>

            {error && <p role="alert" className="mt-4 text-sm text-red-400">{error}</p>}
            <div className="flex justify-between mt-8">
              <button
                onClick={() => setStep("welcome")}
                className="text-sm text-compass-muted hover:text-compass-text"
              >
                Back
              </button>
              <button
                onClick={handleFinish}
                disabled={!productName.trim() || !workspacePath}
                className={clsx(
                  "inline-flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-medium transition-colors",
                  productName.trim() && workspacePath
                    ? "bg-compass-accent hover:bg-compass-accent-hover text-white"
                    : "bg-compass-card text-neutral-600 cursor-not-allowed"
                )}
              >
                Create workspace
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* Launching */}
        {step === "launching" && (
          <div className="text-center">
            <Loader2 className="w-12 h-12 text-compass-accent mx-auto mb-4 animate-spin" />
            <h2 className="text-xl font-semibold text-compass-text mb-2">
              Setting up your workspace...
            </h2>
            <p className="text-sm text-neutral-400">
              Creating local workspace storage.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
