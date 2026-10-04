# Codebase Atlas Rust 语言支持计划

- 计划日期：2026-09-22
- 产品基线：Codebase Atlas 0.26.4
- 状态：`SCIP_ONLY_STAGE0_FAILED / LAYERED_EVALUATION_AUTHORIZED`
- 目标：在不削弱现有 Python、TypeScript/JavaScript 正确性、隔离、事务和资源边界的前提下，为 Rust 建立可验证、可分阶段发布的语言适配能力。

> 2026-09-22 修订：本文件保留最初 SCIP-first 计划和失败纪律作为历史依据。
> 第一次正式 Stage 0 证明 rust-analyzer 1.98.0 的 SCIP 输出不能满足本计划冻结的
> implementation、lint 和 offline-completeness 门，结论为 `KEEP_EVALUATION_ONLY`。
> 后续已获授权的评估由 `docs/RUST_LAYERED_SUPPORT_PROTOCOL.md` 管理；若两份文件冲突，
> 新协议仅对新的分层评估生效，不重写或豁免旧失败。

## 1. 执行结论

Rust 可以加入 Atlas，但本计划不把“支持 Rust”定义成一次性完成六类查询，也不允许先写产品代码再寻找可用的上游事实。

推荐路线为：

1. 先关闭当前 0.26.x 稳定性修复，冻结干净基线；
2. 独立评估 `rust-analyzer scip`，只验证它明确表达的语义域；
3. 建立最小语言适配器注册表，但不同时实现混合语言聚合；
4. 第一阶段只候选发布精确定义、引用和实现关系；
5. callers、callees、related tests 和 impact 必须通过新的资源与独立留出门后再增加；
6. 任一阶段失败都保留为 evaluation-only，不影响现有产品和先前已通过的较低能力层。

首选上游组合：

- `cargo metadata --format-version 1`：工作区、包、target 和依赖身份；
- `rust-analyzer scip .`：定义、引用、符号与实现关系的主要离线语义来源；
- Atlas：仓库身份、构建上下文指纹、SCIP 流式导入、统一事实、查询、完整性、事务发布和生命周期；
- `rust-analyzer` LSP call hierarchy：仅作为后续深层关系候选，不是 Rust MVP 的强制依赖。

不选择自行编写 Rust 解析器，不根据名称或文本引用猜测语义关系，也不把 Tree-sitter 语法结果冒充精确 Rust 语义。

## 2. 上一次尝试为什么失败

M27 的 Go 尝试证明了新语言适配在技术上可行，但最终正确地以 evaluation-only 结束。新计划必须继承以下事实。

### 2.1 已经证明可行的部分

- Provider-neutral language registry 能覆盖发现、配置、生命周期、查询和诊断；
- 精确语言服务器能够提供 Atlas 六类查询所需的事实；
- 构建上下文、进程拥有权、超时、清理、freshness 和回滚可以显式建模；
- Python、TypeScript 既有行为可以在加入第三语言候选时保持不变。

这些成果可以作为设计参考，但旧 Go 分支不得直接合并或改名为 Rust 支持。

### 2.2 第一次独立留出的失败

- 离线缓存缺少冻结仓库的依赖，六个有效案例在语义输出前返回 `go_dependencies_unavailable`；
- 一条 `related_tests` 真值把调用点行号写成了测试函数声明，真值与产品合同不一致；
- 资源采样器没有考虑 Provider 的延迟启动，记录了无效的 0 RSS。

Rust 计划因此要求：依赖准备、真值 schema 和资源采样器都必须在密封留出前独立验收。

### 2.3 最终产品门的失败

Go 候选的正确性已经通过，但 `gopls` 在三次新鲜运行中峰值为 907.000、848.781 和 776.938 MiB，全部超过当时冻结的 768 MiB 上限。一次较低结果不可重复，不能选择性采信。

这说明：

- 正确性通过不等于产品可发布；
- 上游常驻工作集和方差必须在产品实现前测量；
- 不得为了完成里程碑临时放宽已观察到失败的资源门；
- 如果上游只能以高成本提供某种能力，应降低首版能力范围，而不是隐藏成本。

### 2.4 本计划对 M27 的修正

