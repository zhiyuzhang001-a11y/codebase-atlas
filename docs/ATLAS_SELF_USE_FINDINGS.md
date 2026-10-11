# Atlas self-use findings

This is the product feedback ledger produced while Atlas is used to develop
Atlas itself. It records reproducible shortcomings, not speculative feature
requests. A finding stays open until a regression test and a verified change
address the observed behavior.

## Open

None.

## Resolved

### SELF-027: managed routing publication invalidated update acceptance

- Observed task: bootstrap the published candidate with its verified installer,
  then update this repository from the 0.26.0 lifecycle state.
- Expected: after the candidate refreshes the exact source generation, its
  managed routing publication remains operational metadata and doctor stays
  ready.
- Actual evidence: Provider refresh succeeded through the canonical path, but
  appending the byte-exact managed `AGENTS.md` block changed the repository
  fingerprint; candidate doctor reported stale and the update rolled back with
  no rollback errors.
- Safe fallback: retain the ready 0.26.0 lifecycle and freshly queryable index,
  withdraw the candidate Release, and do not rewrite its public tag.
- Resolution: freshness now ignores only a routing insertion or upgrade whose
  bytes match a published Atlas asset and whose non-Atlas remainder exactly
  matches Git `HEAD` after Git's configured text normalization. Published
  routing assets also recognize their portable Windows CRLF representation. A
  companion regression proves a user edit beside the managed block still makes
  the index stale.

### SELF-018: software update could not accept a stale project index

- Observed task: deploy the published 0.26.0 release into the Atlas repository
  immediately after its release merge changed the repository HEAD.
- Expected: `atlas update` either refreshes the stale index inside its protected
  transaction or stops before installation with the exact refresh prerequisite.
- Actual evidence: the verified 0.26.0 installation was created, then candidate
  doctor returned incomplete because the preserved index was stale; update
  rolled project state and configuration back and reported only `updated Atlas
  doctor did not report ready`.
- Safe fallback: refresh with the existing runtime's explicit index command,
  then retry software update; retain the previous version and index throughout.
- Resolution: software update now detects a stale source generation and asks
  the downloaded, checksum-verified candidate to refresh it transactionally
  before switching runtime, Provider, routing, or lifecycle state. The refresh
  has a 75-second minimum bounded window so one 30-second Provider admission
  timeout can retry, and its result is included in the update response.

### SELF-019: update and status disagreed on equivalent Codex transports

- Observed task: verify the successful 0.25.0-to-0.26.0 project switch.
- Expected: the Codex block written by update is immediately recognized by
  `atlas status` and `atlas verify`.
- Actual evidence: update wrote the 0.26.0 `codebase-atlas mcp-auto` entry and
  returned `updated`, but status compared it with the same environment's
  `python -m codebase_atlas.cli mcp-auto` form, reported `outdated`, and made
  verify INCOMPLETE. A project-scoped plan confirmed the written entry was
  otherwise valid.
- Safe fallback: preview and apply the project-scoped canonical Python-module
  block; only the Atlas-managed block changes.
- Resolution: status resolves the exact installation recorded by lifecycle
  state and treats its direct console script and sibling virtual-environment
  `python -m` launcher as equivalent. The real 0.26.0 project block is now
  reported `configured` without rewriting it.

### SELF-020: depth pruning made healthy status globally incomplete

- Observed task: run `atlas status` after the 0.26.0 deployment passed doctor,
  deep inspection, verify, and a real query.
- Expected: bounded nested-repository discovery prunes paths below its maximum
  depth without treating an ordinary deep directory as a failed scan.
- Actual evidence: after visiting only 55 directories, encountering a path
  deeper than six levels returned `nested_repository_depth_budget_exceeded` and
  downgraded the otherwise healthy project status to incomplete.
- Safe fallback: rely on the separately passing verify/deep/query gates and
  retain the explicit partial discovery warning.
- Resolution: reaching the documented depth boundary now prunes that subtree
  and completes the bounded scan; actual time, directory, I/O, and invalid-Git
  failures remain partial. The real Atlas repository now reports
  `bounded_scan_complete`, with a deep-tree regression freezing the behavior.

### SELF-026: cross-project shared daemon blocked an explicit refresh client

- Observed task: refresh the Atlas source repository after committing the
  routing-conflict fix while long-lived PDF-project MCP clients shared the same
  Provider daemon.
