# Problem Framing
Desktop inference should not require passing provider credentials into the Python engine. Each generation also needs to remain bound to the chosen account, model, workspace, task and revision while cancellation can stop the actual provider I/O.

# Approach
Issue short-lived, single-use native inference grants for a specific generation request. Keep provider authorization in the native process, pass only a bound capability and connection metadata to the engine, and persist public connection provenance with the result.

# Key Decisions
Reject forged or mismatched bindings, browser-origin requests and replayed grants. Revoke capabilities on reset, close or expiry, validate the current connection before I/O, retain fixed provider routes and preserve typed errors and known usage without exposing credentials.

# Outcome
The isolated increment passes 240 offline engine tests, 69 desktop checks, source lint, TypeScript checks and the app build. Tests use fictional credentials and controlled provider responses to exercise binding, revocation, deadlines and cancellation; live account acceptance, OS encryption and model quality remain separate gates.
