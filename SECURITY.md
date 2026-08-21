# Security policy

## Supported versions

Security fixes are evaluated against the latest release only. There is
no long-term-support branch or response-time guarantee. Older releases may be
used to reproduce a regression but should not be assumed to receive fixes.

## Reporting a vulnerability

Do not put exploit details, sensitive repository content, credentials, or local
paths in a public issue. Use [GitHub private vulnerability reporting](https://github.com/zhiyuzhang001-a11y/codebase-atlas/security/advisories/new).
Include the affected version, platform, minimal reproduction, impact, and whether
untrusted source/configuration is required.

The channel is private to repository maintainers. This project does not promise a
response deadline.

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
