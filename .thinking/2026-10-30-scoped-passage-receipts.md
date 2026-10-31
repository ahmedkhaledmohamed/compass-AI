# Problem Framing
A bounded model context cannot include every saved source passage. PMs need to know which evidence was actually selected for a draft, reopen its saved version and distinguish a partial selection from a complete source review.

# Approach
Retrieve passages only from the task's selected immutable snapshot, retain document versions and exact offsets, and persist a receipt of the passages supplied to generation. Expose local passage search and saved passage inspection alongside the review workflow.

# Key Decisions
Make retrieval errors and empty matches explicit, preserve accepted brief support when preparing a handoff, and validate candidate citations against the supplied passages. Report context and import omissions without treating matching quotes as proof of semantic support.

# Outcome
The isolated increment passes 230 offline engine tests, 60 desktop checks, source lint, TypeScript checks and the app build. These checks establish scoped retrieval and receipt mechanics; live model quality, human claim assessment and installed-app acceptance remain separate gates.
