# Rust 分层支持 Stage 1 S5 本机安装态结果

- 日期：2026-09-22
- 平台：macOS arm64
- 状态：`LOCAL_INSTALL_PASSED / CROSS_PLATFORM_PENDING / PRODUCT_DISABLED`

## 已完成

1. 通用 Python wheel 继续保持 `py3-none-any`，并新增发布校验，要求 wheel 必须包含 Rust
   registry、scope、refresh/recovery、T1 和 T2 Python 模块。
2. `atlas-rust-syntax` 增加可验证的 `--version` 输出。
3. 原生 scanner 使用独立 bundle，不嵌入通用 wheel。bundle 构建执行两次隔离的
   `cargo build --locked --offline --release` 并要求二进制逐字节相同；manifest 绑定 scanner
   source、Cargo lock、目标平台、工具链版本、二进制 SHA-256 和大小。
4. bundle 携带 Apache-2.0 产品 LICENSE、scanner 第三方 notices 和相邻 archive SHA-256。
5. 本机 macOS arm64 bundle 两次构建一致；bundle 内 scanner 报告固定版本 `0.2.0`、
   tree-sitter-rust 0.24.2、tree-sitter 0.27.0 和 syn 3.0.6。
6. 从候选 wheel 新建隔离 virtualenv 后完成真实 Rust fixture 验收：T0/T1 refresh 成功且 scope
   为 `complete_exact`；T1 definition 1、reference candidates 2；rust-analyzer 1.98.0 的 T2
   definition 1、references 2；session 关闭后无运行进程；仓库源码未变化。
7. 验收同时断言 Rust 不在 public language choices，未修改全局 MCP/editor 设置。
8. 本轮全量 Python 回归为 528 项通过、7 项条件跳过；Rust scanner crate test 通过。

## 跨平台门

新增 `Rust Syntax Scanner Bundles` workflow，目标集合固定为 Linux x86_64、Linux arm64、
macOS arm64、Windows x86_64 和 Windows arm64，不恢复 macOS Intel。aggregate job 会重新校验
完整五平台资产集、archive checksum、inventory、source identity、binary digest、版本和 license。

该 workflow 尚未在远端 GitHub Actions 执行，因此当前不能声称跨平台通过。它也不含发布步骤；
在 Rust 仍 product-disabled 时，不上传 scanner 到公开 Release。

## 剩余门

- 在候选精确 commit 上运行并通过五平台 workflow；
- 使用最终下载资产逐平台执行 installed-wheel 验收；
- 在应用 MCP 配置发生变化后，用全新 Codex 任务验证 exact repository、doctor ready、fresh index
  和真实查询；
- 获得单独的 Rust 产品启用授权。

这些门完成前，Rust 公共入口保持关闭。