- Expected: the distinct-project refresh waits for the bounded mutation slot,
  connects to the shared daemon and publishes its own isolated generation.
- Actual evidence: both the 0.26.0 and 0.26.1 frontends waited about 35 seconds,
  then returned `Provider MCP response id mismatch`; Provider stderr reported
  that the active daemon could not accept the client within 30 seconds. Both
  attempts retained the exact previous generation with no rollback error.
- Safe fallback: keep the previous queryable generation, do not kill unrelated
  project sessions or delete either index, and retry after the owning clients
  release the daemon.
- Resolution: an A/B launch proved that the same Provider bytes connected in
  1.51 seconds from the canonical machine path but timed out after 30 seconds
  from a per-Atlas-version copied path. Stable installation receipts now point
  every Atlas frontend version at one checksum-verified Provider bundle under
  the canonical machine store. Atlas also preserves the Provider's null-ID
  `-32001` admission diagnostic as `provider_startup_timeout` and retries it
  within the caller's bounded deadline. The 0.26.2 candidate refreshed this
  repository in one attempt and 7.26 seconds while the PDF-project Provider
  client remained active.

### SELF-025: a project-authored Atlas skill blocked software update

- Observed task: run the published `atlas update` from 0.26.0 to 0.26.1 in the
  Atlas source repository, whose tracked project skill intentionally contains
  richer development guidance than the installed routing template.
- Expected: preserve the repository-owned skill and upgrade the verified
  runtime, Provider and project MCP transport without changing source.
- Actual evidence: update installed the verified 0.26.1 candidate, classified
  `.agents/skills/codebase-atlas/SKILL.md` as a fatal routing conflict, rolled
  back the project switch and left the stale launcher unchanged.
- Safe fallback: retain 0.26.0 and its fresh index; never overwrite or remove
  the tracked project skill to force an update.
- Resolution: enable and update explicitly preserve a custom colliding skill,
  report it in structured results, and continue to reject modified
  Atlas-managed `AGENTS.md` blocks. Transaction and lifecycle regressions prove
  the custom bytes survive success and rollback boundaries.

### SELF-021: one Provider admission conflict poisoned the whole MCP session

- Observed task: start two fresh Codex validation tasks concurrently against
  one Atlas project.
- Expected: concurrent read/query clients wait for startup admission or expose
  bounded transient backpressure and recover on their next request.
- Actual evidence: one client returned `provider_busy`; `AtlasService` cached
  that startup result permanently, so only archiving the extra task and opening
  a new MCP session appeared to recover it.
- Safe fallback: keep one validation owner and retry after it finishes; do not
  count a replacement session as proof that the failed session recovered.
- Resolution: shared-layout clients wait under the caller's query budget and a
  session retries Provider startup after `provider_busy` or
  `provider_startup_timeout`. Two unit regressions cover both lifecycle APIs;
  four simultaneous real queries all returned the exact target definition.

### SELF-022: Provider migration did not migrate lifecycle identity

- Observed task: migrate this repository from the legacy Provider layout before
  repeating the multi-client query proof.
- Expected: config, Provider database, generation metadata, index state, and
  lifecycle state publish one consistent shared project identity.
- Actual evidence: migration published the shared config and database but left
  `lifecycle-state.json` on the legacy project identity. Doctor and every query
  then failed closed with `lifecycle_state_invalid`.
- Safe fallback: reject the migration result and use an identity-aware repair;
  never edit the lifecycle file ad hoc or report the shared database alone as
  success.
- Resolution: migration now recognizes a valid legacy lifecycle identity,
  rebinds it to the deterministic shared identity, rolls it back with the config
  on later publication failure, and can repair an already-published affected
  config. The repaired real project passed doctor and four concurrent queries.

### SELF-023: first shared index created its cache before securing it

- Observed task: run the public multi-MCP concurrency gate on clean hosted
  runners after making the shared Provider cache fail closed.
- Expected: the first `index` creates the shared Provider root privately and
  the later MCP clients reuse it.
- Actual evidence: `index` used a default-permission `mkdir` before the managed
  Provider setup path ran. On fresh Linux and macOS runners this produced mode
  `0755`, so Atlas correctly rejected its own new directory as
  `permissions_too_broad` before concurrency began.
- Safe fallback: cancel the acceptance run, do not tag the release, and do not
  weaken Provider root validation.
