# Rust 分层支持 Stage 1 产品合同

- 日期：2026-09-22
- 前置结论：`LAYERED_STAGE0B_QUALIFIED`
- 当前状态：`CONTRACT_FROZEN_S5_LOCAL_INSTALL_PASSED_CROSS_PLATFORM_PENDING_PRODUCT_DISABLED`
- 机器可读合同：`cases/rust-layered-stage1-contract.v1.json`

## 1. 本阶段交付边界

Stage 1 只冻结产品接口、能力矩阵、安全边界、迁移顺序和验收门，不实现或启用 Rust。
第一版可进入后续实现的能力只有：

- T0：Git-aware Cargo/module source scope 与 exact repository/generation identity；
- T1：Rust definitions、owners、imports、impl/test/macro 形态和 identifier candidates；
- T2：按需 rust-analyzer definition/reference 确认。

implementations、callers、callees、related tests、impact 属于 T3，全部返回 `unsupported`。
SCIP 属于 T4，继续 `evaluation_only`。T1 名称候选不得推导为 exact caller、impact 或 trait
implementation。

## 2. 现有产品约束与必要变化

当前代码不是语言注册表架构：语言集合分别写在 CLI choices、`AtlasConfig.discover`、
onboarding、runtime checks、refresh planner、simple lifecycle 验收和 Serena adapter 中。
直接把 `rust` 添加到各处分支会继续扩大分叉，也容易让现有“非 Python 即 TypeScript”逻辑把
`.rs` 误当作 JS/TS。

因此第一个实现切片必须引入单一、不可变的语言能力注册表。注册表至少声明：

- language id、source extensions、manifest/config inputs；
- T0/T1/T2/T3/T4 各能力状态；
- source inventory、structural provider、semantic provider 的工厂或明确 unavailable；
- build context、安全策略、resource limits 和 provider versions；
- lifecycle、refresh、verification 和 packaging hooks。

未知语言必须 fail closed。Python 和 TypeScript 的现有行为由兼容性测试锁定，不允许在同一
切片中重写查询算法。

## 3. 查询合同

所有 Rust 响应必须包含 repository identity、generation、`fact_tier`、provider、provider
version、source range 和 completeness。去重身份必须包含 repository、package/crate、path、
range、owner 和 provider identity。

允许状态固定为：

- `complete_exact`
- `exact_hits_partial_scope`
- `syntactic_candidates`
- `unsupported`
- `unavailable`
- `stale`

空数组只有在 required scope 已验证完整时才能表示“没有结果”。其余情况必须返回 partial、
unsupported、unavailable 或 stale，并附原因。

### Definition

rust-analyzer 的 goto-definition 是 position-based，而当前 Atlas 查询只携带 symbol、
`target_path` 和 `target_owner`。Stage 1 增加可选的 `source_path`、`source_line`、
`source_column`：

- 三者齐全且 build context 完整时，T2 可以返回 exact definition；
- 没有 source position 时，只返回 T1 `syntactic_candidates`；
- position 不唯一、cfg scope 不完整或 LSP 尚未覆盖时，返回
  `exact_hits_partial_scope`，不得挑一个同名定义冒充唯一身份。

这些新参数对现有 Python/TypeScript 查询保持可选，旧调用继续有效。

### References

T2 references 必须从唯一声明位置发起。请求至少需要 symbol、`target_path` 和
`target_range`；owner/package/crate 信息参与消歧。若 T1 无法唯一解析声明，返回 partial，
不得对所有同名标识符做名称合并后标为 exact。

### T3/T4

Rust callers、callees、related tests、impact、implementations 和 SCIP 不进入首版路由。
服务层必须在启动不相关 Provider 之前返回结构化 `unsupported`；不能沿用当前结构 Provider
的结果，也不能返回一个无 completeness 的空数组。

## 4. T0 与 source scope

`.rs` 扩展名只用于候选发现，不代表 Cargo/module source scope 完整。T0 必须组合：

- exact Git repository、HEAD/worktree fingerprint；
- `Cargo.toml` workspace/package/target 信息与 `Cargo.lock` identity；
- Git-aware inventory 和 ignore 规则；
- Cargo target、module path 与显式 exclusion reason；
- 每个输入文件的 content hash、size、language 和 generation。

