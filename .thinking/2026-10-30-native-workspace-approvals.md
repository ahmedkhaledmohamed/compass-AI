# Problem Framing
Renderer-provided paths must not authorize workspace writes or source imports. Opening an older workspace also must not silently grant access to its saved source paths.

# Approach
Keep workspace registrations and picker grants in the native process, expose opaque identifiers to the renderer, and check selected file or folder identity before native operations. Require explicit source approval and validate it against the saved configuration before imports.

# Key Decisions
Make picker grants purpose-bound, expiring and consumed after connection. Reject forged paths, symlink storage and changed identities; preserve workspace files when forgetting a registration and retain a busy workspace so generation can still be cancelled.

# Outcome
The isolated increment passes 58 desktop checks, TypeScript checks and the app build. Its engine source is unchanged from the dependency with 195 passing offline engine tests. Later file-access hardening, real native-picker usability and installed-app acceptance remain separate increments and gates.
