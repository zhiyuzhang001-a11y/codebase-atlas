# Security policy

## Supported versions

Security fixes are evaluated against the latest private release only. There is
no long-term-support branch or response-time guarantee. Older releases may be
used to reproduce a regression but should not be assumed to receive fixes.

## Reporting a vulnerability

Do not put exploit details, sensitive repository content, credentials, or local
paths in a public issue. Use GitHub's private vulnerability-reporting or security
advisory channel when it is available. If no private channel is available,
contact the repository maintainer privately through the repository owner before
disclosure. Include the affected version, platform, minimal reproduction, impact,
and whether untrusted source/configuration is required.

This project is currently private. A public reporting channel must be verified
before any public release; this file does not promise a response deadline.

## Security boundaries

Codebase Atlas reads user-selected local repositories and launches explicitly
configured local Provider processes. It does not sandbox repositories or those
Providers. Treat repositories, executable paths, configuration files, and MCP or
JSON-lines clients according to their trust level.

Security-relevant areas include command injection through configuration, path or
symlink errors in repair/cleanup, unsafe Provider/MCP/SQLite parsing, unintended
source disclosure, surviving processes, and incomplete evidence presented as
exact. Maintenance commands are dry-run or read-only by default where documented,
but these controls are not a general sandbox.