- 从“六类查询同时晋级”改为“能力分层晋级”；
- 从“先完成适配器”改为“先完成上游资源与协议门”；
- 从“在线缺依赖时再处理”改为“显式 prepare，语义运行严格 offline”；
- 从“一个总计划反复修补”改为“每层都有不可变证据和独立停止点”；
- 从“语言实现和混合语言同时扩张”改为“先单语言适配，再另立混合语言合同”。

## 3. 参考的现有工作

### 3.1 rust-analyzer

`rust-analyzer` 已经维护 Rust 的 crate graph、名称解析、类型和引用语义，并通过增量查询体系按需更新派生状态。Atlas 不重复实现这些编译器级能力。

参考：

- https://rust-analyzer.github.io/book/
- https://rust-analyzer.github.io/book/contributing/architecture.html
- https://rust-analyzer.github.io/book/configuration

借鉴内容：

- source、crate graph 与派生语义分离；
- 构建上下文是语义身份的一部分；
- 小变更增量更新的依赖图思想；
- 语法信息与语义信息不得混淆。

注意：rust-analyzer 的 SCIP 生成存在公开的单线程性能问题，不能假定它适合大型仓库。Stage 0 必须测量真实索引时间、RSS 和输出尺寸。

### 3.2 SCIP 与 scip-rust

SCIP 是语言无关的代码索引协议，提供 document、occurrence、symbol、relationship 和外部符号模型。官方 SCIP 项目列出 rust-analyzer 为 Rust indexer；`scip-rust` 是 `rust-analyzer scip .` 的薄封装。

参考：

- https://github.com/scip-code/scip
- https://github.com/scip-code/scip/blob/main/docs/scip.md
- https://github.com/scip-code/scip-rust

借鉴内容：

- 稳定的包、版本和限定符号身份；
- 定义、引用和实现关系；
- 跨文件、跨 crate 的语义 occurrence；
- 大索引应流式生成和消费，不能把完整 protobuf 一次性载入内存。

限制：SCIP occurrence 本身不等于完整调用图。没有明确 CALLS 事实时，不得从普通引用或文本形态推断 callers/callees。

### 3.3 Cargo metadata

Cargo 官方提供版本化 JSON metadata，描述 workspace members、packages、targets、features 和 resolved dependencies。

参考：

- https://doc.rust-lang.org/cargo/commands/cargo-metadata.html
- https://doc.rust-lang.org/cargo/reference/manifest.html

借鉴内容：

- 通过 `--format-version 1` 固定 schema；
- 通过 `--locked`、`--offline`、`--frozen` 控制依赖解析；
- 使用 package ID、版本、source、target、edition 和 feature 集合构造 build-context fingerprint；
- 预检可使用 `--no-deps`，但完整语义门必须验证实际 resolved graph。

### 3.4 Salsa/DICE/Glean 的设计思想

本计划不引入这些运行时，但吸收其已验证原则：

- Salsa/DICE：记录计算依赖，只重算失效节点，合并相同并发工作；
- Glean：原始事实和派生事实分层，schema 版本化，旧客户端兼容；
- Kythe/SCIP：符号身份包含 corpus/package/version/language/signature，而不是只有文件与名称。

Rust MVP 只要求 Atlas 的 manifest 能引用不可变 Rust 索引分片；细粒度增量 SCIP 合并属于后续优化，不是首版发布条件。

## 4. 产品能力分层

### R0：可行性，不进入产品

验证工具链、协议、正确性、完整性、资源、安全和离线能力。

### R1：Rust Core

允许公开候选的最小能力：

- workspace/crate/package 发现；
- struct、enum、trait、type、function、method、module、macro 的精确符号定义；
- 精确 semantic references；
- trait/type implementation relationships；
- 同名符号按 package/crate/module/owner/signature 消歧；
- freshness、provenance、completeness、预算和错误语义；
- CLI、JSON-lines、MCP、status、doctor、inspect、repair、remove 生命周期。

在 R1 中：

- `definition` 和 `references` 可以返回 complete；
- implementation 关系作为版本化 edge 暴露；
- `callers`、`callees`、`related_tests`、`impact` 返回稳定的 `unsupported_for_rust_core`，绝不返回成功空结果；
- `analyze_change` 可以使用定义和引用，但必须把缺失的深层关系列为未运行/不支持。

### R2：Rust Call Graph

