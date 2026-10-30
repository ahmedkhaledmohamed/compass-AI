# Problem Framing
Evidence persistence must preserve stable identities and readable history when a write, migration or search-index operation fails. Independent JSON and vector writes cannot establish an atomic canonical record.

# Approach
Use checksummed immutable SQLite snapshots with a transactional active pointer as the source of record. Preserve legacy evidence during migration and treat the search index as a rebuildable cache.

# Key Decisions
Keep historical snapshots available for existing references when active evidence is cleared. Report corrupt or unsupported canonical data explicitly and retain unknown legacy dates as unknown.

# Outcome
The standalone branch passes 110 offline engine tests, 17 inherited desktop policy checks, typechecking, the app build and source lint. Physical data deletion and failed-fetch import replacement are separate workflow increments.
