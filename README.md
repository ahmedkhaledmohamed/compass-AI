# Compass

**Cursor for Product Managers.**

[![CI](https://github.com/ahmedkhaledmohamed/compass-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/ahmedkhaledmohamed/compass-AI/actions/workflows/ci.yml)
[![Release](https://github.com/ahmedkhaledmohamed/compass-AI/actions/workflows/release.yml/badge.svg)](https://github.com/ahmedkhaledmohamed/compass-AI/releases)

AI-native product discovery that connects to your product's evidence sources — code, docs, analytics, and user feedback — finds where they disagree, and generates agent-ready specifications.

**[Website](https://ahmedkhaledmohamed.github.io/compass-AI/)** | **[Download](https://github.com/ahmedkhaledmohamed/compass-AI/releases/latest)** | **[Quick Start](#quick-start)**

---

## The Problem

Cursor and Claude Code accelerated how teams build. But someone still has to figure out **what** to build. Product managers juggle strategy docs, user interviews, analytics dashboards, support tickets, and code reality — often reaching different conclusions from each source. Compass reconciles all of them.

## Four Sources of Truth

Every product has four types of evidence that often contradict each other:

| Source | Question | Examples |
|--------|----------|----------|
| **Code** | What *can* happen? | Repository structure, API surface, module age, test coverage |
| **Docs** | What's *expected*? | Strategy docs, PRDs, roadmaps, design specs |
| **Data** | What *is* happening? | Analytics, metrics, crash reports, usage patterns |
| **Judgment** | What *should* happen? | User interviews, support tickets, stakeholder feedback |

When these sources agree, things are working. When they disagree, that's where product opportunities hide.

## How It Works

```
Connect → Ingest → Reconcile → Discover → Specify
```

1. **Connect** your evidence sources (local files, repos, CSVs, markdown)
2. **Ingest** builds a knowledge graph with semantic embeddings
3. **Reconcile** finds conflicts between sources (e.g., strategy says "mobile-first" but code investment says desktop)
4. **Discover** synthesizes ranked opportunities with multi-source corroboration
5. **Specify** generates agent-ready specs that Cursor and Claude Code can execute directly

### What You Get

- **Conflict Report** — Where your sources of truth disagree, ranked by severity and signal strength
- **Opportunity Ranking** — Evidence-grounded recommendations for what to build, with confidence levels
- **Agent-Ready Specs** — Feature specs with task breakdowns, file paths, and acceptance criteria — formatted for Cursor or Claude Code

## Quick Start

### CLI

```bash
# Install
cd engine && pip install -e ".[dev]"

# Run the demo (ingests sample data, finds conflicts, discovers opportunities)
compass demo

# Or use with your own product
export ANTHROPIC_API_KEY=sk-ant-...
compass init "My Product"
compass connect code ./src
compass connect docs ./strategy/
compass connect analytics ./metrics.csv
compass ingest
compass reconcile
compass discover
```

### Desktop App

Download the native app:

- **[macOS (.dmg)](https://github.com/ahmedkhaledmohamed/compass-AI/releases/latest)**
- **[Windows (.exe)](https://github.com/ahmedkhaledmohamed/compass-AI/releases/latest)**
- **[Linux (.AppImage)](https://github.com/ahmedkhaledmohamed/compass-AI/releases/latest)**

The app includes an onboarding wizard — create a workspace, enter your API key, connect sources, and start discovering.

### MCP Integration (Claude Code / Cursor)

```bash
compass mcp install
```

Adds Compass tools to your AI assistant. Ask "what should we build next?" and get evidence-grounded recommendations without leaving your editor.

Available tools: `compass_status`, `compass_ingest`, `compass_reconcile`, `compass_discover`, `compass_specify`, `compass_ask`, `compass_search`, `compass_refresh`.

## Architecture

```
┌─────────────────────────────────────────────┐
│              Interfaces                      │
│  Desktop App  │  CLI  │  MCP Server  │  API │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│           Engine (Python / FastAPI)           │
│                                              │
│  Connectors → Knowledge Graph → Orchestrator │
│  (GitHub,     (ChromaDB +      (Reconciler,  │
│   Docs,       EvidenceStore)    Discoverer,   │
│   Analytics,                    Specifier,    │
│   Interviews,                   Chat)         │
│   Support,                                    │
│   Jira, Slack,                               │
│   Linear, Notion)                            │
└──────────────────────────────────────────────┘
```

- **Engine:** Python 3.11+, FastAPI, ChromaDB vectors, Pydantic models
- **App:** Electron 33, React 19, TypeScript, Vite 6, Zustand, Tailwind CSS
- **LLM:** BYOK (Bring Your Own Key) — uses your Anthropic API key directly. Cloud option available for managed access.

## Chat Modes

Compass includes multiple AI agent modes for different PM workflows:

| Mode | Purpose |
|------|---------|
| **Default** | Evidence-grounded Q&A about your product |
| **Thought Partner** | Brainstorm and explore ideas collaboratively |
| **Technical Analyst** | Understand systems and architecture in PM terms |
| **Devil's Advocate** | Stress-test assumptions and find blind spots |

## Project Structure

```
compass-AI/
├── engine/          # Python engine (FastAPI, connectors, KG, AI pipeline)
│   ├── compass/     # Main package (server, cli, models, engine, connectors)
│   └── tests/       # Engine tests
├── app/             # Electron desktop app (React, TypeScript)
│   ├── src/         # React components, pages, stores
│   └── electron/    # Main process, preload, engine bridge
├── cloud/           # Cloud API (auth, LLM proxy, Stripe billing)
├── demo/            # Sample data for compass demo
├── docs/            # Roadmap, specs, architecture decisions
└── site/            # Landing page (GitHub Pages)
```

## Development

### Prerequisites

- Python 3.11+
- Node.js 20+
- An Anthropic API key (for LLM features)

### Setup

```bash
# Engine
cd engine
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v

# App
cd app
npm ci
npm run dev
```

### Run just the engine

The desktop app configures a fresh local session token automatically. For manual development, set `COMPASS_ENGINE_TOKEN` to a private random value before starting the server and send it as `Authorization: Bearer <token>` on every request, including health checks. The renderer uses the Electron bridge; browser origins are not granted direct API access.

```bash
cd engine && uvicorn compass.server:app --port 9811
```

API docs at http://localhost:9811/docs.

## Philosophy

Compass builds on the **[PM AI Partner Framework](https://github.com/ahmedkhaledmohamed/PM-AI-Partner-Framework)** — the idea that PM work is fundamentally about reconciling four sources of truth, and AI should augment that reconciliation rather than replace human judgment. The framework's agent modes (Thought Partner, Technical Analyst, Devil's Advocate) are embedded directly into Compass's chat interface, giving PMs structured ways to interrogate their product evidence.

Compass is not "AI writes your PRDs." It's "AI helps you find what's true about your product, where the truths conflict, and what to do about it."

## License

MIT