Jinja 模板、生成输入和 intentional invalid-syntax fixture 必须显式排除并记录原因，否则整体
scope 为 partial。不得因为文件名以 `.rs` 结尾就把 parse error 记作产品索引失败，也不得
静默忽略。

generation manifest 需要从现有 Python/TypeScript 二选一语言字段扩展为注册表驱动的语言
验证；Rust generation 使用独立 provider/sidecar identities，不能覆盖其他语言的索引事实。

## 5. Provider 合同

### T1 `rust-native-syntax`

- 固定 `tree-sitter` 0.27.0、`tree-sitter-rust` 0.24.2，并用 `syn` 3.0.6 独立验证
  Tree-sitter 恢复的新语法；
- 只读取源码，不启动 Cargo、rustc、rust-analyzer 或项目进程；
- 输出 deterministic、content-addressed shard，存放在项目 data dir，不写仓库；
- 每条结果明确为 `syntactic` 或 `candidate`；
- 单仓库峰值不超过 512 MiB，取消/超时后无残留。

### T2 `rust-analyzer-lsp`

- 固定 rust-analyzer 1.98.0；
- `cargo.noDeps=true`、Cargo offline、`cargo.features=all`，并从 generation-bound manifests
  静态注入全部 workspace package feature cfg；build scripts disabled、proc macros disabled；
- 允许 Cargo workspace/config/version/metadata 与 `cargo rustc --print cfg`、
  `--print target-spec-json` 工具链探测；
- 禁止 build/check/test/run、非 `--print` 的 cargo rustc、build script、proc-macro server、
  应用和测试执行；
- `ContentModified` 和 workspace preload `file not found` 只允许在 60 秒总 readiness budget
  内重试；超限返回 unavailable/partial；
- 单仓库进程树峰值不超过 1,536 MiB，所有子进程归 session 所有并可清理。

T1 shard 和 T2 session 分离。T2 crash、timeout 或 unavailable 不得破坏已发布 T1 generation。

## 6. 实现切片

### S1：语言注册与合同类型

集中现有语言选择，扩展 QueryRequest/QueryResponse 与 Node provenance，并保持 schema 的向后
兼容读取。首先锁定 `contracts.py`、`config.py`、CLI/onboarding/runtime 的回归测试。

### S2：Rust source scope 与 generation

实现 Git-aware Cargo inventory、显式 exclusion、manifest identity 和事务式 publication。
该切片不连接 rust-analyzer，也不改变查询结果。

### S3：T1 Provider

把已资格化的 Rust-native scanner 变成版本化 Provider/shard，接入 refresh transaction 与
T1 candidate query。只在开发 corpus 运行，产品语言开关仍关闭。

### S4：T2 session 与路由

实现 owned process-tree、LSP framing、readiness、position query、timeout/cancel 和 provenance
归一化。definition/reference 单独启用，T3/T4 保持 fail-closed。

### S5：生命周期、打包与验收

验证工具发现、版本锁定、安装态、跨平台、旧配置读取、rollback、sealed holdout 和新 Codex
任务 MCP 验收。只有全部通过并获得显式授权，才允许把 Rust 加入用户可见语言 choices。

## 7. 验收与回滚

每个切片必须同时通过：自身合同测试、全量 Python/TypeScript 回归、仓库 clean 检查、资源与
进程安全检查。后续 sealed holdout 不得修改阈值或查询真值。

任一 provider identity mismatch、source snapshot race、资源超限、禁止进程、timeout/crash 或
generation validation failure 都必须回滚候选 Rust generation，并保留上一个有效 generation、
项目配置以及 Python/TypeScript generation。Rust feature registration 是独立回滚单元。

## 8. 下一步

S1–S4 已完成。S5 大型异构仓库 sealed holdout 已通过，结果见
`RUST_LAYERED_STAGE1_HOLDOUT_RESULT.md`；macOS arm64 上的独立原生 scanner bundle、校验和、
installed-wheel T0/T1/T2 验收也已通过，结果见 `RUST_LAYERED_STAGE1_S5_RESULT.md`。

Rust 产品开关仍关闭。五平台 scanner workflow 尚未在 GitHub Actions 执行，全新 Codex 任务 MCP
验收和单独产品启用授权也尚未完成。因此当前结论只是本机 S5 安装态通过，不是跨平台通过，
更不是已发布或已启用。