- Resolution: direct shared-layout indexing now passes through the same
  race-safe, fail-closed managed-cache preparation used by MCP startup. Tests
  prove a fresh shared root is mode `0700`, an existing broad root remains
  rejected, and legacy project caches retain their prior compatibility.

### SELF-024: old readable snapshots caused cross-process retry busy-spins

- Observed task: run the eight-client, 600-file contention profile on the
  release candidate after the ordinary multi-client matrix passed.
- Expected: clients waiting behind the project refresh owner poll at a bounded
  rate until a new generation is published or their deadline expires.
- Actual evidence: macOS ARM64 returned the still-readable old generation
  immediately, causing one waiter to retry 479,463 times in 60 seconds. The
  polling load starved the owner and ended as `refresh_owned_elsewhere`.
- Safe fallback: fail the release gate and retain the previous published
  version; do not relabel the result as transient runner noise.
- Resolution: unchanged-generation and snapshot-race retries now use a
  deadline-bounded 10–100 ms exponential backoff. Owner waits include the
  delay in `wait_for_owner` timing, snapshot races expose `retry_backoff`, and
  deterministic tests cap retry attempts while preserving deadline and
  coalescing behavior.

### SELF-010: cross-project refreshes raced in the shared Provider daemon

- Observed task: run two real repository refreshes concurrently in the
  six-architecture Provider qualification gate.
- Expected: both distinct projects publish fresh, isolated generations and the
  final frontend disconnect releases all private-fixture files.
- Actual evidence: Windows x86_64 intermittently returned one `refreshed` and
  one `failed`; improved diagnostics then identified a false path-safety
  rejection caused by 8.3/long-path spelling changes during concurrent
  directory creation. Both Windows architectures also retained daemon
  log/lifetime-lock handles long enough for immediate fixture cleanup to fail.
- Safe fallback: reject the architecture gate and do not merge or release.
- Resolution: project names are validated lexically without racing two path
  resolutions; a machine-scoped lock serializes only Provider mutations while
  read queries remain concurrent; real Windows fixtures wait for their private
  non-permanent daemon to retire. Local dual-repository isolation passed, and
  all six architectures passed qualification run 34137764725.

### SELF-011: Serena child-exit diagnostics omitted stderr

- Observed task: run the real multi-MCP stress gate on Linux x86_64.
- Expected: if the Serena subprocess exits, the structured failure identifies
  its underlying startup or runtime error.
- Actual evidence: Atlas reported only `Serena runner exited before responding
  (exit=1)`; concurrent clients also wrote to one shared `runner.stderr.log`, so
  the failed process's diagnostic could not be attributed reliably.
- Safe fallback: reject the architecture gate and avoid guessing at the cause.
- Resolution: each runner has a private stderr log, child-exit errors include a
  bounded stderr tail, and successful-run logs are cleaned. The diagnostics
  exposed the missing runtime dependency without cross-process ambiguity; the
  resulting runtime fix passed all six architectures in run 34137764725.

### SELF-012: cleanup probed a retiring Unix daemon unnecessarily

- Observed task: wait for a private Provider daemon to retire after the Linux
  ARM dual-repository isolation proof had passed.
- Expected: `daemon status` returns promptly and permits bounded cleanup.
- Actual evidence: Linux ARM's `daemon status` frontend itself remained in the
  daemon retirement path for more than five seconds and timed out. Unlike
  Windows, Unix does not require the process to release handles before removing
  the private fixture tree.
- Safe fallback: reject the platform job even though its isolation assertions
  passed.
- Resolution: daemon retirement is polled only on Windows, where open handles
  block fixture removal. Linux ARM and every other target passed qualification
  run 34137764725.

### SELF-013: project Codex routing still translated newlines on Windows

- Observed task: run `enable` from the installed 0.26 candidate wheel on
  Windows x86_64.
- Expected: the bytes authorized by lifecycle recovery exactly match the bytes
  published to `.codex/config.toml`.
- Actual evidence: project configuration writes used text mode's platform
  newline translation, producing CRLF while the transaction authorized LF;
  acceptance correctly failed closed and preserved the unexpected file.
- Safe fallback: reject the installed-candidate lifecycle gate and do not
  weaken transaction comparison.
- Resolution: newline translation is disabled at every project Codex config
  write, update, and remove path, with direct regression coverage. Both Windows
  architectures passed installed lifecycle qualification in run 34137764725.

