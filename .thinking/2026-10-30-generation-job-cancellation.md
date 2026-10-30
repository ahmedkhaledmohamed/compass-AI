# Problem Framing
Generation can finish after cancellation, timeout, an engine restart or a PM edit. Its late result must not overwrite the user's saved work, and reopening the task needs to show what happened.

# Approach
Persist generation job states, deadlines and result revisions. Fence publication inside the artifact transaction, recover interrupted jobs without replay, bound the queue and expose explicit cancellation and progress in the review workspace.

# Key Decisions
Keep cancellation idempotent and reject obsolete revisions or reused operation IDs. Report a draining provider request separately from a saved result: this increment prevents late publication, while transport cancellation is a subsequent provider increment.

# Outcome
The isolated increment passes 157 offline engine tests, 17 desktop policy checks, source lint, TypeScript checks and the app build. Tests cover late output, deadlines, crash recovery and migration backup restoration; live provider behavior and installed-app rollback remain separate gates.
