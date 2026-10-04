# Rust Stage 1 / S1 实现计划

- 状态：`IMPLEMENTED_AND_TESTED / RUST_PRODUCT_DISABLED`
- 上游合同：`RUST_LAYERED_STAGE1_PRODUCT_CONTRACT.md`
- 切片：`S1-language-registry-and-contract-types`

## 目标

建立集中式语言能力注册表与向后兼容的查询/provenance 类型，使后续 Rust T0/T1/T2 可以接入，
但不启用 Rust、不启动 Rust Provider，也不改变 Python/TypeScript 查询结果。

## 变更范围

1. 新增 `src/codebase_atlas/languages.py`：
   - 不可变 `LanguageSpec`；
   - `python`、`typescript`、`rust` 三个规范；
   - source extensions、manifest inputs、capability 状态和 public-enabled 标志；
   - `get_language()` 对未知语言 fail closed；
   - CLI 只能读取 public-enabled choices，Rust 初始为 false。
2. 修改 `config.py`：
   - `AtlasConfig` 初始化和 load 使用注册表验证 language；
   - 旧 schema/config 字节保持可读；
   - discovery 默认逻辑保持 TypeScript-then-Python，不自动选择 Rust。
3. 修改 `cli.py` 与 `onboarding.py`：
   - 替换重复的 Python/TypeScript choices 来源；
   - 不把 Rust 暴露到用户 choices；
   - 所有现有参数和默认值保持不变。
4. 修改 `runtime.py`：
   - 由注册表分派现有 Python/TypeScript 检查；
   - Rust 返回 feature-disabled/not-applicable，不探测或安装工具。
5. 扩展 `contracts.py` 和 `service.py` 的数据合同：
   - 新增不可变 `EvidenceProvenance`，Node 以默认 `None` 的可选字段承载；
   - Rust provenance 必须包含 `fact_tier`、provider/version、generation 和 completeness；
   - QueryRequest 接受可选 `source_path/source_line/source_column/target_range` 并严格校验；
   - QueryResponse 增加默认 `None` 的 status、completeness；
   - 本切片不改变任何 Provider 路由或现有结果内容。
6. `mcp.py` 与 CLI serializer 只做条件字段透传：
   - 旧字段保留；
   - provenance/status/completeness 只在非 `None` 时输出，现有语言序列化字节保持不变；
   - schema version 暂不提升，新增字段必须是 additive；
   - 现有 Python/TypeScript snapshot/contract tests 锁定输出兼容性。

## 明确不做

- 不添加 `.rs` 到 refresh inventory；
- 不加入 rust-analyzer、Tree-sitter Rust 或 Cargo 执行；
- 不创建 Rust generation/shard；
- 不改变 Provider 打包或安装；
- 不启用 Rust CLI/onboarding 选择；
- 不实现 callers/callees/related tests/impact/implementations/SCIP。

## 最小测试集

- 新增 `tests/test_languages.py`：注册表唯一性、能力矩阵、unknown fail-closed、Rust 非公开；
- `tests/test_config.py`：旧配置 round-trip、未知语言拒绝、默认发现不变；
- `tests/test_contracts.py`：新增 provenance 默认/校验和 repository-relative position paths；
- `tests/test_service.py`：新参数校验、旧 QueryRequest 行为不变、Rust 未启用时不启动 Provider；
- `tests/test_mcp.py`：additive serialization 和旧工具 schema 兼容；
- `tests/test_onboarding.py`、`tests/test_runtime.py`、`tests/test_cli_operations.py`：choices、默认值和
  runtime 路由回归。

先运行上述定向测试，再运行完整 `pytest`。任何 Python/TypeScript 输出、调用次数、默认选择或
生命周期行为变化都使 S1 失败。

## 验收门

- 注册表成为语言元数据的唯一来源；
- Rust capability spec 可读取但 public-enabled=false；
- 未出现 Rust/Cargo/rust-analyzer 子进程；
- 旧配置可读，未知语言明确报错；
- QueryRequest 新位置参数成组校验，旧请求继续通过；
- Node/QueryResponse 新字段为 additive 且旧语言为 `None` 时不序列化，现有响应保持不变；
- 定向和全量测试通过，工作树只包含授权文件。

## 回滚

S1 是单独提交/PR 单元。失败时完整撤销注册表和 additive contract fields，不触碰任何已发布
generation、用户配置或 Provider 安装。后续 S2 不得在 S1 未验收时开始。

## 实施结果（2026-09-22）

S1 已按本计划完成并通过验收：

- `python`、`typescript`、`rust` 已进入同一不可变注册表，未知语言 fail closed；
- Rust 仍为 `public_enabled=false`，所有用户 CLI choices 均不包含 Rust；
- 即使手工写入内部 Rust 配置，index/query/MCP/UI 也会在 Provider 启动前返回
  `language_not_product_enabled`；
- Rust runtime check 不执行子进程，也不做 Node、Codebase Memory、Serena 或 Rust 工具路径探测；
- 未添加 `.rs` refresh inventory，未添加 Cargo、rust-analyzer、Tree-sitter 或 Rust Provider；
- provenance、position/range 和 completeness 字段均为 additive；旧 Node 响应在 provenance 为
  `None` 时保持原有序列化形状；
- `PYTHONPATH=src python3 -m unittest discover -s tests`：500 项通过，18 项因既有
  `ATLAS_NODE` 条件跳过；没有失败。

本结果只完成 S1 基础合同，不表示 Rust 已可用。S2 后续已获授权并完成，其结果记录在
`RUST_LAYERED_STAGE1_S2_RESULT.md`；Rust 仍未连接 rust-analyzer，也未开放产品开关。