### SELF-014: warm-query performance gate ignored Windows timer granularity

- Observed task: run the full unit matrix on Windows/Python 3.11.
- Expected: a no-scan, near-zero-median warm snapshot path passes a stable
  performance gate.
- Actual evidence: the median was `0.0 ms`, but one approximately `15 ms`
  scheduling quantum moved p95 above the POSIX-derived `2.022 ms` threshold.
- Safe fallback: treat the job as failed and inspect the sample distribution;
  do not attribute the failure to a product regression.
- Resolution: POSIX keeps the strict threshold; Windows uses a bounded `20 ms`
  threshold that covers its timer/scheduler quantum while still catching
  meaningful regressions. The full base CI matrix passed on the final code.

### SELF-015: Serena stdout waiting used Unix-only pipe selection

- Observed task: run the Python multi-MCP stress qualification on Windows.
- Expected: each Serena semantic subprocess returns bounded responses through
  its stdio transport.
- Actual evidence: `select.select()` was called on a Windows pipe and raised
  `WinError 10038` because Windows `select` accepts sockets only.
- Safe fallback: return an explicit tool error and reject the qualification.
- Resolution: stdout is drained on a dedicated reader thread into a
  bounded-wait queue, matching the cross-platform Provider transport pattern.
  Local real multi-MCP Serena stress and both Windows architecture jobs passed.

### SELF-016: TypeScript scope membership compared Windows path spellings

- Observed task: run the TypeScript multi-MCP stress qualification on Windows.
- Expected: `baseline.ts` is recognized as a root in the selected fixture
  `tsconfig.json`.
- Actual evidence: the target used an 8.3 temporary-root spelling while the
  compiler file list used the long spelling, so string-set membership falsely
  reported the file outside the project.
- Safe fallback: reject the query rather than analyze the wrong project.
- Resolution: the requested target, compiler file list, and configured roots
  are canonicalized through the filesystem before comparison and duplicate
  roots are removed. Both Windows architecture jobs passed TypeScript
  qualification in run 34137764725.

### SELF-017: Serena preflight accepted Python without its LS installer

- Observed task: rerun the Python Windows multi-MCP qualification after stderr
  attribution was fixed.
- Expected: lifecycle acceptance rejects an environment that cannot start
  Serena's configured Python language server.
- Actual evidence: `serena` imported successfully, so doctor passed, but the
  first semantic query failed because neither `uvx` nor `uv` was on PATH.
- Safe fallback: reject the qualification and retain the fresh structural
  generation without claiming semantic completeness.
- Resolution: Provider startup exposes both portable interpreter script
  layouts, Python runtime checks require uv/uvx, and the qualification workflow
  installs and verifies pinned uv from Python's reported scripts directory.
  All six architectures passed Python qualification in run 34137764725.
- Follow-up evidence: Windows system Python installs console scripts in a
  `Scripts` child rather than beside `python.exe`; both layouts must be searched
  and workflow verification must use Python's reported scripts directory.

### SELF-001: mixed-language repository selects an unrelated fixture project

- Observed task: query the impact of `update_project` in
  `src/codebase_atlas/simple_cli.py` from the Codebase Atlas repository.
- Command: `atlas query impact update_project --config .codebase-atlas.toml
  --target-path src/codebase_atlas/simple_cli.py --direction upstream --depth 2
  --max-nodes 80 --max-edges 120 --stale-policy warn`.
- Expected: select an index scope containing the requested Python target, or
  return a structured scope mismatch that explains how to correct the project
  configuration.
- Actual evidence: the configured TypeScript provider selected
  `fixtures/ts-tests/tsconfig.json`, then raised an unstructured `RuntimeError`
  because the Python target was outside that TypeScript project.
- Safe fallback: reject the Atlas evidence and inspect the relevant source and
  tests directly.
- Resolution: target language scope is checked before Provider startup. CLI
  queries now return structured
  `target_outside_indexed_language_scope`/exit 2 with one remediation; MCP
  queries return the same reason as explicit incomplete evidence. Mixed-language
  multi-index routing remains a separate future capability, not an implied fact.

### SELF-002: lifecycle rollback existed only in process memory

- Observed task: review `enable`, `update`, and `stop` failure handling while
  preparing lifecycle regression tests.
