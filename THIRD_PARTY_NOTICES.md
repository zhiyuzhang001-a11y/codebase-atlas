# Third-party notices

Codebase Atlas redistributes the TypeScript 5.9.3 compiler runtime for its exact
TypeScript/JavaScript analysis. TypeScript is copyright Microsoft Corporation and
contributors and is licensed under the Apache License 2.0. Its unmodified license
text is included in the wheel at
`share/codebase-atlas/node_modules/typescript/LICENSE.txt`.

Codebase Atlas interoperates with, but does not redistribute, these separately
installed Provider projects:

- Serena (`serena-agent`), MIT License.
- Codebase Memory (`codebase-memory-mcp`), MIT License.

Those Provider projects remain governed by their own packages, notices, and
licenses. This notice does not select or grant a license for Codebase Atlas
itself; that product-license decision remains pending.

Codebase Atlas may publish a separately downloadable, project-maintained
Codebase Memory build when released upstream binaries do not yet contain the
required concurrency fixes. Such a build is produced from the public fork at
`zhiyuzhang001-a11y/codebase-memory-mcp`, records its exact source commit and
SHA-256 in `manifest.json`, and carries the complete Codebase Memory MIT
`LICENSE` beside the executable. It is not embedded in the Codebase Atlas wheel,
does not imply a partnership with the upstream project, and does not require the
upstream project to merge the corresponding contribution.
