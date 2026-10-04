# Rust Stage 1 / S3 候选结果

- 日期：2026-09-22
- 状态：`S3_TRANSACTION_IMPLEMENTED_DEV_VALIDATED / PRODUCT_DISABLED`
- 上游：`RUST_LAYERED_STAGE1_S2_RESULT.md`

## 已完成

- 新增固定依赖的 native scanner：`tree-sitter` 0.27.0、`tree-sitter-rust` 0.24.2；S5 留出后
  升级为 provider 0.2.0，并加入固定 `syn` 3.0.6 整文件语法验证；
- scanner 不再自行递归寻找 `.rs`，只能读取 S2 已验证的 exact `source_paths`；
- scanner 拒绝绝对路径、父目录、反斜杠路径、scope 外文件和非 `.rs` 文件；
- Python T1 Provider 固定校验 provider/version/engine、精确文件覆盖、parse-error 一致性、事实类型、
  source range 与 scope identity；
- shard 绑定 repository、project、generation、source fingerprint、T1 provenance、scope
  completeness 和 scanner SHA-256；
- shard 写入项目 data dir 的 staging，fsync 后使用 content-addressed 文件名发布，并支持失败回滚；
- 新增只读 `RustSyntaxIndex`，提供 definition/reference 的 `syntactic_candidates`，不会把候选标为
  exact，也不会推导 callers、impact 或 implementations；
- provider 0.2.0 只保留仓库内定义相关 identifier candidates，并以 path/name/owner 字典加固定宽度
  packed range 存储；DataFusion shard 为 68 MiB，完整 staging 峰值约 475 MiB；
- scanner runtime 环境显式设置 Cargo offline，scanner 本身不调用 Cargo、rustc、网络、build
  scripts、proc macros、应用或测试。
- 新增内部 `RustRefreshCoordinator`，复用 project refresh lease，把 immutable T1 shard、T0
  generation manifest、current pointer 和 index state 作为一个 publication transaction；
- 独立 Rust recovery journal 在 acceptance point 之前恢复旧 manifest/pointer/state 并删除新 shard，
  在 state 已发布后接受完整新 generation；journal identity、backup path 和 shard path 均 fail-closed；
- scanner 启动前与 native 端双重限制 100,000 source files、单文件 64 MiB、总 source 512 MiB，
  Provider 另保留 300 秒硬超时与 256 MiB 输出上限；
- 以上 coordinator/recovery 仅为内部开发接线，没有加入 service、MCP、CLI 或用户可见语言开关。

## 开发 corpus 结果

本机使用 Rust/Cargo 1.98.0 离线构建候选 scanner，并仅扫描 S2 scope：

- `bytes`：34 files、1,797 facts、0 parse errors、scope `complete_exact`；
- `thiserror`：31 files、815 facts、0 parse errors、scope `exact_hits_partial_scope`；
- `libc`：461 files、65,773 facts、0 parse errors、scope `exact_hits_partial_scope`；
- `regex`：219 files、11,857 facts、0 parse errors、scope `complete_exact`；
- Stage 0B truth：24/24 精确命中；
- 同名/path/owner negative identity groups：12/12 通过。

`libc` 的模板与 intentional invalid-syntax fixture 已由 S2 显式排除，因此 T1 不再把它们报告为
产品解析错误。partial scope 仍原样保留，没有因 T1 扫描成功而升级为 complete。

## 验证

- `cargo fmt --check` 通过；
- `cargo check --locked --offline` 与 `cargo test --locked --offline` 通过；
- Python Provider/shard/query、资源拒绝、事务 publication、注入失败 rollback、pre/post-acceptance
  crash recovery 定向测试通过；
- 使用离线构建的真实 scanner 完成临时 Git/Cargo fixture 端到端 refresh，T0 manifest、T1 shard
  pointer 与 generation id 一致（1 file、2 facts、3 identifier candidates）；
- 全量测试在 S4 内部路由接线后为 522 项通过，18 项既有 `ATLAS_NODE` 条件跳过，无失败。

## 尚未完成

固定四仓同提交 corpus 已用当前 release scanner 重放：三次输出逐字节一致，24/24 truth、12/12
负身份组通过；最大峰值为 `libc` 107,488 KiB，远低于 512 MiB，四仓均无超时、子进程或残留。
旧 validator 对 `libc` 两个故意不可编译文件报告预期差异，因为当前 S2 已明确将它们排除，当前
scope 下 0 parse errors 符合新合同。

S3 transaction 与正式资源门已通过。scanner 还未进入 wheel/Release 跨平台打包，Rust 也没有
接入公共 service、MCP、CLI 或 lifecycle；不得提前开放 Rust 产品开关。
