# Problem Framing
A PM needs to recover saved evidence versions, task revisions and decisions without replacing the original workspace or silently restoring source-access authority. Recovery must also preserve prior files when input validation or publication fails.

# Approach
Create bounded, checksummed data-only backups of canonical history, validate record identities and references, and restore through a private staging folder into a new child of the chosen parent. Read back the new stores before publishing the restored workspace.

# Key Decisions
Exclude application credential records and live source configuration, require source permissions again, and restore active jobs as interrupted without replay. Preserve the original backup and unrelated folders on failure, and refuse unsafe or corrupt inputs before granting a workspace registration.

# Outcome
The isolated increment passes 265 offline engine tests, 79 desktop checks, source lint, TypeScript checks and the app build. Disposable-workspace tests cover readback, malformed history, cancellation and process-stop behavior; installed-app update/rollback and real PM recovery acceptance remain separate gates.
