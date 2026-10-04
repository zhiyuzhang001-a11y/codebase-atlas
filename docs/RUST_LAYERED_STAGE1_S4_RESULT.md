# Rust Stage 1 / S4 会话核心结果

- 日期：2026-09-22
- 状态：`S4_INTERNAL_ROUTING_IMPLEMENTED_DEV_VALIDATED / PRODUCT_DISABLED`
- 上游：`RUST_LAYERED_STAGE1_S3_RESULT.md`

## 已完成

- 新增内部、generation-bound `RustAnalyzerProvider`，只提供带精确 source path/line/column 的
  definition/reference T2 查询；
- 固定 `rust-analyzer 1.98.0`，启动前验证最终可执行文件与版本；兼容 Homebrew 的受控符号链接，
  但最终目标必须是普通文件；
- 实现有界 LSP `Content-Length` framing、32 MiB frame 上限、1 MiB stderr 上限、串行 request id
  校验和 server-request 响应；
- initialization options 固定 `cargo.noDeps=true`、`cargo.features=all`，并从 generation-bound
  manifests 注入 `all_package_features` cfg；build scripts/proc macros/cache priming/check-on-save
  关闭，进程环境固定 `CARGO_NET_OFFLINE=true`；
- initialize 后同时等待 workspace loaded 与非空 crate graph，并保留最多 60 秒的
  `ContentModified`、`preload_file_not_found` 和首次空语义结果稳定化重试；
- 每次查询验证 Git snapshot、generation source hash 与 S2 scope；scope 外输入和 scope 外返回都
  fail-closed；
- LSP 零基行列转换为 Atlas 一基 `SourceRange`，返回 generation-bound T2 provenance；partial S2
  scope 不会被升级为 complete；
- session 以独立进程组启动；请求超时会终止整个 owned process tree，清空会话后才允许重启。
- `AtlasService` 新增仅供显式依赖注入的 Rust T2 路由和 session owner：按需启动、同一 service
  会话复用、关闭时回收；definition/reference 必须带精确源码位置，T3/T4 返回明确不支持状态；
  公共 runtime、CLI、MCP 和 UI 未注入该 Provider。

## 验证

- fake LSP 覆盖版本错配、初始化安全配置、ContentModified retry、T2 provenance、stale/scope
  拒绝、请求超时以及子进程树清理；
- 本机真实 `rust-analyzer/rustc/cargo 1.98.0` 临时 Git/Cargo fixture 端到端通过：definition 1、
  references 2，约 2 秒完成，关闭后无运行会话；
- 大型异构仓库留出验收发现并修复 LSP 分段 payload 读取与非标准 Cargo target-root 模块目录两个
  通用缺陷；最终全量 Python/TypeScript/Rust 回归为 507 项通过、18 项既有条件跳过，无失败；
  Python compileall、
  Rust scanner fmt/check、合同 JSON 与 `git diff --check` 均通过；
- 固定 corpus 的 `bytes`、`thiserror`、`regex` T2 查询 18/18 通过，峰值分别为 587,424、
  582,272、624,768 KiB；`libc` 在修复 S2 内联模块目录后 6/6 通过，观察峰值 672,896 KiB；
  四仓合计 24/24，均低于 1.5 GiB 且关闭后无残留；
- `libc` 的 generation 现在静态包含
  `src/new/apple/libpthread/pthread_/introspection.rs`，仍保持 scope 外返回 fail-closed，未通过动态
  放宽或查询时补录绕过 generation 文件身份约束。
- 真实 `libc` 通过 `AtlasService → RustAnalyzerProvider` 完成 definition 1 与 references 1，service
  关闭后 Provider 不再运行；Rust 路由仍未注入公共 runtime、MCP、CLI 或 UI，产品开关保持关闭。

## 尚未完成

- 固定四仓 corpus、truth、formal-v3 runner 与历史结果仍保存在机器级 Atlas 数据目录；此前因只查
  项目目录而误判为已清理，现已通过全盘查找并核对四个 Git HEAD、remote 与 clean 状态；
- S2 内联模块目录修复和 `libc` 的 6 个冻结查询重放均已完成；
- S5 sealed holdout 在不修改冻结 truth/阈值的前提下修复并重跑通过：五个大型仓库 T2 为 20/20，
  T1 均三次一致且 0 parse errors；
- wheel/Release 跨平台打包、installed-wheel、新任务 MCP 验收和显式产品启用授权尚未开始。

下一步进入 S5 生命周期、安装态和跨平台打包验收，并保持所有公共入口 fail-closed；不得因本机
fixture 通过而开放 Rust 产品语言选择。