- Expected: an interrupted process leaves enough durable evidence for the next
  lifecycle command to restore or safely reject the prior transaction.
- Actual evidence: `EnableTransaction` and `RoutingTransaction` could roll back
  exceptions, but process termination discarded their snapshots.
- Safe fallback: report the transitional lifecycle state and avoid accepting it
  as ready.
- Resolution: a repository-identity-bound journal outside the repository now
  snapshots the wider lifecycle boundary, distinguishes accepted operations,
  preserves external edits, cleans owned staging, and is exercised through an
  actual child process exiting without exception cleanup.

### SELF-003: removal receipt was published after destructive changes

- Observed task: inject termination at every `remove` publication boundary.
- Expected: every destructive phase has an already-durable recovery plan.
- Actual evidence: the receipt was written only after configuration deletion
  and data-directory movement, so termination in between was not resumable.
- Safe fallback: retain the `removing` marker and refuse further mutation.
- Resolution: schema 3 provisional receipts are written before the marker and
  before source mutation. Recovery now handles interruption after routing
  removal or data movement and preserves conflicting user edits.

### SELF-004: concurrency qualification accepted invalid runtime paths too late

- Observed task: run the real four-client Python MCP qualification locally.
- Expected: missing Node.js or Serena runtimes fail before indexing and stress.
- Actual evidence: an empty Node lookup became the current directory and was
  rejected only by the final doctor after the concurrency rounds had run.
- Safe fallback: reject that run as a gate failure and rerun with the verified
  bundled Node and installed Serena interpreter.
- Resolution: the stress entrypoint now requires Provider, Node.js, and Serena
  Python to be executable files before creating the test repository.

### SELF-005: removal could not distinguish an Atlas-created rule file

- Observed task: exercise `enable -> remove -> remove` with repository routing
  enabled and require the repository to return to its original shape.
- Expected: if Atlas created `AGENTS.md`, removal deletes it; if the file
  existed before Atlas, removal preserves the file and removes only the exact
  managed block.
- Actual evidence: the original-existence bit lived only in the enable process.
  A later removal either left an empty `AGENTS.md` behind or could not encode
  “the original file was absent” in its recovery validation.
- Safe fallback: preserve the empty file rather than risk deleting user data.
- Resolution: a repository-identity-bound routing ownership record now survives
  process boundaries, participates in lifecycle recovery, and deletes only an
  Atlas-created rule file. Empty Atlas-created routing directories are removed
  after the final recoverable-removal marker is durable. Regression tests cover
  both created and pre-existing rule files.

### SELF-006: executable normalization escaped the Serena environment

- Observed task: run the installed-candidate acceptance with the verified
  Serena tool interpreter.
- Expected: execute the exact virtual-environment launcher supplied by the
  operator.
- Actual evidence: validation called `Path.resolve()`, followed the launcher's
  symlink to its base Python, and then reported that `serena` was not installed.
- Safe fallback: reject that run before treating it as product evidence.
- Resolution: executable validation now makes paths absolute without resolving
  symlinks. Both the concurrency and candidate-project acceptance entrypoints
  preserve virtual-environment launch semantics, with a regression test.

### SELF-007: macOS acceptance temp roots could violate Provider security

- Observed task: enable the candidate in a disposable repository under the
  default macOS per-user temporary directory.
- Expected: the Provider creates its authenticated local daemon endpoint.
- Actual evidence: inherited ACLs on the default temporary path caused the
  Provider to reject the endpoint as insecure.
- Safe fallback: reject the run; do not weaken Provider permission checks.
- Resolution: the macOS acceptance fixture uses a private `/private/tmp`
  directory, removes inherited ACLs from its runtime directory, and retains
  mode `0700`. Product security policy remains unchanged.

### SELF-008: Provider isolation fixtures assumed macOS utilities

- Observed task: prepare checkout/worktree isolation for the public
  Linux/macOS/Windows gate.
- Expected: the same test selects a secure runtime directory on every runner.
- Actual evidence: the fixture hard-coded `/private/tmp` and `chmod -N`, neither
  of which is portable to Linux or Windows.
- Safe fallback: keep the proof local to macOS and do not claim a platform
  matrix result.
- Resolution: runtime fixture creation is now platform-aware; macOS retains its
  ACL cleanup while other systems use their native temporary root and mode
  handling.

### SELF-009: Windows exposed non-portable file snapshot assumptions