单独评估 rust-analyzer LSP call hierarchy 或未来可验证的 SCIP 调用事实：

- 精确直接 callers/callees；
- trait dispatch、函数指针、宏生成调用和动态分派必须有明确 partial 边界；
- 不允许从 reference occurrence 猜测调用边。

R2 失败不撤销 R1。

### R3：Rust Tests 与 Impact

- `#[test]`、模块内单元测试、`tests/` integration targets；
- 相关测试必须返回测试函数/测试 target 身份，不以任意调用点冒充；
- impact 只遍历已经晋级的 exact edges；
- doctest、custom harness、宏生成测试和运行时注册测试分别标记能力状态。

R3 失败不撤销 R1/R2。

### R4：混合语言仓库

Rust 单语言能力稳定后，才允许与 Python/TypeScript 分片组合。R4 必须另行冻结项目级 generation、查询路由和跨语言 completeness 合同。

首版 R4 不承诺 Rust 与其他语言之间存在调用关系；只有生成代码映射、FFI manifest 或显式 schema 能提供证据时才增加跨语言边。

## 5. Rust 身份与构建上下文合同

### 5.1 仓库和 workspace 身份

Atlas project 仍绑定一个精确 Git 根。Rust scope 由以下信息确定：

- workspace root；
- workspace member package IDs；
- 每个 Cargo target 的 kind、name 和 source path；
- nested workspace 和独立 Git 根必须显式隔离；
- vendor、target、generated output 和 symlink 的边界必须冻结。

检测到多个互不包含的 Cargo workspace 时，默认返回 ambiguity，不自动合并。

### 5.2 符号身份

规范身份至少包含：

```text
repository identity
+ language=rust
+ Cargo package source/name/version
+ crate target
+ module path
+ owner type/trait
+ symbol kind/name/signature
+ SCIP scheme and descriptor
```

绝对机器路径不能成为可持久化的规范身份。SCIP local symbols 只能在所属 document/index generation 内使用。

### 5.3 Build-context fingerprint

至少覆盖：

- `Cargo.toml`、workspace manifests 和 `Cargo.lock`；
- `rust-toolchain` / `rust-toolchain.toml`；
- `.cargo/config.toml` 与允许读取的上层配置；
- rustc、Cargo、rust-analyzer、SCIP protocol 版本；
- target triple、edition、selected packages、features；
- build script、proc macro 和 generated source 策略；
- 明确白名单内会影响 cfg 的环境变量。

任何影响语义的上下文变化都必须使 Rust 分片 stale。未知上下文不能继续报告 fresh。

## 6. 安全与依赖策略

### 6.1 默认不隐式执行项目代码

Rust build scripts 和 procedural macros 可能执行项目或依赖代码。Stage 0 必须证明 `rust-analyzer scip` 的实际执行边界，并记录所有子进程。

默认产品合同：

- 不运行应用或测试；
- 不静默运行 build script/proc macro；
- 如果精确索引不可避免地需要执行它们，必须在计划/预览中明确列出并要求显式选择；
- 未授权时返回 `rust_execution_required` 或能力降级，不能假装完整。

### 6.2 显式 prepare，运行期 offline

提供两阶段流程：

1. `plan/prepare`：列出缺失 toolchain、rust-src、registry/git dependencies、磁盘和预计动作；
2. `index/query`：使用已验证缓存，强制 offline/frozen，不联网获取依赖。

缺少依赖时保留上一健康 generation，返回具体 package 和 remediation。不得在 MCP 查询过程中自动 `cargo fetch`。

### 6.3 工具获取

- Atlas 不使用未固定的 `latest`；
- 记录 rust-analyzer、rustc、Cargo、SCIP CLI/scip-rust 的版本、来源、许可证和 SHA-256；
- 优先使用用户已有且版本兼容的工具链；
- 若未来提供托管工具，必须使用版本化机器安装并通过独立供应链审计；
- 不依赖编辑器扩展中不可定位或不可复现的 rust-analyzer 副本。

## 7. 实施阶段

### Stage -1：关闭稳定性基线

开始 Rust 工作前必须：

- 独立提交当前 SELF-028 Git snapshot 修复；
- 运行完整产品测试和安装态回归；
- 确保产品仓库 clean；
- 决定是否发布 0.26.5；
- 记录 Rust 工作起点的 exact commit。

