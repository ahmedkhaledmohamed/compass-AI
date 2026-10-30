# Problem Framing
The desktop release needs a focused evidence-to-reviewed-handoff workflow and a smaller privileged API surface. Removing older routes must preserve saved documents and avoid overwriting an existing workspace configuration.

# Approach
Restrict renderer navigation and native engine calls to the release workflow, remove deferred bridges from the preload interface, retain read-only review and Markdown export for saved artifacts, and make workspace initialization refuse an existing configuration.

# Key Decisions
Reject deferred or encoded endpoint variants before transport, retain human-selected file exports, and preserve original artifact files. Desktop workspace creation no longer initializes a Git repository implicitly.

# Outcome
The isolated increment passes 195 offline engine tests, 45 desktop checks, source lint, TypeScript checks and the app build. Checks cover the release boundary and existing-config preservation; later native workspace approvals, installed-app acceptance and real PM usefulness remain separate increments and gates.
