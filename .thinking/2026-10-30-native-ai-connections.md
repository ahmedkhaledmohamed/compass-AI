# Problem Framing
Individual PMs need to connect their own AI accounts, choose a model explicitly and keep billing and credentials tied to that selection. A desktop session must not silently restore another account from environment credentials after sign-out.

# Approach
Keep encrypted credentials and authorization callbacks in the native process, expose connection metadata through trusted IPC, and coordinate account selection, refresh and sign-out with generation readiness. Add browser authorization paths and explicit API-key entry where supported by the selected connection mode.

# Key Decisions
Validate callback state, challenge binding and signed identities; use revision checks so an old refresh cannot resurrect signed-out credentials. Require secure storage and explicit model and serving-route selection, keep credentials out of renderer results, and retain both existing IPC and new connection checks in CI.

# Outcome
The isolated increment passes 194 offline engine tests, 42 desktop checks, source lint, TypeScript checks and the app build. Tests use fictional credentials and controlled authorization responses; live account entitlement, browser-provider acceptance, OS credential encryption and later release hardening remain separate gates.
