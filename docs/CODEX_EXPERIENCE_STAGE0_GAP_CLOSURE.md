> **2026-10-11 收尾归档：开发已由用户决定暂停，不再自动推进。** 下文为逐时点历史记录，旧授权、待执行动作及“当前”状态不再构成启动许可；最终结论以[收尾报告](CODEX_EXPERIENCE_CLOSEOUT.md)为准。本机绝对路径以变量代称保留结构，私有原始证据未上传。

# 阶段零剩余缺口与闭合依据

> 2026-10-11 权限更新：当前连续执行、阶段复核与会话调整以[总计划第四版第十二节](CODEX_EXPERIENCE_AND_WORKFLOW_MASTER_PLAN.md#十二-连续执行与独立阶段门2026-10-11)为准。下文保留历史授权及实证；新授权不改变失败、未运行或未通过的结果。

日期：2026-10-10。状态：设计已批准，实验已开始；G1 指引隔离失败，配对路线停止。最新实证见 [执行结果](CODEX_EXPERIENCE_STAGE0_EXECUTION_RESULT.md)。下文保留审批时的缺口设计，不代表尚未启动实验；未进入产品实现。

本轮不重复已完成的版本盘点、旧 M33–M37 引用链摘要核对或六类合同设计。仅补充旧报告列出的缺口，并形成 [具体实验执行卡](CODEX_EXPERIENCE_STAGE0_EXECUTION_CARD.md)。总计划第三版继续有效，工时上限已取消。

## 1. 当前出口逐项判定

准确基线与最小干预范围已有设计。产品开发建议源码 SHA 是 `6ce74df210bc4af4b1be705fb95000a46d9a3e8d`，但真实宿主校准先复用安装 0.26.1。两身份不混用：校准证明连接与记录可行，不证明该源码或未来候选已运行。实现工作区与构建 receipts 尚未产生。

六类合同已有设计，本轮不重写。执行卡仅校准“已知局部”与“未知位置/跨文件”两类，不代替零结果、歧义、partial、停用、busy、stale、变更后证据失效和恢复的全部回归。

真实宿主接入只有一次既有终端临时验证。当前可调用工具清单没有 Atlas MCP，因此没有新的 project_status/query。不能从安装或项目配置推导桌面自动加载；执行卡采用私有根和进程参数，不修复全局旧入口或 trust。

对照隔离：独立审查后新增创建会话及物化项目前的 G0 只读权限/采集预检；0700、cwd 和仅写入沙箱不能证明同 uid 的读取隔离。先填实既有 permission profile、准确启动参数、允许读写路径和原始事件来源；无法证明安全边界时阻断配对，不修改 profile/trust。进程 MCP 与技能覆盖、共同及候选 AGENTS 文本均已具体列明，实际加载与无路由泄漏仍由探针验证。

轨迹与评分：最小事件、版本/读取区间、无效计量分支、零基线及超时已具体化。官方事件流资料没有证明本机拥有完整计分时间戳。执行卡先探针，缺字段则不运行配对任务；本卡采用独立人工评分，原测试与修复后源码在隔离评分副本重放，不实现新脚本。正式评分器尚未实现、运行或独立核对，需另列执行范围。

历史排除：本轮补齐 M37 正式观察的来源及路径集合；M38、产品/父仓自用的仓库级保守排除范围也已明确。完整来源归一、身份别名去重和独立复核未完成，不称历史排除全部通过。

任务与独立评分：公开开发样例已在卡内给出完整源码、提示、判定及执行命令，不依赖新下载。正式 12 对独立任务仍未选择或解封；独立评分者的会话身份、访问控制和真值有效性核验仍需安排。执行卡公开评分不替代正式封存。

成本、平台与恢复：不设开发工时上限，按实测记录。校准暂限本机终端与 Python，不能覆盖桌面、TS/JS 或其他平台；恢复对象、配置冲突处理和共享进程保护已具体化。没有实际恢复结果或候选运行 receipts。

结论：本轮将关键缺口转成了可以审阅和执行的条件；阶段零仍未全部通过。

## 2. M37 正式观察的新增排除覆盖

旧 stage3 exclusion 清单在正式运行前封存，不能覆盖运行中新观察。此次直接读取 task/repository/seal 与实际 results，确认 16 个 observations；排除不能只沿用当时的“无历史重合”结论。

React：remote `https://github.com/facebook/react.git`，观察 SHA `065bc84eb50914fa1bc60983eac0b0b717186d74`。pytest：remote `https://github.com/pytest-dev/pytest.git`，观察 SHA `e984a9b94740697411f5a05fcdfc3299dd20f13a`。正式独立仓库选择应排除这两个整个仓库家族，包括 fork、改名或另一个 SHA，不能只避开同一句 query。

React 八个观察任务：root-work-scheduling、state-hook-queue、hook-effect-commit、controlled-input-sync、dom-property-application、flight-server-task-retry、flight-client-row-decoding、scheduler-task-loop，ID 均带 `react-` 前缀。

pytest 八个观察任务：fixture-resolution、marker-expression-parser、assertion-import-rewrite、output-capture-manager、root-config-discovery、python-test-generation、runtest-protocol、lastfailed-cache，ID 均带 `pytest-` 前缀。完整原始 query、真值与返回集合分别取 task 与 results 文件，不依靠本文简称恢复字符串。

实际结果共有 28 个不同返回路径：React 15 个、pytest 13 个。真值共 16 个不同路径；其中 React 的 Scheduler.js 未在对应仓库的全部返回集合中出现。因此真值与实际返回的并集是 29 个路径，不能把返回列表当作完整真值。pytest 的 expression.py 虽在全任务返回集合中出现，对应 marker 任务仍可能定位失败；全局集合覆盖不等于逐任务命中。失败任务和错误候选均是已观察材料。

React 的返回集合：

```text
fixtures/dom/src/components/fixtures/input-change-events/RadioGroupFixture.js
packages/react-client/src/ReactFlightClient.js
packages/react-devtools-facade/src/DevToolsFacadeProfilerTools.js
packages/react-dom-bindings/src/client/ReactDOMComponent.js
packages/react-dom-bindings/src/client/ReactDOMInput.js
packages/react-dom-bindings/src/events/plugins/ChangeEventPlugin.js
packages/react-reconciler/src/ReactFiberCommitEffects.js
packages/react-reconciler/src/ReactFiberConcurrentUpdates.js
packages/react-reconciler/src/ReactFiberHooks.js
packages/react-reconciler/src/ReactFiberReconciler.js
packages/react-reconciler/src/ReactFiberRootScheduler.js
packages/react-reconciler/src/ReactFiberWorkLoop.js
packages/react-server/src/ReactFizzServer.js
packages/react-server/src/ReactFlightServer.js
packages/scheduler/src/forks/SchedulerMock.js
```

pytest 的返回集合：

```text
scripts/update-plugin-list.py
src/_pytest/assertion/__init__.py
src/_pytest/assertion/rewrite.py
src/_pytest/cacheprovider.py
src/_pytest/capture.py
src/_pytest/config/findpaths.py
src/_pytest/fixtures.py
src/_pytest/hookspec.py
src/_pytest/mark/__init__.py
src/_pytest/mark/expression.py
src/_pytest/pytester.py
src/_pytest/python.py
src/_pytest/runner.py
```

这些是历史 results 的只读提取，不是当前仓库代码导航缓存，也不用于新的定位评分。源码位置摘要不足以证明完整语义排除；正式选择前仍需独立复核仓库身份、任务家族和来源覆盖。

## 3. M37 后续、M38 与自用排除

M37 Stage5F 真实宿主已观察父评估仓 `score_provider`，位置 `src/codebase_atlas_eval/scoring.py:36–86`；报告另述同目标 analyze_change。产品 Codex integration acceptance 还记录父仓 load_cases/score_provider、产品仓 main、PrimaryWorker.run、TS fixture 与生命周期负控制。父评估仓、产品仓及其 fixture 家族均不能充当新独立仓库。

M38 的 contract 和 Stage5 报告明确 PDF 项目身份；当时 Git 为 unborn、未跟踪源码。没有 HEAD 不是未观察，必须排除整个 PDF 项目及其别名。Stage0 summary 和原始 query JSONL 入口证明合成 Python fixture 也被观察。Stage1–5 的故障、双项目、长会话及性能 fixture 属于开发回归，不能改名当独立任务。

SELF findings 覆盖产品部署、共享 Provider、多项目、平台和 TypeScript fixture。排除整个产品仓及这些自用样例；对未记录 remote/SHA 的临时 fixture 采用保守类别排除，不猜其身份。恢复资料保留，不扫描或移动 Trash 归档来补一个非必要路径。M38 后续原始材料若只在旧归档中，应由评分者按既有报告显式入口只读核对；当前不把“报告提到”写成“所有原始数据已验算”。

这次终端对产品 analyze_change 的验证、两个拟开发样例及所有后续校准查询都应加入未来历史排除。对产品/父仓/PDF/React/pytest 的整个家族保守排除，可以避免当前已知身份下的漏项，但不能替代旧 M33–M36 来源包含的其他仓库和未解析观察。

正式选择前逐条记录：来源文件与 SHA256、观察类别、repository remote/name/SHA 或 unborn 状态、任务/query 摘要、真值与实际返回路径、来源 JSON pointer/行位置；无法归一的身份阻断相关候选选择。仓库别名去重与语义相似检查由独立评分者复核。本轮未生成新的机器 manifest，不重复旧链验算。

## 4. 本轮读取的关键来源摘要

以下为 2026-10-10 本地文件 SHA256，仅冻结本轮所见字节，不宣称它们都经过独立认证。路径以父评估仓为基准；product 文档带 `codebase-atlas/`。旧 seal 引用和报告叙述不能自动认证后来 results，后续评分须保持这个区别。

```text
cases/m37-stage3-acceptance.v1.json
aef691e48c02bb749d66dd6cb9ceea40cc83dd3db301b8ab3d7f5083bb1f3a66
cases/m37-stage3-repositories.v1.json
12c91a98d655ef82084aa8511529b8bc45ea54da5af7cb3278ce54ebed3563cd
cases/m37-stage3-seal.v1.json
f1e4622e085d72320ee82e166003ae02045307569da74fa16c362f7498972659
reports/M37_STAGE3_INDEPENDENT_ACCEPTANCE.md
56c63856af797267a5d2aa4fa19adba8d390fb3bac4b81fcebb9bb3ef4fc0db5
evidence/m37-stage3-independent-acceptance/results/independent-acceptance.json
e560cba9dcbfa93f6d9a7e31e0c10918a267639c039456afe736f312c1926048
evidence/m37-stage3-independent-acceptance/results/independent-acceptance.events.ndjson
9135521f764587b6522bc2722b5cd510d9e0c0d1551e0a6ae305f7783a5e7ff6
reports/M37_STAGE5F_FINAL_APPLICATION_ACCEPTANCE.md
ee1330ae52738a646c67633ca5a3a5548524888adfe3de6490604f209aee8953
cases/m38-in-process-refresh-contract.v1.json
03c82a823713005257d32250feb79cb4da10e0ab33a6e94497bc9c396e7e7952
.evaluation-data/m38-stage0/results/stage0-summary.json
8720a25f62244bcace52da206543d7fd7721994c56ed0ba4fc070dc87f8a25c9
reports/M38_STAGE0_BASELINE_AND_PROVIDER_ROUTE.md
95c8845b3a4a744858e8cc7b91edad705f8937eec633d50b96ebbbff97a8afc5
reports/M38_STAGE1_MANIFEST_AND_DIRTY_PLANNER.md
63f18fb27f645339d4f01bec1b522a99254e1063418919c928ecedff51b91fcb
reports/M38_STAGE2_IN_PROCESS_REFRESH.md
b95dfb397d51c5e4eaf16e9c4e21600df6dc150767b2e1f616114f76765eb25a
reports/M38_STAGE3_TRANSACTION_CONCURRENCY_AND_FAULTS.md
5c660dbe6e5f3b20331b82767b16d738b4732e2607910d41ebc5bd2cbda090e0
reports/M38_STAGE4_WATCHER_BATCHING.md
15f178c22c3ebd10d2af1d9d89e28b45e3aa97c2f6c9bbe99928afcbafce8593
reports/M38_STAGE5_PDF_LONG_TASK_ACCEPTANCE.md
2aecd1c05947898efc8db7716ffd6a4f91d28020f88f4a92bd105189f9f477db
reports/M38_STAGE6_UNIFIED_READINESS_DECISION.md
97e8e410f6c32d73b21d780f5233a74ee10f08a8748aebd94ee71ca651934856
codebase-atlas/docs/CODEX_INTEGRATION_ACCEPTANCE.md
5e8a0ada25af0e126d473522721ccedbc5f867b4537784a77ceed854ae728fef
codebase-atlas/docs/ATLAS_SELF_USE_FINDINGS.md
f96710750b06a3460408630504ac10f7ba4fa6acc9bfe4c9c726a4019145a6a7
```

## 5. 下一步的明确出口

用户审阅执行卡后，第一步是 G0 只读核实既有权限与采集路径；有具体安全启动方案后才物化项目并创建宿主探针。若预检或探针失败，交付准确失败字段、影响及安全替代设计；不先写产品包装层掩盖它。

探针成功后完成公开开发配对与恢复，同时由独立评分者完成历史来源归一、候选仓库冲突检查、正式评分访问安排。若没有评分者或可靠 trace，保留已证实的连接/行为结论，正式收益验收仍不成立。

只有阶段零全部出口实证或按明确范围完成后，才审阅并批准第一阶段的技能、规则、MCP instructions/description 改动。候选改变后重新记录身份并验证，不用本卡或旧 M37 成绩替代新候选验收。

## 2026-10-11 当前出口证据映射

以上各节为 2026-10-10 设计时点。当前准确源码基线已改为 stable 0.27.1 的 `dd6c00eec85f834ead52ed59d01ef3a2cefe666c`，tree `c213d99f8cd2c88c38befae8f380df3e71ff1655`，私有独立工作区已冻结；旧建议 SHA 不作为开发基线。真实公开校准仍使用独立的 0.26.1 安装及所录 Provider 身份，不代表 0.27.1 候选宿主行为已验证。最新状态与失败以[执行结果](CODEX_EXPERIENCE_STAGE0_EXECUTION_RESULT.md)顶部及逐时点记录为准。

六类场景的现有证据和后续责任如下；源码中存在测试不能写成测试已运行或真实宿主通过。

1. 已知局部：真实 DEV-A 候选直接读取 labels.py、未调用 MCP，修复与三项原测试通过，有限工作/采集复核通过。旧 DEV-A 控制组采集永久无效，不能以其产物重放通过填补有效配对。
2. 未知位置：真实 DEV-B 候选先 status/locate，读取 options/batch/request 与原测试后确认目标；候选位置只是导航，不是首次实现证据。原生 MCP 来源与版本关联经复核，修复及四项原测试通过。新候选第二阶段仍需新的宿主开发任务。
3. 已知符号跨文件：DEV-B 的 analyze_change 使用准确 choose_timeout 路径、compact 和有限 bounds，完整性与来源 fingerprint 保存；两入口源码确实读取，编辑后没有沿用图查询。stable 0.27.1 `tests/test_change_analysis.py:95` 的合同测试核对 definition 后优先测试证据，但本轮仅检查源码，不宣称执行通过。
4. 不相关/零结果/歧义/partial：A2/B2 的项目负控制提供真实有效零结果证据；0.27.1 `tests/test_change_analysis.py:103` 断言歧义停止、关系 not_run，`:119` 断言空而截断为 partial，`:126` 保留 subquery error。它们是已读源码合同证据；不相关候选与全部异常分支仍由第一阶段回归、第二阶段真实宿主开发验证覆盖，不能以合成采集器的错误用例代替产品行为。
5. 不可用/停用/busy/startup/错配：历史 method-not-found 与 stderr 门失败保留，当前根 stale 后直接源码回退。0.27.1 `tests/test_reloadable_mcp.py:210` 断言 repository_mismatch；`tests/test_refresh_coordinator.py:713` 有 owner 等待合同，`:835` 对 startup_timeout 有有限两次重试与 0.01 秒等待断言。未在校准模型中强制故障或改共享 Provider；第一阶段做隔离回归，第二阶段以预注册私有宿主场景验证安全降级。
6. 过期/刷新失败/旧代/长会话：0.27.1 `tests/test_refresh_coordinator.py:471`、`:483`、`:495`、`:507` 的已读合同保留旧代、拒绝变化快照、处理超时/取消；同文件 `:555`、`:868` 及 `tests/test_reloadable_mcp.py:126` 仅定位了待检测试入口，尚未把其正文或执行当证据。第一阶段核对并运行准确基线/候选回归；第二阶段新私有宿主验证长会话变更后证据失效。公开 DEV-B 校准不包含编辑后 refresh，不声称这一分支已实证。

阶段零出口要求六类的基线证据与责任明确；第一阶段出口要求场景回归全部通过，第二阶段出口要求真实宿主开发验证通过。这三种状态分别记录，不能因阶段零采用合同证据提前认证后两阶段。

公开校准进程均为新鲜 CLI 前端；共享 Provider 与预建项目索引已存在，不能称 Provider 冷启动或查询缓存全冷。事件主指标从可观察 UserMessage 完成起算，排除此前前端处理；前端 observer 耗时单列，不冒充端到端收益。900000ms 公开计量 cap 与 960 秒监督超时是当前公开校准合同，正式公平性/冷热/并发资源条件仍须独立冻结，不因短小样例耗时自动推定正式上限充分。

实际成本账本保留九探针、四工作与每次评分启动；首次评分在 thread 创建前失败，累计启动尝试与真实模型会话分开。原 A-control 用量和完整人工准备工时未知，不推算费用。已有进程临时覆盖未写全局配置；恢复核对保留外来配置时点冲突，不覆盖回滚用户配置。校准目前只认证所录 macOS CLI/Python 范围；Linux、Windows、桌面接入、TS/JS 候选尚未测，不计通过。

历史排除的有限保守安排保留永久身份组、已封存关联、未解名称范围及递归阻断；覆盖仍 partial。所有当前公开任务、源码基线导航和评分投影应增列永久开发观察，正式选题 clearance 仍为空。正式 12 配对任务及隐藏真值尚未选择、未打开；封存/权限/独立验证安排和完整评测实现合同需在阶段零复核，真实正式候选身份与平台验证在正式运行前另冻。完成一次公开评分不等于这些出口完成。


## 2026-10-11 阶段零整体出口待审

准确源码基线为稳定 v0.27.1，commit `dd6c00eec85f834ead52ed59d01ef3a2cefe666c`，私有 detached 工作区当前 clean；主 checkout HEAD 与用户修改保留。校准继续复用 v0.26.1 与既有版本化 Provider，不以旧校准验证未来候选。阶段零已物化真实工具隔离、有限公开工作、匿名采集/独立评分可行性、计量与固定正确性门、历史不确定候选阻断、成本/资源/平台/恢复设计。

正式评估采用私人 `20261011-formal-evaluation-arrangement-proposal-v4.json`（SHA `5abd26fd36fa690abc0afa1f1667ad2579fe610f385e126c5464ce031e534a41`）。原始匿名评分先全部封存，主 agent 保管预承诺映射但不评分或提前计算配对；独立封存审查通过后一次解盲，程序仅按映射重排原判断。28 个计量/封存/投影与 22 个采集模拟检查通过；两个 reviewer 有限认可设计，不代表阶段零整体通过。v1/v2/v3 及其必要修正保留。

实际公开校准包含四场工作、两次评分启动、一个真实独立评分；旧 A 控制永久无效，只存在一个有效 B 配对。有限可行性出口替代原卡两个完整有效配对，经独立 reviewer 明确认可；不用于收益统计。初始安装/Provider、共同背景与差异指引、真实能力清单及 OS 边界有对应 receipts；原 G1 与 resource-interface 失败完整保留。历史覆盖仍 partial，231 未决项和递归未知阻断不作清除；未来正式仓库必须独立排除产品/开发/审查家族及语义重叠，否则拒绝准入。

正式任务与真值尚未选择或打开，最终候选尚未实现。阶段零冻结契约和接口；阶段二/三必须实证验证候选 Python、TS/JS、失败与超时全链，阶段四再审核具体 task profiles、测试运行时、保护清单、原始判断封存与映射时序，缺项不得正式启动或接受。正式 12 配对一次运行、8 个预注册适合任务、20% 主指标改善、完成中位数不超过 1.10 倍、零 control-only 成功退步均保持不变。

本机实证范围为 macOS 27 arm64 上的新鲜 Codex CLI；其他 native hosts 和平台未验证，列为兼容回归待测。Python 运行时已核对；TS/JS 运行时与具体正式测试须在未来准入时准确冻结和预检。已有校准计量排除前端启动，不宣称端到端收益；Provider 与预建索引属于准备热状态，具体缓存未知，不宣称冷启动优势。

恢复只核对自有实验前端、进程覆盖、私有源码/指引和资产，未停止共享 Provider 或覆盖全局配置。当前精确记录的 12 个前端 PID 均不存在；7 份保护对象（包括原本不存在的 override）与 29 份匿名文件相对最后审阅快照保持不变。早期全局配置外来差异继续保留，不宣称全程未变。恢复快照见 `20261011-stage0-current-recovery-snapshot.json`。

阶段零整体出口需新的独立 agent 逐项审查；当前阶段一至四未开始，不能用以上有限设计 PASS 提前推进。
