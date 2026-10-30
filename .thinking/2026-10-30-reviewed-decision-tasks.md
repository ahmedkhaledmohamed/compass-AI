# Problem Framing
A PM needs to preserve the selected evidence, corrections and chosen direction behind a brief, then resume that work without a later response overwriting intervening edits. A generated draft alone does not establish a reviewed decision.

# Approach
Persist workspace-scoped tasks and revision history, bind drafts to saved evidence snapshots, validate quoted passages and offsets, and expose explicit claim review, correction, acceptance and handoff actions.

# Key Decisions
Require human review and a recorded direction before accepting an artifact or drafting its handoff. Reject obsolete saves and citations outside the selected snapshot, retain original evidence when sources change, and authenticate all task endpoints before operations run.

# Outcome
The isolated increment passes 146 offline engine tests, 17 desktop policy checks, source lint, TypeScript checks and the app build. These checks establish the reviewed-task foundation; cancellation, later desktop refinements, live model quality and installed-app acceptance remain separate increments and gates.
