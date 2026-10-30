# Problem Framing
A source label can change without changing which evidence belongs to it. Refresh and reconnection need stable identities so a renamed source does not duplicate evidence, remove another source or break a saved task's selected references.

# Approach
Persist source identifiers, derive document and evidence identities from source provenance and content, replace evidence by source identity, and carry those identifiers through native approvals, source selection and task snapshots.

# Key Decisions
Preserve identities across rename and reconnect, reject ambiguous or duplicate selections, retain legacy source names for migration, and save source configuration atomically. Saved tasks keep their original evidence versions and are marked stale when selected content changes.

# Outcome
The isolated increment passes 203 offline engine tests, 59 desktop checks, source lint, TypeScript checks and the app build. These checks establish source identity and migration mechanics; subsequent import jobs, filesystem hardening, live connector acceptance and real PM usefulness remain separate increments and gates.
