# Read-only MCP interface

Codebase Atlas implements the stable MCP protocol revision `2025-11-25` over stdio. Messages are newline-delimited UTF-8 JSON-RPC. The server exposes read-only `definition`, `references`, `callers`, `callees`, `related_tests`, `impact`, and `analyze_change` tools and returns both serialized text content and structured content.

Protocol references:

- https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- https://modelcontextprotocol.io/specification/2025-11-25/server/tools

The server starts its shared provider service once per MCP session. If no Codebase Memory daemon exists, the service starts one and owns its shutdown. If a daemon already exists, Atlas reuses it and does not stop a process it does not own.

`analyze_change` is the task-oriented entry point. Give it an exact symbol plus
path/owner when needed, or the deterministic `Owner.member` shorthand. It
resolves definition first and stops on ambiguity, then runs the necessary
primitive queries under one shared deadline. Its Change Brief preserves
provenance and reports `complete`, `partial`, `not_run`, or `error` for every
subquery. It does not interpret arbitrary natural language or modify source.

Only one local Atlas session may own the upstream Provider at a time. Reuse one
MCP session for daily work and close a running UI before starting a separate MCP
client. A competing process returns explicit `provider_busy` truncation after a
short wait instead of consuming the full query budget.

Budget-truncated exact TypeScript `references` results may return an opaque
`truncation.continuation`. Call `references` again with that token and the same
symbol/path/owner to retrieve the next exact page without rerunning the compiler.
The token is valid only in the MCP session that issued it; every use revalidates
the repository source fingerprint.
