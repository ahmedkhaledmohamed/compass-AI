# Problem Framing
The desktop production work requires local engine endpoints to reject callers that do not belong to the current native app session. Loopback binding alone does not establish that authority.

# Approach
Create a private token for each engine launch and attach it to native health, request and streaming calls. Require the matching bearer token at the engine boundary and remove permissive browser-origin access.

# Key Decisions
Keep the token out of renderer storage and rotate it when the engine restarts. Reject missing engine configuration and stale tokens before endpoint operations run.

# Outcome
The standalone branch passes 95 offline engine tests, three engine-bridge checks, five credential-storage checks, typechecking, the app build and source lint. These checks establish the session-authentication increment; real-provider and installed-app acceptance remain separate.
