# Rust 正式启用尝试收尾复盘与材料索引

日期：2026-10-09。结论：当前路线已停止，Rust 公共入口继续关闭。

这次尝试未能交付可公开启用的 Rust 支持。最后一次试验在入口判定阶段被阻断：完整监督入口的来源认证、实际加载闭包与角色准入没有闭合，不能在既定安全边界内进入新语义核心的真实编译和查询。结论是当前路线未完成，不是 Rust 技术被证明不可行，也不是新核心已经编译或语义验证失败。

## 准确基线和关闭状态

受测源码基线为 [`a296f46358173b8ce005ddfc2032c8132517efea`](https://github.com/zhiyuzhang001-a11y/codebase-atlas/tree/a296f46358173b8ce005ddfc2032c8132517efea)。原开发分支为 `codex/rust-public-enablement`，对应 [Draft PR 21](https://github.com/zhiyuzhang001-a11y/codebase-atlas/pull/21)。本次收尾只增加文档，不改变该基线的实现或执行门；后续文档 commit 不是新的受测产品资格。

stable 0.27.0 的 Rust 公共入口保持关闭。新核心 C0/C1/C2 与正式启用阶段 2 未全部通过，阶段 3 至 5 未进入。`atlas-rust` 和旧 `atlas-0-27-0` 自动跟进保持暂停；保留代码、证据或剩余试验额度不构成重新启动授权。

## 路线经过与可保留成果

早期分层实现保留了 T0/T1、原生语法 scanner、内部 T2 适配及生命周期、身份、刷新/恢复和受控工具准备工作。早期本机 installed-wheel fixture 与五平台 scanner 构建存在范围明确的通过记录，见 [S5 结果](RUST_LAYERED_STAGE1_S5_RESULT.md)。它们不能替代后来的隔离路线或新核心产品资格。

正式启用路线发现，官方 rust-analyzer 并不自动满足本产品对项目代码执行、子进程、参数、网络和宿主资源的精确控制合同。随后研究官方工具桥接、平台隔离和 Atlas 自有语义 guest/可信宿主路线。各计划经过独立审查，但计划通过不等于实现或平台通过。

新核心准备取得了固定上游源码、保守 registry 源码库存、惰性 WASM target 和较窄的 Linux exec 观察证据。C0-G 的 181 registry 集合是保守源码出口，不是 Cargo 实际解析图；C0-O 的受控执行证据不是 metadata/build 授权。源码 acquisition 的超限和网络失败均保留，不能把 partial 获取改称整批成功。详细经过见 [C0 源码证据](RUST_SEMANTIC_C0_EVIDENCE.md)。

监督组件和完整入口草稿、AST/语法检查与 mock 回归也保留；真实完整入口的正常路径及 controller 死亡恢复没有通过资格。回归 CI 成功不证明该入口、新语义核心构建或冻结查询已可用。

## 最终阻断项

- bootstrap 的 `APPROVED_MANIFEST_SHA256` 未设置，来源与 artifact 加载门仍关闭，公开入口不能运行。
- Python、loader、libc/libpython、stdlib/native 扩展和固定 true 工具的外部来源认证与实际加载文件对应尚未闭合。包数据库、HTTPS、自采摘要和 CI image 名称不能代替该证据。
- kernel/procfs、单线程、唯一回收者和各角色 FD 的完整原生对账仍缺失，专用 launcher 不能凭预算批准或调用者传入 hash 解锁。
- 固定 true 路径与只读 FD 的权限绑定兼容项未决，不能静默放宽规则或用临时副本冒充固定路径资格。

这些不是等待现成测试结束即可解决的问题。完整入口需要进一步实现和认证，超过最后一次试验的入口止损条件。因此独立 reviewer 与主 agent 判定停止，而非继续投入零散准备。

最终入口判定中没有启动完整原生正常/死亡实验、Cargo metadata、新核心编译或原冻结 T2 查询。早期其他内部查询成功与这组未启动项目属于不同范围，不能互相替代。准确源码位置与当时审查结论见 [最后一次尝试结果](RUST_LAST_ATTEMPT_RESULT.md)。

## 失败经验

以下是根据本次过程形成的工程复盘，不是已证明的通用技术定律。

1. **先证明最小闭环入口可执行，再扩展库存和 helper。** 下一次应优先回答完整正常路径与死亡恢复能否在目标平台运行；组件数量、静态审查与 mock 数量不能作为距真实原型还有多久的依据。
2. **在计划开始时说明可信计算基和平台代价。** “不执行项目代码”与“编译可信上游核心”是不同角色；可信宿主、上游 build scripts/proc macros、compiler/linker 仍有执行面。官方来源也不等于已满足隔离合同。
3. **把来源、执行许可和运行结果分开记录。** 源码 checksum、保守依赖库存、真实 resolved 图、准确命令卡、已运行构建和语义查询是不同证据，不能以较早出口替代较晚出口。
4. **按实际可执行里程碑估时。** 未闭合入口时不承诺编译或产品日期。把准备账本与语义资格分开，及时报告“仍在准入准备”，不把 CI 等待描述为编译进展。
5. **预算跨轮累计并在新架构出现时重新决策。** 原型从 480 分钟扩至 600 分钟后仍未闭合入口；最后一次新增额度在入口阶段提前停止，未用时间不能自动投入同类准备。不是每次新增 helper 都值得继续。
6. **失败或阻断也应成为可获取的交付物。** 保留准确源码、关闭门、独立审查、原始失败与清理证据和最终结论；最终复盘不能只留在聊天或依赖有保留期限的 Actions artifact。

## 预算与最后一次裁决

历史原型账本累计为 600.06 分钟，最后一次入口核查、独立审查与裁决保守记 6 分钟，累计 606.06 分钟。最后一次新增上限 240 分钟，其中 234 分钟因提前停止没有继续使用。该数值是既有账本记录，不是本次文档整理重新计时或新增研发授权。

最后一次的结论为 `BLOCKED`，不是 `BUILD_FAILED` 或 `SEMANTIC_FAILED`。其计划、入口停止条件和独立裁决已结束。对 Rust 的技术可行性保持未知；对当前路线是否已交付则明确为没有。

## GitHub 材料索引

未来重新阅读时建议先读本复盘，再按问题查阅以下材料。历史文件中的“下一步”和通过记录只适用于其当时阶段，不覆盖本次关闭结论。

- [最后一次试验计划](RUST_LAST_ATTEMPT_PLAN.md)与[最后一次结果](RUST_LAST_ATTEMPT_RESULT.md)：最终停止条件、阻断项与未知范围。
- [正式启用总计划](RUST_PUBLIC_ENABLEMENT_PLAN.md)与[独立审查](RUST_PUBLIC_ENABLEMENT_PLAN_REVIEW.md)：冻结能力、五平台和公开启用门。
- [官方工具桥接研究](RUST_OFFICIAL_TOOL_BRIDGE_RESEARCH.md)、[桥接计划](RUST_OFFICIAL_TOOL_BRIDGE_PLAN.md)与[桥接审查](RUST_OFFICIAL_TOOL_BRIDGE_PLAN_REVIEW.md)：原工具与安全边界之间的差距。
- [执行隔离计划](RUST_EXECUTION_ISOLATION_PLAN.md)与[隔离审查](RUST_EXECUTION_ISOLATION_PLAN_REVIEW.md)：快照绑定、精确 exec 合同与负例证据要求。
- [语义核心计划](RUST_SEMANTIC_CORE_PLAN.md)与[核心审查](RUST_SEMANTIC_CORE_PLAN_REVIEW.md)：自有 guest/可信宿主、ABI、资源限制与实例化前防护。
- [C0 源码证据](RUST_SEMANTIC_C0_EVIDENCE.md)、[构建执行面](RUST_SEMANTIC_BUILD_SURFACE.md)、[guest 源码卡](RUST_SEMANTIC_GUEST_SOURCE_CARD.md)与[metadata 卡](RUST_SEMANTIC_METADATA_CARD.md)：源码/库存出口与尚未真实解析或构建的区别。
- [观察能力卡](RUST_SEMANTIC_OBSERVER_PROBE_CARD.md)与[ptrace 控制卡](RUST_SEMANTIC_PTRACE_CONTROL_CARD.md)：受控 probe 与完整监督入口之间的剩余门。
- [分层协议](RUST_LAYERED_SUPPORT_PROTOCOL.md)与[S5 历史结果](RUST_LAYERED_STAGE1_S5_RESULT.md)：早期实现的能力范围，不等于新核心资格。

入口源码按受测基线保存：[bootstrap](https://github.com/zhiyuzhang001-a11y/codebase-atlas/blob/a296f46358173b8ce005ddfc2032c8132517efea/scripts/rust_semantic_supervisor_bootstrap.py) 与 [supervisor](https://github.com/zhiyuzhang001-a11y/codebase-atlas/blob/a296f46358173b8ce005ddfc2032c8132517efea/scripts/rust_semantic_supervisor_entry.py)。对应 SHA-256 分别为 `d490bf45e5ce6a68eb369e3849ca139f35b9121878e81fe6d28c78cf605ade13` 和 `dd8eaf38328a7476486ee9da6ef3605537c0722f56e98d6b86ce41a51e562e2e`。

## 原始证据归档与恢复边界

原始 STATE、日志、receipt、详细审查、源码资产与五个临时 probe 目录已保存为私有本机 `rust-evidence.tar.gz`，未上传 GitHub。归档为 463,093,683 字节，约 442 MiB；含 51,038 个普通文件、7,760 个目录，共 58,798 个成员，普通文件原始字节数 2,288,970,580。归档 SHA-256 为：

`81ae9cdd8ba42c79a54e5a7e13f1451fb3e23104e79c88ed562a3dd798d76b2b`

归档成员、逐文件 hash 与元数据在移动前后及删除原件前已完整验证；本次收尾又重算了归档整体 hash，一致。最初移动到垃圾桶的原实验目录后来经用户单独批准永久删除，不能再从垃圾桶恢复。恢复材料是现有归档及相邻的 `README.md`、`VERIFICATION.json`、`verify_archive.py`；本机忽略目录中的 `RUST_ARCHIVE_LOCATION.md` 记录准确存放位置，不提交机器绝对路径。

GitHub 保存的是脱敏复盘、计划、审查与源码，不包含完整私有原始证据，不能凭 GitHub 文件声称已经取得归档。完整复现需要持有该归档，先核验 hash，再在新建空的私有目录中检查，不覆盖现有项目或自动运行脚本。未来资产可用性与平台条件须重新核验，归档存在不保证当前实验可执行。

第二份异地或离线备份尚未在本次收尾中完成；单份本机归档仍有丢失风险。应保留该归档，不因文档上传而删除。上传整个归档、公开原始日志或复制到其他存储须先做隐私/来源审查并另行授权。

## 未来重新尝试的条件

只有出现实质性新的可执行入口或隔离架构证据，并取得新的用户授权，才考虑重新立项。新计划须解释如何闭合上述来源/加载/角色门，在新增大规模准备前先真实验证最小正常与死亡恢复路径，并保留原冻结语义真值和资源合同。政策或可信计算基变化须列明差异，不能默认沿用旧批准。

后续产品还需完整安全与准备资格、五平台 installed-wheel/fresh stdio、冻结语义、外仓及独立原始证据验收。新任务、临时连接、用户项目部署、公开启用、合并与 Release 均按各自授权处理。本次文档上传不启动这些动作，也不恢复 heartbeat。