Rust 计划不得与当前 0.26.x 未提交修复混在同一提交或 PR。

退出：`RUST_BASELINE_READY` 或 `BLOCKED_BASELINE_DIRTY`。

### Stage 0：上游可行性和资源门

只允许 evaluation 代码和忽略目录中的工具/数据，不修改产品语言选择。

#### 冻结工具

- exact rustc/Cargo toolchain；
- exact rust-analyzer build；
- exact scip-rust wrapper（若使用）；
- exact SCIP CLI；
- checksums、licenses、支持平台和命令行。

#### 冻结仓库

至少四类 exact SHA：

1. 小型单 crate library；
2. 中型 workspace，包含 library、binary、unit/integration tests；
3. 宏、trait、泛型和 feature 较多的框架/工具；
4. 大型 workspace，用于索引时间、内存和输出尺寸压力测试。

开发真值至少 36 项：

- 12 definition；
- 12 references；
- 6 implementations；
- 6 negative identity；
- 至少覆盖同名 method、trait impl、re-export、macro、cfg/feature、integration test 和外部 crate reference。

#### 比较路线

- rust-analyzer SCIP；
- rust-analyzer LSP，仅作为能力和资源对照；
- 当前 Codebase Memory Rust 能力，只在其明确支持的事实域中比较；
- 不因某条路线失败就在同一冻结结果后新增未预注册路线。

#### 必测指标

- cold/warm index wall time；
- 最大 process-tree RSS，而非只测 launcher；
- index 文件和临时/cache 磁盘；
- SCIP lint diagnostics；
- 两次独立索引的规范化答案 hash；
- offline/frozen 行为；
- build script、proc macro、cargo、rustc 子进程；
- source/config/caller environment 变化；
- timeout、cancel、crash 和 residual process；
- SCIP streaming importer 的峰值内存。

#### 预注册资源硬门

使用当前 Atlas 资源分层，不沿用 M27 的旧 768 MiB 数字：

- ordinary repository：总 Atlas-owned process-tree peak <= 1,536 MiB；
- large repository：独占索引，peak <= 3,072 MiB；
- 查询期不得重新载入完整 SCIP index；
- 索引结束后不得残留 rust-analyzer/scip-rust/cargo 子进程；
- 大型仓库若因公开的单线程 SCIP 路径超过冻结时间预算，停止晋级，不在结果后放宽。

Stage 0 在第一次 Provider 输出前记录设备、仓库规模、冷热条件、重复次数和具体时间/磁盘门。资源阈值只能在看结果前冻结；若需修订，必须由用户明确批准并使用新的仓库/新协议，旧失败保留。

#### 晋级条件

- admitted facts precision = 1.000，零错误身份；
- 所有 required positives 有源位置；
- 所有 negatives 无错误命中；
- SCIP lint 对已采用事实域没有未解释错误；
- normalized answers 在至少三次运行中稳定；
- ordinary/large 资源门分别通过；
- offline、cleanliness、containment、cancel 和 cleanup 通过；
- build script/proc macro 执行边界可被产品明确表达。

决策：`ADOPT_SCIP_CORE`、`REFINE_WITHIN_FROZEN_CONTRACT`、`KEEP_EVALUATION_ONLY` 或 `STOP_RUST`。

### Stage 1：冻结 Rust Core 合同

仅在 `ADOPT_SCIP_CORE` 后开始。产出：

- Rust identity/build-context contract；
- capability matrix；
- error/reason codes；
- SCIP schema/version compatibility；
- streaming import/store schema；
- freshness 和 generation manifest；
- setup/doctor/inspect/repair/remove 行为；
- Stage 2 machine-readable acceptance manifest。

必须冻结的错误包括：

- `rust_toolchain_unavailable`；
- `rust_analyzer_incompatible`；
- `cargo_workspace_ambiguous`；
- `rust_dependencies_unavailable`；
- `rust_execution_required`；
- `rust_build_context_changed`；
- `rust_scip_invalid`；
- `rust_capability_unsupported`；
- `rust_index_budget_exceeded`。

退出：`RUST_CORE_CONTRACT_FROZEN`。

### Stage 2：evaluation importer 和事实存储

