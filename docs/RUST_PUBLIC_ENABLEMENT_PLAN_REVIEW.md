# Rust 正式启用计划独立审查

- 日期：2026-10-05
- 审查对象：`docs/RUST_PUBLIC_ENABLEMENT_PLAN.md` 初稿
- 初稿 Git blob hash：`30e4888021d727a755f9240b60b0a926b507bf04`
- 初轮结论：需修改。以下 R1–R3 应解决后再标记计划可实施。
- 范围：计划与有界源码/合同审查；未运行产品测试，未修改产品、部署或发布。

本审查按仓库 AGENTS.md、Atlas 技能及预算化源码阅读技能执行。Atlas 查询工具
不可用，未取得 `project_status/analyze_change`，因此不声称完成全量影响分析；
使用已定位文件的有界只读检查，没有新建索引或运行时缓存。

## 原始发现

### R1 [P1] 60 秒冻结门与正常入口预算混为一谈

初稿第 131–134 行说“冷启动就绪与单查询沿用冻结 60 秒门”。冻结合同实际上只
把 `ContentModified/preload_file_not_found` readiness 重试限制为总 60 秒：
`cases/rust-layered-stage1-contract.v1.json:113` 和
`docs/RUST_LAYERED_STAGE1_PRODUCT_CONTRACT.md:119`。

当前 `providers/rust_analyzer.py:285` 版本探测有独立 5 秒预算，`:327` 的
initialize 使用调用者预算，`:365` 的 `_wait_ready` 又启动 readiness 时钟。
`service.py:268` 将查询剩余预算传给 start，但 start 内部未把上述阶段统一进该预算；
service 默认查询是 30,000 ms（`:37`），请求上限是 300,000 ms（`:125`）。
只说“不能相加”不能消除这个真实接入缺口，也不能声称已有单查询 60 秒合同。

建议：分别写明冻结 readiness 上限、查询 `timeout_ms`、首次启动及端到端阶段
deadline；明确采用较小剩余预算并让 version/init/readiness/query/cancel 共用时钟。
新增加的冷启动/端到端验收上限应标为本轮新增门，不能冒充既有冻结合同。
用延迟版本探测、initialize 和 readiness 的组合负例证明不会串联超时。

### R2 [P1] 允许的工具探测和 Cargo/rustup 执行面缺少明确防护门

初稿第 88–90、128–129 行原则上禁止联网和执行项目代码，但尚未定义怎么安全
处理冻结合同允许的 Cargo metadata/config/version、rustc `--print` 等探测
（`docs/RUST_LAYERED_STAGE1_PRODUCT_CONTRACT.md:115`）。

当前 `providers/rust_analyzer.py:185` 复制整个 `os.environ`，仅新增
`CARGO_NET_OFFLINE=true` 和 `RUST_BACKTRACE=0`；`:298` 在目标仓库 cwd 启动
analyzer。offline 标记不能单独证明项目/继承 Cargo 配置、wrapper、rustc override、
rustup toolchain override 不会执行外来程序或触发下载。这是待验证风险，并非本审查
已经观察到恶意执行。

建议：在运行任何 version/metadata/LSP 探测前冻结 env 与命令允许策略；明确处理
Cargo 项目/祖先/全局配置、`RUSTC*` wrapper/override、rustup shim 与
`rust-toolchain*`，不安全或缺工具链时应 fail closed 并给准备计划。不得通过修改
用户全局配置规避。加哨兵脚本、禁网观察与完整子进程 argv 证据，覆盖 discovery、
doctor、enable、首次查询、refresh；允许探测的具体命令必须写入验收合同。

### R3 [P1] 外部仓库与最终 draft 的平台覆盖不够明确

初稿第 112–137 行明确五平台 fixture 门，但第 146–156 行的两个外仓正常
CLI/MCP 验收没有列平台；第 168–171 行的 final draft 重跑也没有明言五平台。
因此执行者可以只在 macOS 上验两个外仓或 final draft，然后把 earlier fixture
和 scanner build 结果当成五平台最终用户交付证据。