- Observed task: run the 0.26 candidate on the public Windows/Python 3.14
  matrix.
- Expected: unchanged routing/config files compare identically across path and
  open-handle snapshots, and generated configuration has canonical bytes.
- Actual evidence: Windows gave `st_ctime_ns` different path/handle semantics,
  text writes translated LF to CRLF while the transaction authorized LF bytes,
  and `readlink` exposed an extended `\\?\\` path prefix. These cascaded into
  safe but false rollback conflicts.
- Safe fallback: the public gate failed; no merge or release was attempted.
- Resolution: routing races use portable identity/mode/size/mtime plus content,
  configuration writes disable newline translation, and Windows link targets
  are normalized without following them. The public matrix is the regression
  proof.

### SELF-028: global Atlas guidance survived a process-only control setup

- Observed task: stage-zero ordinary Codex terminal probe with
  `--ignore-user-config`, an empty MCP map, Atlas skill disabled, and private
  common AGENTS guidance. Exact receipts are linked from
  [the stage-zero execution result](CODEX_EXPERIENCE_STAGE0_EXECUTION_RESULT.md).
- Expected: the control has neither Atlas tools nor Atlas-specific guidance,
  without editing global configuration or trust.
- Actual evidence: the real tool catalog had no Atlas tools, but the raw
  user instruction message included global `Codebase Atlas deployment routing`.
  A read-only `debug prompt-input` check with `project_doc_max_bytes=0` and
  common `developer_instructions` still included that routing. No real Atlas
  MCP query or paired work was run in this experiment.
- Safe fallback: stop the paired route, retain failure events and private
  preparation, and keep global AGENTS/configuration unchanged. Do not tell the
  model to ignore loaded guidance and call that isolation.
- Resolution: unresolved. A supported process-only global-guidance isolation
  mechanism or separately reviewed host/control contract is required. Validate
  actual loaded guidance and tools in a fresh bounded probe before paired work;
  preserve the probe/session cap and report the failed first probe separately.

### SELF-029: a host capability probe assumed optional MCP resources were lists

- Observed task: the bounded stage-zero Atlas A probe used the unchanged 0.26.1
  calibration installation and its private project connection. The real host
  called `resources/list` and `resources/templates/list`; receipts are recorded
  in [the stage-zero execution result](CODEX_EXPERIENCE_STAGE0_EXECUTION_RESULT.md).
- Expected: establish that generic MCP resource functions cannot provide an
  alternate file-reading route before target-project queries. The experimental
  gate required empty lists, without first handling a tools-only MCP server.
- Actual evidence: both calls returned `-32601 Method not found`. The probe
  stopped before patch, Git, `project_status` or symbol queries, and B did not
  run. The installed 0.26.1 `McpServer.handle` advertises only tools and rejects
  non-tool methods, including resource reads. This is an experimental capability
  contract gap; it does not establish a current-product retrieval regression or
  a successful target query.
- Safe fallback: preserve the failed gate and session cost; inspect the exact
  installed dispatcher read-only. Do not equate a failed list call with an empty
  list, enable another server, read real private URIs, or replace real host query
  acceptance with a direct script.
- Resolution: the finite capability classification was independently reviewed
  against the exact installation source and a no-service dispatcher diagnostic.
  A revised tools-only gate keeps the original failure and is valid only while
  that installation and sole server remain unchanged. Actual A1 target queries
  subsequently passed their positive/negative controls, but its stderr gate
  still blocks the full probe. No product source fix or completed stage is claimed.

### SELF-030: compact change briefs discarded evidence qualification

- Observed task: stage-one independent review of the candidate guidance that
  recommends compact change briefs, against the exact stable 0.27.1 source.
- Expected: compact evidence retains repository/generation provenance,
  relationship resolution and each subquery's completeness and truncation.
- Actual evidence: `_compact_result` removed node `provenance`, edge
  `resolution`, and subquery `status`, `completeness` and `truncation`.
  A partial-scope response and heuristic relationship lost those qualifications.
  Two new regressions fail against the frozen baseline with missing provenance
  and completeness; the original eight change-analysis tests did not detect it.
- Safe fallback: use full evidence or explicitly fill missing qualification;
  do not present a compact heuristic or partial result as exact complete facts.
