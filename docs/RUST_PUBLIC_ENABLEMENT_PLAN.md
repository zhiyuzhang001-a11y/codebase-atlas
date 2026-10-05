# Rust 正式启用与部署收尾计划

- 日期：2026-10-05
- 状态：独立复审通过，用户已于 2026-10-05 批准按计划实施；尚未授权产品公开启用
- 基线：已发布的 Atlas 0.27.0，tag 对应 commit `6ce74df210bc4af4b1be705fb95000a46d9a3e8d`
- 目标：其他 Rust 项目通过正常安装、启用和 MCP 查询入口真正使用受限 Rust 能力。

本计划补齐内部实现到可部署产品之间的缺口，不重做已经通过的语言可行性评估，
也不把“源码合并”“构建成功”或“发布 wheel”当成 Rust 用户交付完成。
计划与独立审查完成后，用户已批准实施。实际用户项目部署、公开启用和新 Release
仍按下文授权边界执行，不因实施授权自动生效。
独立审查原始发现及复审结论见 [审查报告](RUST_PUBLIC_ENABLEMENT_PLAN_REVIEW.md)。

## 已有成果与未完成部分

0.27.0 已包含独立的 Cargo/module scope、T1 原生 scanner、T2 rust-analyzer 会话、
generation 与 refresh/recovery 模块。大型 Rust 留出集的正确性和资源结果见
[Stage 1 留出验收](RUST_LAYERED_STAGE1_HOLDOUT_RESULT.md)。这些结果可复用，但不证明
正常 CLI、安装器和 MCP 已接通。