建议：列出最小覆盖矩阵，区分五平台冻结 fixture、两个外仓以及真实桌面 Codex。
若首版承诺五平台外仓可用，则两个外仓的冻结 CLI/MCP 请求也在五平台验收；若
只做部分平台，必须缩小公开承诺并另行授权，不能默认为通过。final draft 的同一
wheel/checksum 和对应平台工具需要五平台 installed-wheel 重跑，并核对先前候选
字节/身份；真实桌面 Codex 可单独至少一次，不要求伪造五平台桌面连接。

### R4 [P2] 第四阶段“正常部署”应区分候选验收与稳定用户部署

第三阶段已正确采用临时 candidate/draft 资产注入来避免开关与验收循环依赖，但
第四阶段第 150 行改说“正常部署入口”而未明确沿用候选隔离规则。仓库 AGENTS
要求真实用户部署仅用稳定 Release，而 Rust-enabled 稳定 Release 尚未产生。

建议：明确第四阶段仍是候选验收，以正常产品命令和测试专用可验证资产源运行，
只写临时项目配置，真实 Codex 新任务的配置/启动要有用户授权。第五阶段发布后
再用官方稳定下载入口做最终用户部署验收；不能把注入候选源称为稳定交付。

## 已确认合理的部分

- `rust-analyzer 1.98.0` 与机器合同 `:108`、产品合同 `:112` 和 provider 常量
  `providers/rust_analyzer.py:30` 一致；五平台可获取性尚待实施验证，没有凭构建
  scanner 通过推断 analyzer 可得。
- 正常 CLI 工厂未注入 Rust provider、无位置查询缺 T1 fallback、旧 candidate
  acceptance 直接构造对象等缺口确实存在；计划没有把历史内部验收当成公开支持。
- 候选 wheel 在未公开分支启用、稳定版仍关闭的顺序可以实施；同一字节要求应以
  final draft 下载 SHA 比对落实。tag workflow 重建 wheel（`release.yml:20`），
  RELEASING.md 已有可重现构建规定，不能只靠候选分支名字证明字节不变。
- 降级 Rust 不当成 TS、保护 Python/TS、跨仓隔离、回滚清理、资源门、独立发布
  授权及不改旧版本资产的方向合理。

## 复审记录

### 第二轮：计划修订后复核

- 被审查修订版 Git blob hash：`69d623a3a7e959d3e4740c909025928243b8d4b3`
- 结论：原始 R1–R4 已在计划层面解决；可交用户批准实施。
- 此结论只代表计划具备实施顺序和验收约束，不代表产品实现、测试或公开启用通过。

R1 已明确合同 readiness ≤60 秒、原留出单查询 ≤60 秒（后者与
`RUST_LAYERED_STAGE1_HOLDOUT_RESULT.md:10` 一致），不再声称冷启动已有冻结门。
第一阶段必须冻结端到端/启用索引/cleanup 数值；探测、spawn、initialize、readiness
和查询共享剩余 deadline，不能扩大调用者预算，组合延迟与 cancel 有明确负例。
具体新增数值留待第一阶段冻结合理；未冻结时不得通过后续阶段。

R2 已要求运行前环境白名单、固定已验证 Cargo/rustc 身份、只读配置和 override
检查、fail closed、明确允许命令参数/cwd/env、discovery 同等约束，以及哨兵程序、
禁网观察和完整子进程证据。实现能否覆盖各种平台配置，仍必须由安全负例证明；
此处不把文字策略当作现有代码已经安全。

R3 已明确两个外仓的冻结 CLI/MCP 请求均覆盖五平台，final draft 也在五平台
重跑 fixture、两个外仓和生命周期，并比对候选 SHA-256；字节改变则重新候选
验收、不继承旧证据。真实桌面 Codex 门单列至少一次，没有与五平台 stdio 混淆。

R4 已明确第四阶段仍属临时候选隔离验收，使用正常命令但不是稳定版用户交付，
临时 live MCP 配置/新任务先获授权；第五阶段发布后另做官方稳定部署验收。

复审未发现新增计划阻断项。仍须在执行时验证五平台固定工具可获取性、冻结
具体 deadline 和允许探测清单、完成真实 live MCP 授权与验收；任一门失败按计划
停止晋级。当前只允许用户考虑实施授权，不能据本报告直接开放 Rust 或发布。
