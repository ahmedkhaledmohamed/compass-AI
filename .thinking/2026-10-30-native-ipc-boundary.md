# Problem Framing
Privileged desktop actions need to distinguish the trusted main application frame from other windows, child frames and navigated content. Generated prototypes must not inherit the application's origin authority.

# Approach
Route native handlers through a shared sender guard that checks the window, main-frame identity, origin and renderer URL. Deny unexpected navigation and new windows, and give prototype frames an opaque origin.

# Key Decisions
Handle destroyed native window or frame wrappers as untrusted callers. Prototype scripts retain their existing execution capability within the opaque frame; this increment addresses native caller and origin isolation.

# Outcome
The standalone branch passes 17 desktop policy checks, typechecking, the app build and source lint. Its engine source is unchanged from the separately verified authentication base, which passes 95 offline engine tests.