最终 0.27.0 主分支 CI、五平台 scanner 构建/校验和 Managed Provider 构建已通过：
[CI](https://github.com/zhiyuzhang001-a11y/codebase-atlas/actions/runs/37219224031)、
[scanner](https://github.com/zhiyuzhang001-a11y/codebase-atlas/actions/runs/37219223925)、
[Provider](https://github.com/zhiyuzhang001-a11y/codebase-atlas/actions/runs/37219250587)。
这里的 scanner 门是构建及资产身份验证，不是五平台 Rust installed-wheel 语义验收。

当前缺口有直接源码依据：

- `languages.py` 的 Rust `public_enabled=False`；普通 `atlas enable` 不接受 Rust。
- `AtlasConfig` 的运行时字段仍以 Node、Codebase Memory、Serena 为中心；Rust 工具身份、
  discovery、安装 receipt 和升级兼容需要独立建模。
- `cli.py` 的普通 service 工厂仍组装 Codebase Memory、Serena 和 TS test provider，
  没有把 Rust provider 注入正常入口；已有 Rust service 方法不能单独证明接线完成。
- `simple_cli.py::_tracked_sources/_verification_candidate/_query_payload` 仍有
  Python/TS 二选一和无 source position 的验收逻辑，不能照搬给 Rust。
- `service.py::_query_rust` 对缺少位置的请求返回不可用路径；冻结合同要求可用的 T1
  definition candidates。需要明确补齐合同，不把直接调用 T1 类当成用户可见查询。
- `scripts/rust_candidate_acceptance.py` 直接构造内部对象，并断言 Rust 关闭；它保留为
  内核回归，不能充当用户正常安装、CLI/MCP 和启停升级的最终验收。
- scanner workflow 只有资格资产，没有 installed-wheel/Rust 公共生命周期门；
  0.27.0 的公开 Release 没有 scanner 资产。

这些是有界源码检查结果，尚未使用 Atlas Change Brief 证明完整调用影响范围。
实施时须按 AGENTS.md 尝试精确仓库的 Atlas `project_status/analyze_change`；不可用时
明确限制并补充相关调用与测试证据，不创建替代索引。

## 首版能力与不做的事情

沿用 [Stage 1 产品合同](RUST_LAYERED_STAGE1_PRODUCT_CONTRACT.md) 和
`cases/rust-layered-stage1-contract.v1.json`，不临时扩大语义或资源门：

- T0：Git-aware Cargo/module source scope 和仓库、构建上下文、generation 身份。
- T1：definition/syntax candidates，清楚标记语法候选，不能冒充精确语义。
- T2 definition：使用 `source_path/source_line/source_column` 的按需精确定义。
- T2 references：从唯一声明的 `target_path/target_range` 发起精确引用查询。
- scope、cfg 或依赖不足时返回明确 partial/unavailable，不宣布全仓完整。
- callers、callees、implementations、related tests、impact 返回结构化 unsupported；
  SCIP 继续 evaluation-only。不新增混合语言聚合、FFI 边或全量调用图。

缺少位置不能随便选一个同名定义。原 Python/TypeScript 参数保持向后兼容。
不把扩展名库存等同 Cargo source scope，不把宏生成/动态 include、互斥 features、
跨 target cfg 或依赖源缺失下的结果解释成无限制“完整 Rust 支持”。

## 实施顺序与阶段出口

### 第一阶段 冻结正常使用合同和失败用例

先写能在 0.27.0 上重现缺口的回归测试，固定 CLI、JSON-lines、MCP 的同一请求语义，
以及 enable/status/doctor/verify/stop/update/remove 的 Rust 结果。

定义一个小型跨文件 fixture 与一个 Cargo workspace fixture，覆盖同名符号、Unicode
列位置、相对模块、feature/cfg、生成代码边界和两个互不相属项目的身份隔离。
运行前固定真值、请求数、允许状态和资源采样方式；不在看到失败后删用例或改阈值。
引用空结果必须区分“完整 scope 下没有引用”与“缺少依赖/目标声明/分析未就绪”。

出口：可重复的失败基线、机器可读能力/错误合同和阶段测试清单；不改变公开入口。

### 第二阶段 接通工具安装与项目生命周期

为 Rust 定义语言专属 runtime hooks 和版本化配置，接入 discovery、onboard、doctor、
receipt、service factory、T1 generation、T2 按需会话及统一 refresh 路由。
纯 Rust 项目不得无条件启动或要求 Python/TS 的 Provider、Node 或 Serena。
原配置仍可读取，已有 Python/TS 项目的运行时、数据和路由不得因迁移被改写。

scanner 作为独立、五平台、带许可证和来源 manifest 的资产；rust-analyzer 固定
1.98.0，必须制定可验证的来源、校验值、平台映射和工具链依赖策略。复用合法机器
安装，拒绝版本不符、symlink/path escape、缺失或损坏资产；不为每个项目重装工具。
先确认该固定工具在所有目标上的实际可取得性，不能把“scanner 能编译”当成其证明。

工具下载/依赖准备与查询分离。查询保持 offline、build scripts/proc macros 禁用，
不得执行项目 build/check/test/run。缺依赖时给出具体诊断和只读准备方案，联网、
执行项目代码、修改全局工具链或 Cargo 配置均不得隐式发生。

实现前冻结进程环境和工具探测合同：不照搬父进程环境；只传递必要的白名单变量，
固定已验证 cargo/rustc 的绝对路径及身份。只读检查项目、祖先目录和用户 Cargo
配置、环境 wrapper/runner、rustup override 与 rust-toolchain 文件；不能证明不会
执行外来程序或自动安装工具链时 fail closed，不能用 cargo metadata/工具探测来
试着执行后再判断安全。危险配置必须拒绝或按预先记录的安全隔离策略处理，不
静默忽略会改变语义的配置，也不改写用户文件。仅设置 CARGO_NET_OFFLINE 不算防护。
验收用可观测的 wrapper/下载诱饵，证明 enable、doctor、refresh 和 query 都不触发
未经授权的程序或网络；合法缺工具/缺依赖路径给出明确诊断及单独准备授权。
允许的 version、Cargo metadata/config、rustc --print 等探测逐项列出命令、参数、
cwd 和环境；discovery 也受同一门约束。记录整个子进程树 argv 与禁网观察证据。

enable 必须识别 exact Git root/workspace，先给计划，拒绝覆盖外来配置；候选 index、
配置和 routing 同一事务发布。多 workspace 不自动混合。每个 worktree 独立身份。
update 需要旧数据兼容及失败恢复；stop/remove 只清理拥有的会话和项目状态。

出口：隔离 fixture 的内部集成、生命周期、并发和故障注入通过；生产发行仍关闭。
主要范围：`languages.py`、`config.py`、`runtime.py`、`release_installation.py`、
`onboarding.py`、`simple_cli.py`、`cli.py`、`service.py`、`mcp.py` 和 refresh/recovery。
这是计划范围，不代表必须重写每个模块；优先最小语言 hook，避免复制一套产品。

### 第三阶段 使用候选安装包做正常入口验收

新增独立 E2E harness，不以现有直接调用内部对象的脚本替代。测试者从下载包开始，
通过普通 `atlas enable --language rust`、status、doctor、verify 和 CLI/MCP 查询使用。

解决“公开开关关闭就无法验收、先公开又没有验收”的顺序问题：经实施授权后，在
尚未合并/公开的候选分支构建启用 Rust 的最终候选 wheel，只在临时测试环境验收。
公开稳定版的入口保持关闭。测试 harness 可注入明确标为 draft/candidate 的资产源，
但不能增加生产允许任意 URL、draft 或本地未验证二进制的捷径；必须校验同样的
来源、checksum、manifest、license 和版本。最终发布使用同一候选字节，不临发布换开关。

五个平台都从 wheel + 对应 scanner + 固定 rust-analyzer 安装后验证：Linux
x86_64/ARM64、macOS ARM64、Windows x86_64/ARM64；不恢复 macOS Intel。
每个平台同一冻结测试集均须通过，没有 runner/tool 则记 blocked，不记 skipped pass。

最低必测内容：

- exact repository、doctor ready、fresh index，以及 T1 候选、T2 definition/references
  的已知真值和来源；CLI 与一个全新 MCP stdio 会话都真实返回目标文件。
- 位置缺失、同名歧义、Unicode 坐标、错误目录/仓库/声明位置和不支持关系的负例；
  Rust 隔离负例使用已知外项目 source position，不照搬无位置的随机名称查询。
- .rs 源码编辑、增删/重命名、Cargo manifest/features/lock 变化后的 refresh；
  session rebind、旧 generation 的 stale policy 和一致性。
- stop 后禁止查询，resume 后可用；更新、降级及恢复行为明确，重复 remove 幂等；
  删除不影响其他项目、共享工具或源码。
- checksum/version mismatch、分析超时/崩溃、刷新中断、启动失败和并发 mutation；
  previous generation、原配置和原安装可恢复，没有 orphan 进程。
- scope partial、缺依赖、build.rs/proc macro 和危险 Cargo/toolchain 配置；
  不以外部查询结果触发联网或执行项目代码。

继续执行 T1 整个 staging 峰值 ≤512 MiB、T2 拥有的进程树峰值 ≤1,536 MiB，
合同冻结的是 readiness retry 总预算 ≤60 秒，原留出集另有单查询 ≤60 秒门；
不能声称二者已经覆盖冷启动全部阶段。第一阶段须单独冻结端到端调用 deadline：
包括工具版本探测、spawn、initialize、readiness 和查询，各阶段共用剩余预算，
重试不得重置 deadline。冷启动定义与上限、超时后的 cleanup 宽限和启用/索引预算
须在实现前明确数值及采样边界；未冻结或只测热查询不得通过第三阶段。
保留既有 timeout_ms 默认值与范围；冻结的新端到端门不得扩大请求者的预算。
用 version/init/readiness 组合延迟和 cancel 负例验证剩余预算及清理宽限。
每个平台至少三次新鲜进程重复；保留每次资源、结果、退出和源码未修改证据。
进程树采样必须包含子进程，采样缺失或 0 RSS 不能算资源门通过。

出口：五平台安装态与正常 stdio 路由全部通过，Python/TS 全量回归和 package lifecycle
同时通过。新 CI job 必须显式安装所需工具，条件跳过不能替代平台门。

### 第四阶段 验证外部 Rust 项目和真实 Codex 使用

不重复大规模性能调研。复用原留出结果，并冻结至少两个外部仓库的精确 commit：
一个独立 crate、一个多 crate workspace。选择前记录与开发 fixture/原留出集的重叠，
使用未参与本轮调试的查询作验收；失败不得更换仓库或重写真值后宣称原留出通过。
若修改 scope/parser/feature/readiness 等内核，再重跑受影响的原大型留出门。

外部 checkout 默认是临时克隆，不动用户现有项目。对每仓固定至少 4 个 definition
和 4 个 references 请求，覆盖跨文件/跨 crate、同名与 scope 边界；有合理 partial
预期的用例必须预先记录，不能把所有失败统称“允许 partial”。
两个外仓均在五个承诺平台运行同一冻结 CLI/MCP 请求集，记录平台所需依赖准备与
构建上下文；平台不适用的 cfg 用例须预先区分，不能用某平台成功替代其他平台。

第四阶段仍是候选隔离验收，沿用第三阶段的临时安装、受校验测试资产源和普通
产品命令，只写临时项目配置，不部署用户现有项目，不称为稳定版交付。真实 Codex
新任务所需临时项目 MCP 配置/启动先取得用户明确授权，不修改全局配置。

从正常产品入口安装索引后，使用全新 Codex 任务确认 live MCP：project_status 是
准确目标仓库，doctor ready，fresh generation，至少一组精确 definition/references，
以及禁止外项目事实混入的负例。fresh stdio 进程是五平台传输证据，不冒充桌面
Codex 新任务验收。新任务需要用户操作/新任务授权时明确请求，不伪造已连接结果。

出口：两个外仓正常 CLI/MCP 路由通过，加至少一次真实 Codex 新任务验收；源码与
外来配置未变，索引更新正确，停止/卸载后无所属进程残留。

### 第五阶段 审核启用决定并发布受限支持

独立 agent 根据原始证据检查四阶段出口、未知/partial 语义、安全、回归和资源门。
用户同意实施不等于同意跳过门；只有全部通过且用户明确授权公开启用，才合并
启用开关、更新公开文档和部署规则并发布新版本。版本暂不指定；不得改写 0.27.0。

需要先调整 `docs/RELEASING.md` 的 scanner 仅资格资产禁令，但新规则只能在上述
Rust 启用条件满足时允许资产发布，不能提前用旧资格包做公开安装。来源没有变化的
合法工具可复用，但 final wheel、配置 schema、源码 commit 和资产 manifest 必须一致。

按最终候选精确 commit 跑主分支/平台/安装/Provider 门。tag workflow 先创建 draft，
附 wheel/checksum、既有五平台 Provider 和新的五平台 scanner 及汇总校验。固定
rust-analyzer 的分发或可验证获取方式必须完整，不只让用户自己在 PATH 上碰运气。
从最终 draft 再下载、校验和重跑正常生命周期/语义验收，不能只测 CI 工作目录。
五个平台都必须使用 final draft wheel/checksum 与对应工具，重跑冻结 fixture、
两个外仓的 CLI/MCP 请求及生命周期。比对候选与 draft wheel 的 SHA-256；tag 构建
不得假定可复现，若字节变化须查明原因并将新包作为候选重新验收，不复用旧证据。

全部通过后 promote stable latest，再以未认证公开下载验证同一字节；对正式用户
只使用稳定 Release。不得安装 main/Actions artifact，覆盖全局配置或提交机器路径。
GitHub 等待可按用户授权设置定时跟进；未变状态安静，不持续占用会话轮询。

出口：一个外部 Rust 项目从新稳定 Release，通过官方部署流程和新 Codex 任务完成
真实查询；五平台承诺有安装态证据。发布说明明确只有 T0/T1/T2，不称全面 Rust 支持。

## 停止与回滚纪律

以下任一问题必须停止晋级：身份/来源不符、源码被意外改写、禁止进程执行、
资源超限、不可重复结果、跨项目事实、旧配置/旧 generation 丢失、无法清理的进程。
不降低门槛，不跳过失败平台，不撤销旧版资产；修复后复跑受影响门并更新证据。

Rust-only generation/schema 要明确向旧版降级的处理：若 0.27.0 不能继续服务公开
Rust 配置，回滚应恢复启用前的 stopped/unconfigured 状态或保留数据等待新版，
不得强行让旧版把 Rust 当 TS。Python/TS 仍可回滚到原验证版本。
软件版本回滚和数据事务回滚分别验证，不以更改 public_enabled 一项替代恢复。

## 证据与授权交接

每个门记录 Atlas/source/tag、wheel/工具/配置 schema 版本、平台/runner、外仓 commit、
输入请求与冻结预期、结果出处/completeness、资源与进程树、变更前后源码 fingerprint、
错误/回滚证据和 Actions run。公开文档只放可发布的摘要，原始日志与机器路径本地保存。

本计划的审查报告独立保存；发现的阻断项解决并复核前，不标记“可实施”。
完成计划及审查后交回用户确认：先批准实施受限 Rust 集成，产品公开启用仍须通过
所有门并明确批准；当前不重启旧发布 automation、不创建新 Release 或部署任何仓库。

## 实施进展（2026-10-05）

- 已开始第一阶段和受关闭开关保护的第二阶段内部集成，不代表阶段出口全部通过。
- 新增 `cases/rust-product-enablement.v1.json`：冻结数值预算、平台/传输覆盖、
  生命周期、安全探测列表和 fixture 查询真值；新增小型 crate 与 workspace fixture。
  真值坐标经过输入一致性检查后冻结；尚未做五平台真实包验收。
- 回归先复现了版本探测预算未受调用预算约束、subprocess timeout 未规范化的问题，
  已修复版本探测/initialize/readiness 共用启动 deadline，并新增组合延迟负例。
- Unicode 请求/结果坐标回归先失败，已补 T2 代码点与 LSP UTF-16 双向转换；
  T1 原有字节坐标在 index 查询边界规范化为代码点，保留 scanner 资产格式和证据 hash。
  已有单元回归，但正常安装后的全链路仍未验收。
- 内部 service 新增 T1 definition candidates 注入，拒绝跨仓 shard 和 stale generation，
  不支持关系标记 unsupported；第一批结束时正常 CLI 工厂及配置/安装/生命周期仍待接通。
- 新增非执行型 Rust runtime preflight：工具 receipt hash、环境白名单、项目/祖先/用户
  Cargo 配置、rustup override、toolchain 文件检查。第一批结束时仍是独立模块；
  后续接线进展见下述第二批记录，不能据此宣称五平台运行环境已经验收。
- Rust 配置开始支持 schema 2 的独立安装 receipt，discovery 不再查找 Node/CBM/Serena；
  原 schema 1 Python/TS 往返保持不变。配置只记录路径，不宣称 receipt 已可信验证。
  schema 2 的 exact-project config 被识别为运行时元数据，不污染源码 fingerprint。
- 官方 [1.98.0 channel manifest](https://static.rust-lang.org/dist/channel-rust-1.98.0.toml)
  与相邻 checksum 已核对，五平台 analyzer/cargo/rustc/std 和 rust-src 的来源/校验值
  固定在 packaged `rust_release_lock.json`。尚未下载/安装或验证各平台实际二进制。
- 本机早期全套回归运行 541 项，18 项因本机条件 skipped；属于本地回归证据，
  不是五平台验收。后续修改需重新回归，skipped 不计产品资格通过。
- 第一批内部集成后全套本地回归运行 553 项，18 项 skipped，无失败；
  引用查询新增声明位置匹配，空结果附 scope 状态，不把 partial scope 当完整无匹配。
  后续各阶段仍需真实工具/安装态验收，mock 回归不能替代。
- 未发布的 packaging smoke wheel 通过 `verify_release`，含运行时模块与五平台来源锁；
  SHA-256 为 `278106b6c589401db32d266aea3d9b81e35d9c22c400b28b8c795ac3ec08f369`。
  此包公共 Rust 仍关闭，只用于打包检查，不是 0.27.0 已发布资产或最终 Rust 候选。
- Rust `public_enabled` 仍为 false；本批只在新开发分支做本地提交，不推送或修改 Release/tag，
  未部署现有项目，
  未改全局 MCP/editor。原 0.27.0 发布 heartbeat 不重启。
- 第二批新增官方组件 archive 与现有安装逐文件比对，包括三个工具、动态库、sysroot、
  rust-src 和许可证；archive 必须命中 packaged source lock。receipt 原子发布、不覆盖
  已有不同安装，读取时重新检查安装文件；正常工厂只接受账户独立私有 store，拒绝
  项目内 store/工具链及错误平台。当前验证用合成 archive，尚未证明本机实际工具可信。
- Rust service 工厂绑定 exact project、同一 generation 与 checksum-pinned T1 shard，
  T2 必须获得 verified runtime；版本探测及 analyzer 启动前执行安全 preflight。
  CLI 将 Rust 路由与原 Node/CBM/Serena 工厂分开，schema 2 不要求这些旧依赖。
  公共入口仍返回关闭开关；测试中模拟开启只验证路由，不是产品启用或真实 MCP 验收。
- 安装/doctor/刷新/升级降级卸载仍未接通；真实官方工具比对、Windows ACL、进程清理
  时限以及五平台 installed-wheel/CLI/MCP 验收仍是未完成项。不得据此部署现有 Rust 项目。
- 第二批接线中全量回归曾发现旧内部 Rust config 在检查 receipt 时提前退出；
  修复为先保留语言产品 gate，真正构建 Rust service 时强制可信 receipt。
- 修复后全套本机回归运行 570 项，552 项通过、18 项 skipped，无失败；跳过项不计
  产品资格通过。新的未发布 packaging smoke wheel 通过 `verify_release`，包含 receipt
  与 Rust 工厂模块，SHA-256 为
  `50e5e1a5dc9b4806eb8a7f5b66c6042d158dde5290251b221fea709615756677`。
  仍是关闭 Rust 的内部打包检查，不替换已发布 0.27.0，也不是最终候选验收。
- 第三批接通 Rust 专属 runtime checks 与 doctor：先验证 receipt 和安全配置，再执行
  cargo/rustc/analyzer 的版本探测，三者与 preflight 共用 30 秒检查预算，失败即停止；
  公共开关关闭时不执行 Rust 探测。doctor 不再要求 CBM 数据库或共享 Provider root。
- CLI 的 operational status 开始按显式 Rust language 检查 generation/T1 artifact，
  不因不存在 CBM 数据库误报 rebuild。该检查仅证明索引身份/完整性，不证明 T2 ready；
  maintenance inspect、正常 enable/update/refresh 和安装生命周期仍需后续接通。
- 真实本机官方组件比对通过：macos-arm64 的五份锁定 archive 对照已有 1.98.0 安装，
  共 3766 个文件、10 份许可证一致。三个工具 SHA-256：
  cargo `1de2e84c15443b70444eecfa959ff9099dd8c1a5606b6d9ef5bc0ea9c25bc7f9`；
  rustc `a11618eca0956a8aa4372c2bc898690b513cbdfa2cb9125b2a5301e360ed5b49`；
  analyzer `387dd2602eea1162387c03936b0ddb5f422c2d611a4c7a11d7d32684ed83dbb0`。
  新增离线核验脚本，不执行工具、不重装、不发布 receipt；约 100 MiB 下载仅为临时证据。
  这不是正常 installed-wheel、语义查询、其余四平台或真实 MCP 的验收。
- analyzer 请求锁等待计入请求 deadline；shutdown、等待退出、强制清理共享 10 秒 grace，
  超限未退出时报错并关闭管道，不伪装成功。POSIX 父进程先退出时仍清理其进程组，
  新增真实 worker 存活负例。Windows parent 已退出时的 worker ownership/ACL，以及
  五平台实际时限测量仍未完成，不能把模拟测试当作这些出口通过。
- 第三批全量本机回归运行 579 项，561 项通过、18 项 skipped，无失败；临时关闭 Rust
  的 smoke wheel 通过 `verify_release`，SHA-256 为
  `6993f8cdcaa8308eea66ceac27ae5826fde2408aed476e3ee45a8f803d58cc7a`。
  仍未推送公开启用开关、未发布新版本、未改变任何现有项目部署或全局连接。
