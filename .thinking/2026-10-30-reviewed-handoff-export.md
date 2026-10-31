# Problem Framing
A PM handoff needs to preserve the accepted direction, saved supporting passages and source versions that were actually reviewed. Exporting an unsaved or obsolete revision would misrepresent that decision.

# Approach
Export canonical accepted task revisions as readable Markdown with a bounded source manifest and exact saved citations. Use a native save dialog, recheck the task after the dialog and write the file atomically.

# Key Decisions
Omit rejected claims, refuse unreviewed artifacts, mark changed sources as needing review and treat saved text as non-executable content. Exclude application credential records, source configuration and arbitrary metadata from the manifest while preserving the selected evidence provenance.

# Outcome
The isolated increment passes 247 offline engine tests, 76 desktop checks, source lint, TypeScript checks and the app build. Tests cover revision conflicts, saved evidence, manifest boundaries and native export failure/cancellation; real PM handoff usefulness and installed-app acceptance remain separate gates.
