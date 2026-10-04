# Rust Stage 1 / S2 实施结果

- 日期：2026-09-22
- 状态：`IMPLEMENTED_AND_TESTED / RUST_PRODUCT_DISABLED`
- 上游合同：`RUST_LAYERED_STAGE1_PRODUCT_CONTRACT.md`
- 切片：`S2-rust-source-scope-and-generation`

## 已完成

S2 新增纯读取的 Rust T0 source-scope 层，并接入现有 generation manifest 构建、校验、
staging、publish 和 rollback 原语：

- 静态解析根 `Cargo.toml`、workspace members、package 与 Cargo targets；
- 记录所有 Cargo manifests 与根 `Cargo.lock` 的内容哈希和大小；
- 使用 Git-aware inventory 与 `.cbmignore` 约束 `.rs` 候选；
- 从 Cargo target roots 沿外部 `mod`、内联 `mod { ... }` 的嵌套目录、`#[path]` 和字面量
  `include!` 建立可达源码集合；字符串、raw string、字符、注释和无关代码块不会扰乱模块层级；
- 所有 scope 外 `.rs` 文件记录 `outside_cargo_module_scope`，不再把扩展名等同于完整 scope；
- unresolved module、缺失 target、动态 include、无效 workspace member 等返回
  `exact_hits_partial_scope`，不会静默生成完整空结果；
- Rust generation 绑定 exact repository snapshot、language、Cargo scope、source hashes、
  provider/sidecar identity 与 generation id；
- persisted scope 与 generation files 不一致、执行边界被篡改、跨语言文件、路径逃逸和重复路径
  均 fail closed；
- 候选 manifest 延续现有 fsync、原子 publish、rollback 机制，不写仓库。

## 安全边界

S2 没有运行或接入 Cargo、rustc、rust-analyzer、build scripts、proc macros、应用、测试或网络。
Rust 仍为 `public_enabled=false`，用户 CLI、MCP、UI、refresh 和 Provider 生命周期入口继续在启动
Provider 前返回 `language_not_product_enabled`。本切片没有发布任何用户项目 generation。

## 真实仓库只读试跑

使用 Stage 0B 固定仓库执行静态 scope discovery：

- `bytes`：`complete_exact`，1 manifest、1 package、16 targets、34 sources、0 exclusions；
- `regex`：`complete_exact`，7 manifests、7 packages、12 targets、219 sources、8 个明确的
  `outside_cargo_module_scope` 排除；
- `thiserror`：31 sources，因 `OUT_DIR` 动态 `include!` 返回
  `exact_hits_partial_scope`，38 个 scope 外文件显式排除；
- `libc`：461 sources；内联 `libpthread::pthread_` 等外部子模块已静态纳入。剩余宏生成模块、条件
  模块和动态 include 无法仅从静态文件安全还原，仍返回 `exact_hits_partial_scope`；41 个 scope 外
  文件显式排除。

后两项的 partial 是预期的 fail-closed 结果，不代表 T1 解析失败。S3 可以使用已资格化的 native
parser 补充结构事实，但不得把未解决的 T0 scope 提升为完整。

## 验证

- Rust scope/syntax/analyzer/refresh 定向测试：19 项通过；
- 全量：`PYTHONPATH=src python3 -m unittest discover -s tests`，522 项通过，18 项因既有
  `ATLAS_NODE` 条件跳过，无失败；
- Python/TypeScript legacy manifest 继续可读；新 manifest 增加 language identity；
- `py_compile`、JSON 校验和 `git diff --check` 通过。

## 已知限制与下一步

静态 T0 不尝试执行 cfg/build context，也不解释 macro expansion 或动态 `include!`；遇到这些
情况必须保持 partial。内联 module 的普通外部子模块路径已经支持。下一步 S3 是把已资格化的
`rust-native-syntax` scanner 产品化为版本化、content-addressed T1 shard，并只在开发 corpus
中接入事务；Rust 产品开关仍保持关闭。S3 core 的后续结果记录在
`RUST_LAYERED_STAGE1_S3_RESULT.md`。
