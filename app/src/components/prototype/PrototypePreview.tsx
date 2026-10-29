import { useState, useRef, useCallback } from "react";
import { RefreshCw } from "lucide-react";
import ViewportToggle, { getViewportWidth } from "./ViewportToggle";
import IterationPanel from "./IterationPanel";
import PrototypeExport from "./PrototypeExport";
import type { Viewport } from "./ViewportToggle";

interface Iteration {
  prompt: string;
  html: string;
}

interface PrototypeData {
  title: string;
  type: string;
  html: string;
  description: string;
  iterations: Iteration[];
  evidence_ids: string[];
}

interface PrototypePreviewProps {
  prototype: PrototypeData;
  workspacePath: string;
  onUpdate?: (prototype: PrototypeData) => void;
}

export default function PrototypePreview({
  prototype,
  workspacePath,
  onUpdate,
}: PrototypePreviewProps) {
  const [viewport, setViewport] = useState<Viewport>("desktop");
  const [currentVersion, setCurrentVersion] = useState(prototype.iterations.length - 1);
  const [isIterating, setIsIterating] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const currentHtml =
    prototype.iterations[currentVersion]?.html ?? prototype.html;

  async function handleIterate(prompt: string) {
    setIsIterating(true);
    try {
      const result = (await window.compass.engine.call("/prototype/iterate", {
        workspace_path: workspacePath,
        html: prototype.html,
        title: prototype.title,
        prototype_type: prototype.type,
        description: prototype.description,
        iteration_prompt: prompt,
        evidence_ids: prototype.evidence_ids,
      })) as { prototype: PrototypeData };

      const updated = result.prototype;
      setCurrentVersion(updated.iterations.length - 1);
      onUpdate?.(updated);
    } catch (err) {
      console.error("Iteration failed:", err);
    } finally {
      setIsIterating(false);
    }
  }

  const handleSelectVersion = useCallback(
    (index: number) => {
      setCurrentVersion(index);
    },
    [],
  );

  function handleRefresh() {
    if (iframeRef.current) {
      iframeRef.current.srcdoc = "";
      requestAnimationFrame(() => {
        if (iframeRef.current) {
          iframeRef.current.srcdoc = currentHtml;
        }
      });
    }
  }

  const viewportWidth = getViewportWidth(viewport);

  return (
    <div className="flex flex-col h-full gap-4">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-lg font-semibold text-compass-text">
            {prototype.title}
          </h3>
          <span className="text-xs px-2 py-0.5 rounded bg-compass-accent/15 text-compass-accent">
            {prototype.type}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <ViewportToggle viewport={viewport} onChange={setViewport} />
          <PrototypeExport html={currentHtml} title={prototype.title} />
          <button
            onClick={handleRefresh}
            className="p-1.5 text-compass-muted hover:text-compass-text transition-colors"
            title="Refresh preview"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Preview iframe */}
      <div className="flex-1 bg-compass-bg/30 rounded-lg border border-compass-border overflow-hidden flex items-start justify-center p-4">
        <div
          className="h-full bg-white rounded-lg overflow-hidden shadow-lg transition-all duration-300"
          style={{ width: viewportWidth, maxWidth: "100%" }}
        >
          <iframe
            ref={iframeRef}
            srcDoc={currentHtml}
            sandbox="allow-scripts"
            className="w-full h-full border-0"
            title={prototype.title}
          />
        </div>
      </div>

      {/* Iteration panel */}
      <IterationPanel
        iterations={prototype.iterations}
        onIterate={handleIterate}
        onSelectVersion={handleSelectVersion}
        currentVersion={currentVersion}
        isLoading={isIterating}
      />
    </div>
  );
}

export type { PrototypeData };