先构建不进入公开 CLI choices 的 evaluation adapter：

- streaming 读取 SCIP protobuf；
- 规范化 Node/Edge/SourceRange；
- 保存 package/crate/module/owner/signature 身份；
- 保存 upstream tool/version/arguments/protocol provenance；
- 支持 definition、references、implements；
- 对其他查询返回显式 unsupported；
- 构建不可变 Rust shard，并由 project manifest 引用；
- 失败时不替换前一健康 shard。

禁止：

- 一次性载入完整大索引；
- 用 occurrence 文本推断 call edge；
- 为通过 fixture 添加项目名特例；
- 在 importer 中执行 Cargo 或 rust-analyzer；
- 修改 Python/TypeScript 输出。

退出：`RUST_CORE_ADAPTER_READY` 或 evaluation-only。

### Stage 3：最小产品接入

实现中央语言适配器注册表，职责包括：

- detection 与 ambiguity；
- runtime requirements；
- indexer/importer factories；
- capabilities；
- lifecycle participants；
- status/doctor/inspect；
- query routing；
- explicit unsupported semantics。

先把 Python 和 TypeScript 以行为不变方式注册，再加入 Rust。禁止散落新的 `if language == "rust"` 分支。

本阶段只支持显式单一 Rust scope。混合 Python/TypeScript/Rust 仓库可以被检测并提示，但不得自动聚合。

退出要求：

- unit、integration、fault、installed-wheel 和 schema compatibility 通过；
- Python/TypeScript 冻结回归 hash 不变；
- Rust R1 全部能力通过；
- enable/update/stop/remove/rollback 不污染源码或外来配置；
- no hidden install/network/process residue。

### Stage 4：开发仓库验证

在 Stage 0 的四个开发仓库运行：

- 全部冻结 truth；
- 三次冷索引与三次热重用；
- unchanged、single-file edit、rename、delete；
- Cargo.toml/Cargo.lock/features/target/toolchain 变化；
- corruption、timeout、cancel、disk full、process kill；
- previous-generation recovery；
- exact repository/worktree isolation；
- Python/TypeScript compatibility；
- ordinary/large resource gates。

开发结果只能进入 `READY_FOR_HOLDOUT`、`REFINE_WITHIN_FROZEN_CONTRACT` 或 `KEEP_EVALUATION_ONLY`。

### Stage 5：独立密封留出

这是针对 M27 失败重新设计的门。

#### 留出准备顺序

1. 选择从未被适配器观察的两个或更多仓库和 exact SHA；
2. 使用独立脚本验证 Cargo manifests、lockfile 和 source anchors；
3. 显式执行依赖 prepare，并冻结隔离 cache inventory/hash；
4. 使用 `--locked --offline` 证明所有依赖可用；
5. 冻结至少 18 个 source-first truth cases；
6. 独立验证真值 schema：definition 行、reference occurrence、implementation edge 不得混用；
7. 先用已知 fixture 验证 lazy-start-aware process-tree sampler；
8. 冻结 runner、catalog、tool manifest、cache manifest 和 start marker；
9. 只执行一次密封运行。

#### 失败纪律

- 不替换失败案例；
- 不重标 observed output；
- 不在同一 holdout 之后补依赖；
- runner/catalog 缺陷与产品缺陷分别记录，但都阻止晋级；
- 新尝试必须使用新仓库、新真值和新授权；
- 正确性失败、availability 失败和资源失败均不可由其他成功项抵消。

通过后只允许进入 unified gate，不自动发布。

### Stage 6：统一、跨平台和发布门

至少包括：

- 完整产品与 fault suite；
- Python/TypeScript 全量兼容；
- Linux x86-64/ARM64、macOS Apple Silicon、Windows x86-64/ARM64；
- macOS Intel 仅保留历史说明，不新增构建或验收；
- reproducible package；
- clean installation、enable、index、verify、stop、resume、update、remove；
- Codex 新任务真实 `project_status`、definition、references；
- wrong-repository negative；
- process/disk/config/source cleanup；
- support matrix、known limits、third-party notices 和 rollback。

发布必须另行授权。可能结果：

- `RELEASE_RUST_CORE`：仅 R1 能力稳定支持；
- `RELEASE_RUST_CORE_AND_CALL_GRAPH`：R1/R2 同时通过；
- `KEEP_RUST_EVALUATION_ONLY`；
- `STOP_RUST`。