- Resolution: candidate presentation fix retains the optional fields without
  changing queries, schemas or the full default. Direct compact regressions
  cover exact/heuristic/unresolved edges, and a typed service-to-JSON test checks
  partial scope through the complete brief. The candidate's 37 protocol/evidence
  tests pass; independent stage-one re-review and real host validation remain
  required before claiming the optimization complete.

## SELF-031 — Node 在严格宿主权限下读取 OpenSSL 配置的启动失败

- 观察任务：阶段二 TypeScript 开发 fixture 的 `codex sandbox -P atlas_stage2_work` 公共测试。使用既有 Node v24.19.0、root deny/minimal read，未增加系统文件权限。
- 预期：实际加载公共测试，再将初始业务断言失败记录为失败；退出码本身不证明测试已执行。
- 实证：原104项 G0操作中的4次 TS测试均 rc1、stdout0、stderr344bytes；新增测试加载计数检查没有匹配测试执行。最小诊断明确 `BIO_new_file: Operation not permitted`，读取 `/System/Library/OpenSSL//openssl.cnf` 被拒绝，尚未启动模型。原操作与加载失败收据永久保留。
- 安全回退：只准备进程内 Node `--openssl-config=/dev/null`，MCP child `OPENSSL_CONF=/dev/null`；不修改全局环境或扩大 OS 文件权限。显式 `--test-reporter=tap` 使加载计数可核验，避免误用 v24 默认 spec 输出的格式。
- 解决验证：v3 追加同 profile 非模型检查的8次公共测试实际加载计数和退出码全部匹配；527项immutable和项目健康证据待独立复核。该检查未运行模型，尚不宣称宿主开发或性能通过。本项是试验 runtime 条件问题，目前未修改 Atlas 产品源码。

## SELF-032 — 私有宿主验证采集器与候选协议不一致，且退出回执不完整

- 观察任务：阶段二首次 Python 只读探针，线程 `01a12b16-fd26-7f23-b53f-76c6770fcb39`。
- 预期：合法省略 depth 的 compact 查询与既有状态/definition 字段可被严格识别；监督结束时记录明确的自有进程收尾证据。
- 实际证据：CLI rc0、无 stderr，但采集七处 unresolved，漏识别 coordination/schema_version、误判省略 depth；wrapper 最终退出回执缺失。后续只读精确 PID 核查仅证明两进程不存在，退出码未知。这是私有验证工具缺口，未证明产品查询失败。
- 安全降级：旧样本永久 measurement_invalid；停止后续模型启动，不补评分、不停止共享 Provider；候选协议通过准确源码检查确认。
- 前瞻修复及检查：版本化严格字段适配，depth 仅采用服务端已声明默认2；注册并核验自有 PID/argv/启动身份；post-Popen finally 清理、协调 TERM/KILL 等待、有界管道收集。39 项采集、10 项真实合成进程及17项控制器检查通过。独立修复审查和新有限执行卡待完成，阶段二出口未通过。

MAINSELF-032 追加前瞻证据（2026-10-11）：第三次私有 TS 探针日志含9个唯一结束事件而无启动事件，旧控制器误以启动/结束集合必须相同判定不闭合；该历史样本保持 invalid。新版本以完整请求/结果映射和核心未解决项验证未来闭环。纯候选源码调用还发现私有定位适配器漏识别已有 heuristic/non_exhaustive/matched_terms/budget/required_next_action；严格补齐已有来源字段，不修改产品定位行为、不将导航候选算作读取。24项控制器、42项采集和7项实际来源契约检查通过；真实普通任务验证尚待执行，阶段出口仍未闭合。


## 2026-10-11 体验优化收尾状态（SELF-028 至 SELF-032）

以上条目保留发现时点；最终状态见[完整收尾](CODEX_EXPERIENCE_CLOSEOUT.md)。SELF-028 的原控制指引隔离失败保留，后续仅采用独立审查的有限控制合同，未证明通用指引移除。SELF-029 的tools-only分类只解决实验能力合同，不改变产品协议。SELF-030 的compact字段保真修复在私有候选中通过37项回归和独立复核，C7提供有限TS真实查询证据；未合并、安装或发布，Python真实资格及整体体验未通过。SELF-031进程内OpenSSL参数让公共测试真实加载，未扩大权限。SELF-032采集/收尾适配只对未来生效，四份历史无效记录仍无效；最后三场采集有效，仍不能替代阶段二全部场景验收。项目已按用户决定暂停，不继续开发或补实验。
