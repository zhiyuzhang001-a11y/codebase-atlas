# Privacy and local data

## Product behavior

Codebase Atlas is a local command-line and stdio MCP product. It has no Atlas
account, hosted backend, analytics, advertising, or telemetry. Atlas does not
upload repository source or query results to an Atlas service.

Atlas reads the user-selected repository and configuration. It stores indexes,
state, logs, Serena metadata/home data, and Provider databases under the
configured Atlas data directory (by default below
`~/.local/share/codebase-atlas/`). Project configuration may contain local
absolute paths.

## Processes and network access

Atlas launches configured Codebase Memory and Serena runtimes and invokes Node.js
for the packaged TypeScript analyzer. These are separate projects with their own
behavior and licenses. On first TypeScript semantic use, Serena may use npm to
provision its pinned managed language server. Package installation, cloning,
release download, and Provider dependency provisioning can therefore use the
network; normal Atlas query/index logic does not require an Atlas network service.

Review configured Provider versions and policies before processing sensitive
repositories. Atlas does not make an untrusted Provider safe.
Guided `onboard` never downloads or installs Node.js or Provider dependencies;
without `--apply` it also creates no project configuration or index data.

## Retention and removal

Uninstalling the Python package does not remove configuration or indexed data.
Use `codebase-atlas inspect` and dry-run `codebase-atlas clean` to identify state,
then remove the confirmed project configuration/data directory according to local
retention requirements. `clean` intentionally retains live databases and current
recovery/log generations; it is not a complete uninstall command. Atlas does not
modify global editor/MCP configuration automatically or delete repository source.