## 8. 测试矩阵

### 8.1 语义

- functions、methods、associated functions；
- struct、enum、trait、type aliases；
- inherent impl 与 trait impl；
- generic bounds 和 blanket impl；
- modules、re-export、renamed imports；
- same-name items across crates/modules/owners；
- declarative macro、proc macro、generated source；
- cfg、features、target-specific modules；
- unit、integration、doctest、custom harness；
- external crate symbols 和 workspace path dependencies。

### 8.2 生命周期和故障

- tool missing/incompatible；
- Cargo.lock missing/out-of-date；
- dependency unavailable offline；
- invalid SCIP/lint failure；
- index timeout/cancel/crash；
- source changes during index；
- stale build context；
- staging/manifest/state publication failure；
- previous generation restoration；
- remove/re-enable and upgrade/downgrade。

### 8.3 资源

- small/medium/large repository；
- cold/warm/no-op；
- one and multiple simultaneous projects；
- source size、SCIP size、import peak、query peak；
- process tree、file descriptors、temporary disk、cache growth；
- repeated runs and 24-hour soak only after functional gates pass。

## 9. 交付物

每阶段必须至少产出：

- plan/contract；
- exact tool and repository manifest；
- source-first truth catalog；
- machine-readable raw result；
- validator；
- concise evidence report；
- decision and next authorized action；
- known limitations and rollback description。

建议文件命名：

```text
docs/RUST_LANGUAGE_SUPPORT_PLAN.md
docs/RUST_CORE_CONTRACT.md
cases/rust-stage0-repositories.v1.json
cases/rust-stage0-truth.v1.json
cases/rust-stage2-acceptance.v1.json
reports/RUST_STAGE0_FEASIBILITY.md
reports/RUST_STAGE2_EVALUATION_ADAPTER.md
reports/RUST_STAGE4_DEVELOPMENT_VALIDATION.md
reports/RUST_STAGE5_HOLDOUT.md
```

## 10. 工作拆分与提交边界

建议使用小而可撤销的提交：

1. evaluation manifests/runners，不改产品；
2. Rust Core contract，不改产品；
3. SCIP streaming importer 和 isolated store；
4. language adapter registry，Python/TypeScript behavior-preserving；
5. Rust registration and lifecycle；
6. CLI/JSON/MCP/status/doctor；
7. fault/resource/installed acceptance；
8. documentation and release metadata。

不得把工具下载、评估 corpus、机器绝对路径、Cargo registry/cache、SCIP index 或 raw output 提交到产品包。需要保留的不可变证据使用小型 manifest/hash 和独立 artifact。

## 11. 明确不做

- 不把能解析 `.rs` 宣称为 Rust 语义支持；
- 不自行实现 Rust name/type resolution；
- 不从 reference occurrence 推断 call graph；
- 不执行用户应用或测试来生成静态事实；
- 不在查询期间联网或安装 toolchain；
- 不把缺失依赖、宏失败或 cfg 缺口显示为空结果；
- 不为了满足六类查询降低 exactness；
- 不在同一里程碑同时承诺 Rust、Go、Java 和混合语言；
- 不恢复或发布旧 M27 Go candidate；
- 不更新或新增 macOS Intel Provider；
- 不在失败后选择性重跑直到出现较低资源数字。

## 12. 完成定义

Rust Core 只有在以下条件全部成立时才算可发布：

1. exact repository/workspace/build-context identity；
2. definition/reference/implementation truth 和 negatives 全部通过；
3. SCIP 完整性、normalized determinism 和 provenance 通过；
4. ordinary/large 资源门通过且重复结果稳定；
5. offline dependency contract 和执行边界明确；
6. freshness、transaction、rollback、cleanup 和 project isolation 通过；
7. Python/TypeScript 无回归；
8. 独立密封留出一次通过；
9. 五个活跃架构完成安装态验收；
10. 文档明确列出 R1 支持和 R2/R3 未支持能力；
11. 用户另行批准合并、版本、标签和 Release。

任何较高能力层失败都不得篡改较低层证据；较低层只有通过自己的完整发布门后才能保留。计划完成不等于实现授权或发布授权。
