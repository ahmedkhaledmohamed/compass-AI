# Problem Framing
A generation deadline must bound the whole provider response, cancellation must close pending I/O, and malformed or partial responses must never become accepted draft content. Account errors and usage also need accurate reporting without automatic fallback or retry.

# Approach
Use fixed provider origins with bounded cancellable transport, validate each supported response protocol and completion state, classify safe error codes, and preserve returned model and usage metadata with the task result.

# Key Decisions
Make one attempt against the explicitly selected account, model and serving route. Reject incomplete streams, tool requests, refusals and oversized output; represent missing usage as unknown and retain known usage for failed output without accepting its text.

# Outcome
The isolated increment passes 191 offline engine tests, 17 desktop policy checks, source lint, TypeScript checks and the app build. Fictional protocol fixtures and a real loopback socket verify cancellation mechanics; real account entitlement, provider quality and installed-app acceptance remain separate gates.
