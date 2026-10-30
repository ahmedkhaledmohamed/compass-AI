# Problem Framing
A failed refresh must not erase a source's last good evidence or remove evidence from another configured source. Partial imports also need to report their actual outcome before any synthesis starts.

# Approach
Fetch and validate a complete source before transactionally replacing its active evidence. Use this path across the HTTP API, command line, MCP tools and discovery workflow, and authenticate API test clients explicitly.

# Key Decisions
Treat inaccessible, empty, unreadable or invalid imports as failures that preserve prior evidence. Return partial or error outcomes, make command failures explicit, and stop discovery before model calls when an import fails.

# Outcome
The isolated increment passes 124 offline engine tests and source lint. These checks cover import preservation and source isolation; live connector behavior, provider quality and installed-app acceptance remain separate gates.
