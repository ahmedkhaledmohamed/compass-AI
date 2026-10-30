# Problem Framing
A local import needs to be cancellable and recoverable without losing a failed source's previous evidence. PMs also need to see which files were read, skipped or excluded before relying on the resulting evidence.

# Approach
Persist bounded local import jobs and source coverage, validate complete UTF-8 file reads, and publish each completed source and its job result in one transaction. Expose progress and cancellation through the native workspace boundary and recover interrupted work without replay.

# Key Decisions
Retain completed sources while preserving prior evidence for unfinished or failed sources. Fence publication against cancellation, deadlines and changed source configuration; report partial coverage explicitly and coordinate imports with generation and source changes.

# Outcome
The isolated increment passes 216 offline engine tests, 60 desktop checks, source lint, TypeScript checks and the app build. Tests cover source rollback, late readers, crash recovery, skipped formats and migration backup restoration; live connector behavior, installed rollback and real PM usefulness remain separate gates.
