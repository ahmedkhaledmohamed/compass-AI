# Compass Quickstart

Get from zero to your first product discovery in 5 minutes.

## Prerequisites

- Python 3.11+
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com))

## Install

```bash
# Clone and install
git clone https://github.com/ahmedkhaledmohamed/compass-AI.git
cd compass-AI/engine
pip install -e .

# Verify
compass --version
# → compass 0.1.0
```

## Option 1: Interactive Quickstart (recommended)

```bash
# Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."

# Navigate to your product's root directory
cd /path/to/your/product

# Run the interactive quickstart
compass quickstart
```

The quickstart walks you through:
1. Naming your product
2. Connecting sources (code repo, docs, metrics, interviews, support tickets)
3. Ingesting evidence
4. Running discovery

**Expected output** (with 3+ sources connected):
```
✓ Workspace initialized
✓ code:my-repo: 15 items
✓ docs:strategy: 3 items
✓ analytics:metrics: 2 items
42 evidence items ingested.

Running reconciliation + discovery...
  Found 4 conflicts
  Found 5 opportunities

╭─ Fix sync reliability before it becomes existential ─╮
│ ...                                                   │
╰───────────────────────────────────────────────────────╯
```

## Option 2: Step-by-step CLI

```bash
export ANTHROPIC_API_KEY="sk-ant-..."

# 1. Initialize workspace
cd /path/to/your/product
compass init "My Product" --description "What my product does"

# 2. Connect sources (at least 2 for meaningful conflicts)
compass connect github --path ./                    # your codebase
compass connect docs --path ./docs/                 # strategy docs, PRDs
compass connect analytics --path ./data/metrics.csv # usage data
compass connect interviews --path ./research/       # user interview notes
compass connect support --path ./support/tickets.csv # support tickets

# 3. Ingest evidence
compass ingest
# → 42 evidence items from 5 sources

# 4. Find conflicts
compass reconcile
# → 4 conflicts found (2 high severity)

# 5. Discover opportunities
compass discover
# → 5 ranked opportunities

# 6. Generate a spec for the top opportunity
compass specify "Fix sync reliability"
# → Agent-ready feature specification
```

## Option 3: Try the demo first

Not ready with your own data? See Compass in action:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
compass demo
```

Runs the full pipeline on sample data showing a product with strategic misalignment between what leadership says (sync is reliable) and what the data shows (it's not).

## What makes a good setup

**Minimum viable:** 1 code repo + 1 strategy doc = you'll get basic code-vs-docs conflicts.

**Best results:** Sources from all 4 truth types:

| Source Type | What to Connect | Why |
|------------|----------------|-----|
| Code | Your main repo | Shows what the product *can* do |
| Docs | Strategy doc, roadmap, PRD | Shows what's *expected* |
| Data | Metrics CSV, analytics export | Shows what *is* happening |
| Judgment | Interview notes, support tickets | Shows what users *want* |

The more sources you connect, the higher-confidence Compass's discoveries will be.

## Next steps

- `compass doctor` — diagnose any setup issues
- `compass mcp install` — use Compass inside Claude Code ([MCP setup guide](./mcp-setup.md))
- `compass chat` — ask questions grounded in your evidence

## Troubleshooting

```bash
# Check your setup
compass doctor

# Common issues:
# - "API key not configured" → export ANTHROPIC_API_KEY="sk-ant-..."
# - "No sources connected" → compass connect <type> --path <path>
# - "No evidence ingested" → compass ingest
```
