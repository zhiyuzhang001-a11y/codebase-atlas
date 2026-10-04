# Rust 分层支持 Stage 0B 决策

- 日期：2026-09-22
- 结论：`LAYERED_STAGE0B_QUALIFIED`
- 产品状态：`PRODUCT_ENABLEMENT_NOT_AUTHORIZED`

冻结的 `bytes`、`thiserror`、`regex`、`libc` 开发语料已经证明：Rust 原生 Tree-sitter
T1 Provider 和受限 rust-analyzer T2 definition/reference Provider 可以进入下一阶段的产品
合同设计。本结论不包括 T3 implementation/call hierarchy、T4 SCIP 或任何产品发布资格。

正式采用 `formal-v3`。第一次并行运行暴露 rust-analyzer 启动期 `ContentModified`；v2 暴露
runner 漏掉 readiness wait。两次失败证据均保留。v3 只加入最多 60 秒的有界 readiness
重试并顺序执行仓库，没有改动冻结查询、目标、计数阈值、安全配置或语料提交。

- T1：24/24 source-range truth、12/12 同名身份隔离；每仓库三次输出逐字节一致；最大进程树
  155,520 KiB，无子进程、无残留。
- T2：12/12 definition、12/12 references；最大进程树 687,760 KiB；正常和超时清理均无残留。
- 安全：offline、`cargo.noDeps=true`、build scripts 和 proc macros 禁用；只观察到 Cargo
  workspace/config/version/metadata 与 `cargo rustc --print cfg/target-spec-json` 工具链探测，
  没有 build/check/test/run、build script、proc-macro server、应用或测试执行。
- 完整性：四个仓库仍为冻结提交且 Git clean，无 repository-local target/analysis cache。
  `libc` 的两个 parse errors 精确对应非编译模板和故意 invalid-syntax fixture。

下一门是 Layered Stage 1 产品合同。首版合同只能考虑 Rust detection/Cargo scope、T1 语法
候选和 T2 definition/reference；T3/T4 必须继续明确标为 unsupported/evaluation-only。
产品实现、配置启用、合并和发布仍需另行授权。
