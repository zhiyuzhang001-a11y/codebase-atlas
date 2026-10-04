# Rust Stage 1 大型异构仓库留出验收

- 日期：2026-09-22
- 状态：`HOLDOUT_PASSED / PRODUCT_DISABLED`
- 冻结目录：`~/.local/share/codebase-atlas/rust-layered-stage1-holdout/2026-09-22`

## 冻结纪律

首次候选运行前固定五个未参与开发的仓库、精确 Git HEAD、20 个 source-first T2 查询及资源门。
每仓必须满足：T1 三次逐字节一致且 0 parse errors；T2 4/4；单查询 60 秒；进程树峰值不超过
1.5 GiB；无残留；不得执行 build scripts、proc macros、应用或测试。运行后没有修改真值或阈值。

## 仓库与结果

- `serde` `6693a89c`：78 个 scope 文件，T1 3,680 facts、22,680 个仓库定义相关 identifier
  candidates，三次输出哈希一致、0 parse errors；T2 4/4，约 4.1 秒，峰值 446.8 MiB。
- `tokio` `5d5ca118`：775 个 scope 文件，T1 24,459 facts、104,557 candidates，三次一致、
  0 parse errors；T2 4/4，约 6.4 秒，峰值 668.4 MiB。
- `rustls` `39bff3d2`：148 个 scope 文件，T1 9,668 facts、36,126 candidates，三次一致、
  0 parse errors；T2 4/4，约 4.0 秒，峰值 602.5 MiB。
- `ripgrep` `3fce3b5b`：109 个 scope 文件、8,076 facts、36,990 candidates，scope
  `complete_exact`，T1 三次一致、0 parse errors；T2 4/4，约 3.4 秒，峰值 543.7 MiB。
- `datafusion` `714956b3`：1,751 个 scope 文件、136,691 facts、762,281 candidates，scope
  `complete_exact`，T1 三次一致、0 parse errors；T2 4/4，约 26.3 秒，峰值 726.7 MiB。

总计 T2 为 20/20。T1 最重仓库的原生 scanner 峰值约 187 MiB，完整 staging 峰值约 475 MiB，
均低于 T1 512 MiB 门；T2 峰值低于 1.5 GiB。五仓均无超时或残留进程，冻结门全部通过。

## 留出集发现并修复的通用缺陷

1. LSP reader 原先假定一次 `read(Content-Length)` 返回完整 payload。DataFusion 的大型 references
   响应被管道分段后误报连接关闭。现改为有界循环读取，并新增 fragmented-payload 回归测试。
2. 非 `lib.rs`/`main.rs` 的 Cargo target root 被错误地当成普通模块文件。例如
   `tests/core_integration.rs` 的 `mod datasource;` 应从 `tests/datasource/` 解析。现按 target-root
   父目录解析；DataFusion scope 从 1,575 个 partial 文件提升为 1,751 个 `complete_exact` 文件。
3. Serde 的 crate-root 模块声明位于本地 `macro_rules!` 中。scope 现在只在该宏确实从可达源码被调用时，
   才按调用目录解析宏生成的外部模块；查询文件全部进入 generation，同时未知动态 include 仍保持 partial。
4. Tokio 在完全离线 `cargo.noDeps=true` 下缺少 Cargo resolve graph。generation 现在固定绑定
   `all_package_features`，从已哈希的工作区 manifests 静态提取全部 feature 名并通过 rust-analyzer
   `cargo.cfgs` 注入；build scripts、proc macros 和网络仍关闭。readiness 还要求 crate graph 非空，
   不再仅凭 workspace 状态提前放行。
5. 最新 Rust 语法超出 `tree-sitter-rust 0.24.2` 的部分由固定 `syn 3.0.6` 做独立整文件验证；只有
   两个 parser 都拒绝才记 parse error。Tree-sitter 恢复事实仍只作为 T1 candidates。T1 shard 升级为
   字典化、固定宽度 packed candidate schema，并只保留仓库内定义相关 identifiers，使 DataFusion
   输出从 285 MiB 降至 68 MiB，完整 staging 降至约 475 MiB。

最终全量回归为 507 项通过、18 项既有条件跳过；Python compileall、Rust fmt/test 与
`git diff --check` 均通过。

## 下一门

留出集已全绿，后续 macOS arm64 installed-wheel T0/T1/T2 验收也已通过，详见
`RUST_LAYERED_STAGE1_S5_RESULT.md`。公共 Rust 路由仍保持关闭；五平台 Release workflow、全新任务
MCP 验收和单独产品启用授权仍未完成。本结果本身不等于已经发布。
