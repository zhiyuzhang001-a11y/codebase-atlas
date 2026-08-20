# Read-only MCP interface

Codebase Atlas implements the stable MCP protocol revision `2025-11-25` over stdio. Messages are newline-delimited UTF-8 JSON-RPC. The server exposes only read-only `related_tests` and `impact` tools at this stage and returns both serialized text content and structured content.

Protocol references:

- https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- https://modelcontextprotocol.io/specification/2025-11-25/server/tools

The server starts its shared provider service once per MCP session. If no Codebase Memory daemon exists, the service starts one and owns its shutdown. If a daemon already exists, Atlas reuses it and does not stop a process it does not own.
