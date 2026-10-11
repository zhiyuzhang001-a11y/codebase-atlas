> **2026-10-11 收尾归档：开发已由用户决定暂停，不再自动推进。** 下文为逐时点历史记录，旧授权、待执行动作及“当前”状态不再构成启动许可；最终结论以[收尾报告](CODEX_EXPERIENCE_CLOSEOUT.md)为准。本机绝对路径以变量代称保留结构，私有原始证据未上传。

# 阶段零执行结果与当前阻断

> 当前状态（2026-10-11）：阶段零设计、准确基线与有限宿主可行性整体出口独立复核通过；阶段一开发复核通过。阶段二未通过，本轮按冻结失败门结束：累计7/10次实际启动，4次历史计量无效、3次有效采集；C7的TS MCP资格及C1局部Python工作通过，C4任务和采集通过但没有实际Python MCP调用，所需资格门缺失。C2/C5/C6、阶段三至四均未开始。独立收尾报告通过不代表阶段二通过；总计划未完成，没有解盲、收益统计或正式验收。

> 2026-10-11 权限更新：当前连续执行、阶段复核与会话调整以[总计划第四版第十二节](CODEX_EXPERIENCE_AND_WORKFLOW_MASTER_PLAN.md#十二-连续执行与独立阶段门2026-10-11)为准。下文保留历史授权及实证；新授权不改变失败、未运行或未通过的结果。

以下至“2026-10-11 连续执行与新增证据”之前为 2026-10-10 历史时点记录，不作为当前阶段状态。原状态：执行卡已批准并开始执行；G1 指引隔离失败，按卡停止配对路线。长期目标阻塞审计满足连续三轮条件，目标未完成；无工时或 token 预算。未进入产品实现。

## 已完成

当前 pwd 与 Git root 均为 `${REPOSITORY_ROOT}`。复用稳定 0.26.1 安装与既有 Managed Provider，重新核对 Provider 二进制摘要与 manifest 一致；未更新安装。

创建四个相互独立的 unborn Git 合成根。每对初始源码字节相同；共同指引与候选追加文本分别冻结。冻结原测试保存在旁侧评分目录，工作会话不得读取。真实修复和评分重放尚未运行。

两个 Atlas 根分别审阅 onboard JSON 的精确 apply_argv：写入目标只有各自私有配置和索引，没有生成或替换 AGENTS、技能或 MCP 项目配置。分别 apply 后 `doctor=ready`，deep inspection 为 `healthy`、索引 `fresh`、数据库 stored_repository 精确匹配。shared-v1 在共享 Provider 数据目录下使用两个不同项目数据库，不声称数据库文件全部位于私人实验目录。CLI 准备结果不是真实宿主 MCP 验收。

针对四个根的 probe/work 进程权限共完成 24 项 OS 检查：自己的合成文件可读、旁侧不可读；probe 不可写；work 可写自己的 src，不能写 AGENTS 或读冻结测试。只对进程生效，没有写全局 profile 或 trust。

完成一个真实普通组探针：thread `01a12521-3e94-73b0-b78c-5c00cd605ce4`，Codex CLI `0.162.0-alpha.17.2`，模型 `gpt-6.1-sol`、推理 `medium`。实际 ALL_TOOLS 共 12 项，无 Atlas 工具、具体 MCP server、app connector 或 browser 工具；通用 MCP resource 函数与其他内置工具仍存在。进程 `features.plugins=false` 后本次 stderr 没有先前的插件 manifest warning；不以无 warning 证明全部插件边界。

## 实际失败与修正证据

G1 失败：原始 rollout 的 AGENTS 用户消息将全局 `~/.codex/AGENTS.md` 的 `Codebase Atlas deployment routing` 并入私有根指引。`--ignore-user-config` 不会屏蔽它；产品技能禁用也不能屏蔽全局 AGENTS。指引来源与普通组合同不符，不能将本次作为干净对照。

仅进程修正的只读诊断：本机 `codex debug prompt-input` 配置 `project_doc_max_bytes=0` 并以 `developer_instructions` 注入共同文本，退出 0，但渲染输入仍包含全局 Atlas routing。故这个参数组合没有解决本机问题。它只是提示构建诊断，未调用模型或 Atlas，不是替代 MCP 查询。

Git 原始命令退出 128：读取 `~/.gitconfig` 被拒绝，Xcode shim 缓存写入也被拒绝。范围内修正已通过 OS sandbox：只在进程设 `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`，使用已确认的 Xcode Git executable，`rev-parse --show-toplevel` 退出 0、返回私有根。未修改 Git 配置或放宽读取边界。尚未在新的真实 agent 会话验证修正，不能回填原命令为通过。

独立 reviewer `stage0_plan_review` 只读复核实际 receipts，确认必须停止配对；提示模型忽略全局指引不能充当隔离，Git 修正不能抵消指引失败。已有普通探针计入本卡三个探针上限，不能隐式增加重试会话。

## 事件、成本与恢复

原生 `event_msg.payload` 的 UserMessage completion 为 `1791624307163` ms；两行 `host_canary.py` 读取 completion 为 `1791624316506` ms。文件版本 SHA256、区间 `[1,2]`、工具输出及同 turn 关联已保存，相差 9343 ms，仅用于采集校准，不是效率收益。时间字段在 payload，不在 item；失败 Git 项及输出完整保留。没有复制 reasoning 或其他聊天。

真实 `turn.completed.usage`：input_tokens 30140、cached_input_tokens 13312、cache_write_input_tokens 0、output_tokens 611、reasoning_output_tokens 0。按宿主原字段记录，不把这些值推算成未提供的费用或全阶段用量。

本卡实际创建 1 个探针会话、0 个工作会话、0 个评分会话；此前单独批准的安全探针另计 1 个。准备、监督、诊断的命令与 observer timestamps 留存；没有完整的人工作时计量，不编造评分/恢复成本。真实 MCP 时间、partial/completeness 和四个修复正确性均为 `not_run`。

唯一启动的 Codex 前端退出 0，按精确 PID 核对已退出。没有启动本卡 Atlas MCP 前端，没有终止共享 Provider。私有配置和数据保留供复核，进程临时覆盖已随退出失效。全局配置、产品 MCP/Atlas 配置及父仓 STATUS/IMPLEMENTATION_PLAN 前后摘要相同；四个根的源码、测试和 AGENTS 摘要仍与冻结初始状态一致。

私人证据基目录：`${PRIVATE_EXPERIMENT_ROOT}`。主要 receipts：`evidence/preparation.json`、两个 onboard preview/apply 与 inspect-deep JSON、`sandbox-gates.json`、`DEV-A-control-probe.*`、`process-instruction-render-check.json`、`process-git-check.json`、`G3-event-mapping.json`、`execution-stop-summary.json`、`frontend-exit-check.json`。

## 下一步范围

执行卡第 3 节明确要求“源自系统的无法禁用路由使两组条件不成立时停止”；本次严格执行该门，不运行后续 G2 或配对，不增加会话额度，不改全局 AGENTS、不变更 CODEX_HOME、不恢复 Rust/heartbeat、不做产品提交发布。

剩余工作是只读确认宿主是否有受支持的进程级全局指引隔离机制，并修订启动方案与指引载体后再审阅。若没有机制，必须明确改用另一种宿主隔离方案或改写对照合同；不能悄悄豁免 G1、以共同全局 Atlas 指引冒充无 Atlas 路由的普通组，或把 CLI 准备结果当 MCP 成功。此前四个工作会话、独立评分、正式任务安排和阶段零全出口均保持未完成。

四个新合成项目、两个 canary、提示与所有本次观察永久加入历史排除，不能转成正式独立任务。既有历史链不重复验算；来源归一和正式评分访问控制仍需完成。

## 后续只读诊断（2026-10-10）

已读取本机 feature 列表及官方 project-instructions/config 字段资料，没有找到已证明可用的全局 AGENTS 禁用开关。安装 executable 的有界字符串检查出现 `user_instructions`，但它不是官方配置参考中列出的开关；只读提示渲染测试仍包含 global routing。`codex debug` 明确拒绝 `--strict-config`，因此非严格接受参数不能证明字段生效。新增 receipts 为 `strict-user-instructions-render-check.json`、`user-instructions-render-check.json`；没有创建模型会话，不试用该参数启动工作 agent。

本次新观察的历史排除已物化为私人 `evidence/historical-exclusion-addendum.json`：记录四个新根与此前安全 canary 的家族、unborn 身份、冻结摘要、实际观察类别和来源 receipts。它只增加这些合成家族，不是代码导航索引，不称旧 M33–M38 归一全部完成。

另已准备静态宿主渲染的精确只读诊断 argv：用本机 `codex sandbox` 拒绝 global AGENTS 文件读取，包住 `codex debug prompt-input`，不写文件、联网、运行模型或创建 thread。不修改全局文件，不改变 CODEX_HOME。方案见 `evidence/static-host-render-denial-proposal.json`，状态 proposed_not_executed，先作范围复核；即使诊断通过也不算 G1、不能扩大探针会话数或自动授权不同宿主隔离边界。

官方依据：[全局与项目指引发现](https://learn.chatgpt.com/docs/agent-configuration/agents-md#how-codex-discovers-guidance)、[项目指引参数](https://learn.chatgpt.com/docs/config-file/config-advanced#project-instructions-discovery)、[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。当前失败是实验条件未成立，不是 Atlas 产品修复或效率收益失败。

静态诊断范围复核后，实际执行使用已有 DEV-A-control-probe 权限，只有两个 global AGENTS 精确 deny 条目新增，没有采用 proposal 中较宽的 root read。所有写入与网络仍拒绝。结果退出 1、stdout 空：`Failed to read config file ${CODEX_HOME}/config.toml: Operation not permitted`，同时 PATH aliases 创建被拒绝。命令未到达提示渲染，故既不能证明可移除指引，也不能证明移除绝不可能。证据在 `evidence/static-host-render-denial-result.json`；没有用更宽的配置、凭据、缓存或日志读取权限重试，没有创建模型会话或 Atlas MCP。

独立 reviewer 区分了静态诊断与新实验宿主：收紧已有进程读取的非会话诊断可继续；真实 exec 的不同宿主隔离边界、额外探针及权限扩展不因此获准。当前已确认的下一步障碍是：已批准的 command profile 不足以运行宿主提示构建，而普通 unsandboxed 宿主仍注入全局 Atlas 指引。新启动方案必须先具体列明宿主自身所需读取与记录权限、AGENTS 拒绝边界、真实工具验证及剩余会话账本，不能偷偷拓宽后继续配对。

09:37 UTC 的后续完整性核对发现全局 `~/.codex/config.toml` 当前 SHA256 与准备时不同，mtime 为 09:32:04 UTC。模型/推理、sandbox/approval、features 与 MCP 名称这批已记录的非敏感字段未见变化；没有完整原字节快照，不能推断具体差异或责任来源。没有覆盖、恢复或将当前摘要重定义成原基线。`evidence/global-config-observation-conflict.json` 与 `post-static-diagnostic-integrity.json` 如实记录冲突。此前“摘要相同”只适用于前一轮恢复核对时点，当前不能宣称全局配置全程未变。产品两份配置、父仓保护文件与四个合成根仍与冻结摘要一致。

## 阻塞审计结果

09:38 UTC 重新核对：global AGENTS 文件及 Atlas routing 仍存在，原探针进程已终止，没有可等待的运行句柄。相同 G1 条件已跨原执行轮及两轮自动继续重复，目标未完成。

还有一项已授权的非会话诊断已执行：独立复核澄清，G0 本已允许读取现有运行配置；父进程静态诊断权限不等同模型 command profile。故 outer profile 只新增既有 `~/.codex/config.toml` exact read，继续拒绝 root、global AGENTS、其他新读路径、写入及网络。此命令仍退出 1、stdout 空，只有 `Operation not permitted`，未完成提示渲染，不能猜测哪个未报告依赖是原因；没有继续开放 auth/cache/log 或更多目录。此次配置前后摘要相同，不消除准备时基线的既有冲突。准确 argv 和过滤结果在 `evidence/static-host-config-read-result.json`，没有记录配置正文、凭据或完整提示。

完整要求审计见私人 `evidence/goal-blocked-audit.json`：G1 contradicted，真实 G2/四个工作会话/测试重放/独立评分 not_run，完整 G3 与历史归一/成本出口 incomplete；不能将已完成的准备或两行事件关联重新定义为整个目标成功。当前没有已证实、满足原启动与会话合同的接入修正。解除阻断需要单独审阅实验宿主启动/隔离方案和失败普通探针的会话账本；不得将静态诊断许可传播到新的真实宿主权限或隐式扩额。用户目标保留，未进入阶段一。

## 2026-10-11 连续执行与新增证据

第四版计划与 G1-v2 替代开发对照合同已分别独立复核。共同全局指引只含条件性部署规则；新比较明确是这个共同背景下的查询工具与候选项目指引组合效果，原 G1-v1 失败永久保留。正式验收两组定义仍须另行冻结，不回填旧结果。

有界静态宿主诊断未建立可用隔离：原生 sandbox 路线退出 1，精确目录枚举路线及三项设备读取修订均退出 -6、无输出。后者未到达提示渲染。宽泛 `.codex` 读取提案被 reviewer 拒绝且未执行；没有扩大认证、日志或历史会话读取。失败不证明具体原因或无法隔离。

稳定校准基线 0.26.1 两个私有项目重新检查，doctor 为 ready、deep inspection 为 healthy，身份和新鲜度分别核对。当前产品 0.27.1 与这个旧校准基线保持分开。恢复核对保留旧全局/产品配置的时点冲突，没有覆盖或重定义原始基线。

G1-v2 控制探针 thread `01a12a2e-2843-7b63-b97d-2ddeba652a52` 于 08:57:10–08:57:31 UTC 运行，退出 0、未超时。五条 stdout JSON 事件只有启动、两条 agent message 和完成，没有工具调用事件。模型报告 `image_gen__imagegen` 存在并按预设门停止；其“已报告全部描述”的说法没有事件支撑。故真实枚举及能力隔离为 **unverified**，不能把未调用视为隔离，也不能把进程退出 0 视为 G1 通过。Git、源码、媒体/patch 边界、MCP 和配对均未运行。

独立 reviewer `stage0_isolation_review` 核对 argv、提示摘要、thread、事件与七项保护摘要，确认停止正确、能力门未通过。本次前后保护摘要相同，两项合成写入不存在；这些时点检查不消除历史配置冲突。本卡累计探针 2、工作 0、评分 0，早期独立安全探针另计 1。候选 A/B 未运行；剩余额度不构成启动许可。真实 usage 为 input_tokens 30291、cached_input_tokens 23168、cache_write_input_tokens 0、output_tokens 330、reasoning_output_tokens 0，不推算未提供的费用。

静态 feature 检查发现本机稳定开关 `image_generation`、`view_image`；仅进程覆盖为 false 时列表确认为 false。这只证明参数解析与列表状态，不证明模型工具移除。已提交一个仅枚举元数据的新具体方案，要求首个实际 `functions.exec` 事件输出 ALL_TOOLS，禁止任何嵌套调用、文件或 MCP 操作；未获独立具体复核前不启动。

该纯枚举方案随后独立复核通过并实际运行，thread `01a12a32-5df8-7b31-9460-71868920f013`，17.316 秒内退出 0、无超时。四条 stdout 事件仍只有启动、agent final 与完成；没有 `functions.exec` 元数据记录。模型自报媒体工具 absent，不能独立证明真实工具移除，也不能据 stdout 缺事件推断后台从未调用。Reviewer 核对结果为 **inconclusive**，不重试同假设或启动 A/B。本卡累计探针 3、工作/评分 0，早期安全另计 1；总真实探针 4。七项保护摘要本次前后相同，历史冲突保留。Usage：input_tokens 27671、cached_input_tokens 12672、cache_write_input_tokens 0、output_tokens 101、reasoning_output_tokens 0。

独立批准的非会话事后诊断只读取上述精确自有合成 thread 的单一原生文件，在内存中过滤消息/上下文/推理；没有读取其他聊天或复制 raw rollout。原生 schema 为 `custom_tool_call`/`custom_tool_call_output`，不是仅 `function_call`。精确预注册纯枚举代码事件时间 09:01:53.984 UTC、同 call_id 输出 09:01:54.078 UTC；提取十项公开 name/description 元数据，确实没有 imagegen/view_image。Reviewer 核对脱敏 receipt 通过其有限声明，但未重新读 raw 字节；事后诊断不追认原 capture 门。更新的采集方案前瞻覆盖两种 schema、绑定 call_id，只保留白名单元数据及声明的合成操作。针对剩余 IO 边界的新控制探针另行具体复核通过，限一次、180 秒，候选/工作/评分仍不获此方案启动。

控制 IO 探针 thread `01a12a37-3886-7703-8d72-435667f3bf41` 已运行，27.593 秒内退出 0、无超时。十项实际工具无媒体工具；resources/templates 均为空。精确合成 Add File 的同 call_id 原生响应为只读 sandbox/approval settings 拒绝，目标事前事后均不存在；Git 返回精确自身根，sed 只读取 canary 两行、退出 0。纯枚举代码 SHA 与方案一致；四个嵌套调用的完整表达式和参数通过严格白名单，call_id 绑定完整。`stage0_isolation_review` 复核通过**有限控制操作门及声明的原生调用采集**。

该次 launcher 只保存 stderr 非空布尔，未保留可分类字节，故 stderr_capture=incomplete 永久单列；不猜警告、不宣称宿主全面健康，不为恢复 stderr 重跑实验。保护摘要本次前后相同。本卡累计探针 4，早期安全另计 1；候选、工作、评分仍未运行。原生 UserMessage/CommandExecution 时点已提取为纯元数据，但公共提示接收语义尚须确认，不计算主指标。新的 Atlas A/B 方案需另行复核，前瞻保留安全 stderr 分类并对未知分类停止后续项目。

随后 Atlas A 精确方案独立复核通过并运行，thread `01a12a3f-98f4-7452-8b2e-c07dab9e7e51`，23.638 秒内退出 0。原生工具清单为共同十项加精确 Atlas project_status/definition 两项，无媒体工具。但真实资源列表和模板列表均返回 `-32601 Method not found`；按当次预注册门停止，没有 patch、Git、项目状态或 definition 查询。安全过滤后的 stderr 为两项 runtime_failure，停止 B。独立 reviewer 确认停止正确，原资源门 failed/unverified_empty，退出 0 不抵消失败。

Atlas A 七项保护摘要本次前后相同，合成写入事前事后不存在。本卡累计探针 5、早期安全另计 1，B/工作/评分未运行。Usage：input_tokens 45796、cached_input_tokens 29056、cache_write_input_tokens 0、output_tokens 188、reasoning_output_tokens 0。安装源的有界只读检查显示 0.26.1 dispatcher 只声明 tools，非工具方法包括 resources/read 走 -32601；这个源码观察不是实际连接查询成功，也不把失败列表当空列表。下一步只审阅非会话 dispatch 能力证据及明确替代资源门，不重跑原假设或隐式豁免失败。

独立 reviewer 随后核对安装源摘要及构造/分派函数，批准并核对非会话诊断：无 service/Provider 的 `McpServer` 初始化仅声明 tools，三项资源方法（含合成 URI 的 read）均直接 -32601，安装源前后摘要不变，没有新会话或实际目标查询。显式替代合同因此将该精确安装的 generic resource 通路分类为不可用，而非空列表；其他 server、版本、摘要或能力变化立即使分类失效。原 A 资源门失败保留。修订的 A1/B1 方案独立复核后只启动 A1。

A1 thread `01a12a47-960b-79b2-9f09-26d18c082a57`，43.423 秒内退出 0。实际 project_status 精确匹配 DEV-A-atlas 根/config/project，fresh/ok、Provider 数据库 ready，自动更新和软件检查均 disabled。正控制 `atlas_host_probe_a` 返回一个属于本项目的 host_canary.py 1–2 行定义；负控制 `atlas_host_probe_b` 返回有效零节点，两次 query 均 completed、无截断/continuation/警告，不把 error 当空结果。Git/sed 成功，合成写入不存在。原生清单十二项（共同十项加两个指定 Atlas 工具）、纯枚举与 patch 拒绝均提取；Atlas 三次完整表达式、参数和 call_id 已白名单绑定。

`stage0_isolation_review` 独立核对并认可上述**有限实际目标查询结果**；但安全 stderr 分类记录 runtime_failure=1，按冻结规则 stop_next_project=true。没有保存原始行，不猜它来自哪次工具调用；模型所说“probe completed”不能抵消监督门。A1 不标 all-gates pass，B1、工作和评分仍未运行。七项保护摘要及安装 dispatch 摘要不变。本卡累计探针 6、早期安全另计 1；失败和新假设全部保留。计量 origin 语义仍未确认，正负查询证明不等于效率收益。

历史排除清单冻结 116 个来源、355 项仓库声明、145 个任务提示摘要、367 个显式引用。修正了目录误算缺失、派生 pointer、Unicode 行位置和五个生命周期假别名；仍缺三个 M38 JSON 引用，未发现已提供期望摘要的冲突。新增有限归一保留全部声明：6 个显式 GitHub remote 分组、28 个精确 lexical path 分组，273 项仅名称声明保持 unresolved，不按 basename 或 commit 合并。独立 reviewer `stage0_exclusion_review` 复核通过其有限 partial 声明；不代表完整家族/观察覆盖或正式选题可解封。真值/返回路径、递归历史观察、fixture/fork/上游和产品别名仍需闭合。

新增证据均在上述私人基目录的 `evidence/20261011-*`：`control-capability.events.jsonl`、`control-capability.launch-result.json`、`calibration-baseline-health.json`、`resume-integrity.json`、`historical-exclusion-inventory.json`、`historical-family-normalization.json`、`media-feature-static-check.json`。历史清单 SHA256 为 `03323ee1d83fcc927465679336209134dfd7e8ff1437060d95625ade05ac1b2d`，有限归一 SHA256 为 `34cbfbbf1e4b9063bb9f8ee375bfa995365b9bc3c839b281315ec344fe2b9f6f`。

九分支公共 synthetic 纸面计量合同也已修正并独立复核通过其有限声明：唯一原点是宿主接收提示的 native 时点；版本演进本身不判失效，缺必需映射才 invalid；同文件同版本闭区间重复阅读与冻结任务条件分开判断。它不是评分器、真实计量或完整阶段契约通过。独立评分安排、最终校准超时和正式资源公平性仍未完成。

## 2026-10-11 历史排除增量复核

独立 reviewer `stage0_exclusion_review` 核对五个明确封存 bundle 的输入哈希、registry key 与 JSON pointer，有限认可 66 行关联对应的 42 条唯一仓库声明；重复关联无 path/remote/commit 冲突。原 273 条仅名称声明中仍有 231 条未解，不作全局同名合并。28 个精确 checkout 根已核对，27 个当前 HEAD 与历史声明一致；产品 HEAD 不一致保留。当前 origin 不能证明历史 remote，两个未保存 origin 的占位值不算字节核验。

已登记来源和显式 JSON 引用共 139 个文件进一步提取 13,506 条词法路径、185 个提示摘要；源 SHA、pointer/value、继承 context 经独立复核。字段明确为 `lexical_role`，395/4,208/8,903 三类均保留：不是有效真值、认证实际返回数或完整观察计数，不能按角色删除排除路径。原证据及修正后的 v2 分别冻结。三个缺失 M38 文件、嵌入文本、递归来源与 family/fork 关系仍未闭合，正式排除门未通过。

新增私人证据：`20261011-historical-checkout-metadata.json`、`20261011-historical-sealed-repository-links.json`、`20261011-historical-observed-paths.json`、`20261011-historical-observed-paths-v2.json`、`20261011-historical-review-addendum.json`。没有新增真实会话，累计账本不变。

当前产品 project_status 返回精确产品身份，但 index stale/repository_changed 与 registration fingerprint mismatch；不接受其查询事实，源码基线核对使用直接 Git 读取。v0.27.1 源码基线修订与可观察 native 事件计量原点均已形成明确提案，尚待独立设计复核，不追认旧校准通过。

计量原点修订已获独立设计有限认可并写入总计划/执行卡 G3-v2；原点为可观察 native UserMessage 完成事件，而非声称实际接收时间。具体字段与前瞻校准仍需通过，不计算旧探针效率。一次匹配准确 CLI 二进制的拒绝模板检查未找到预期完整模板；没有增加 stderr 例外或猜测旧 A1 原因。只读/query 运行诊断已提交具体新假设方案，未启动。

v0.27.1 官方 Release 与远端 tag peeled commit 已核对为 `dd6c00eec85f834ead52ed59d01ef3a2cefe666c`，非 draft/prerelease；与原未接受 v0.27.0 提案差异限十个文件。独立 reviewer 已有限认可基线选择设计。当前 Rust 分支与新 stable 分叉，不继承当前 HEAD 增量。实际工作区身份尚未产生，故基线冻结尚未完成；校准安装仍为 0.26.1，其结果不证明新开发源码。[官方发布](https://github.com/zhiyuzhang001-a11y/codebase-atlas/releases/tag/v0.27.1)。

### 2026-10-11 源码基线冻结更新

旧 v0.27.0 建议只作为历史提案保留。经独立设计及官方发布身份复核，准确源码基线更新为 v0.27.1 `dd6c00eec85f834ead52ed59d01ef3a2cefe666c`，tree `c213d99f8cd2c88c38befae8f380df3e71ff1655`。已用禁用 hooks 的单命令 Git 原生 worktree add，在私人实验基目录下创建 `source-baseline` detached 工作区；核对精确 Git 根、HEAD、版本及初始 clean 状态并冻结 receipt。主 checkout HEAD 和用户修改保留，不继承当前 Rust 开发增量、不执行 setup、新 Rust 工作或移植。

v0.27.1 的查询新鲜度复验属于基线行为，不能算本轮干预收益。校准安装继续为 0.26.1，校准结果不验收新源码或未来候选。源码基线出口已物化；未做产品实现，阶段零整体仍未通过。冻结证据为私人 `20261011-source-baseline-freeze.json`。

A2 限定只读/query 探针 thread `01a12a5a-2c12-7771-8bcd-06c1cef1b944` 已运行，退出 0、无超时，所有 stderr 字节（含末尾残行）内存分类为空，无例外。十二项实际 metadata、六项完整白名单 request/output、精确公共输入 SHA 与原生提示/任务完成事件已采集。原生嵌套工具 item ID 与外层 custom call ID 不同、command 字段为精确 shell argv；初始解析缺口及结构关联修正独立留存，等待具体复核，未计算效率分数或启动 B2。累计本卡探针 7、早期安全另 1，总探针 8；工作/评分 0。旧 A1 不回填通过。

A2 有限读取/query/runtime/capture 门独立复核通过。G3-v2 的具体采集 schema 在私人 `20261011-native-event-metric-contract-v3.json` 澄清：outer request/output 绑定自身 call ID，nested item 绑定自身 item ID；同精确 thread/turn 内按完整 argv+cwd 或 server/tool/arguments 双向一对一关联，重复、缺项、多匹配或未知均不确认，不以时间猜测。起终点及阈值不变，旧失败与分数不回填。原已复核 B2 在此登记后可单次执行；完整计量及阶段零尚未通过。

B2 thread `01a12a60-2beb-7051-9049-46ee555f5468` 已独立复核通过有限读取/query/runtime/capture 门，41.676 秒退出 0、无超时、完整 stderr 为空。精确 DEV-B 身份 fresh/ready；b 命中自身 1–2 行，a 返回有效零节点，无截断或警告。六个完整 request/output、五个 native 操作及唯一输入原点/完成事件同 thread/turn、双向唯一关联，符合前瞻 v3 合同；不计算正式指标或判阶段通过。累计卡探针 8、安全另 1、总 9，工作/评分仍 0。

历史引用更正已独立复核：报告三处实际完整引用均为 `.jsonl`，原提取截为 `.json` 后误报缺失。三文件确实存在、摘要及 JSON 语法已核对，当前 367 条显式引用缺失为零；原清单和误报保留。此更正不证明完整 recursive coverage；本结果文档是活动快照，当前摘要不同于历史保存摘要，另记 drift，不声称全部引用当前哈希匹配。

### 2026-10-11 首场公开开发与监督修复

首场 DEV-A-control 工作 thread `01a12a6a-d9b8-78d1-a434-6417db0978df` 已实际执行，累计真实工作为 1，评分仍为 0。监督脚本错误引用没有 stderr 分类表的配置，保存前抛出 KeyError，丢失当时 stdout、stderr 与退出记录。原 capture 永久 invalid；其余三场工作已停止。没有重跑原任务、补判通过或隐式恢复额度。

独立限定的 metadata 恢复只查固定时间窗内一个候选首行，再以唯一精确 cwd/CLI/exec 身份及原生公共提示 SHA 验证自有会话。后续只过滤这一个自有 trace 的声明操作，确认 src/labels.py 修改与完成的 FileChange。测试声明返回退出 134，直接报告 Homebrew Python 动态库被 sandbox 拒绝，没有 native CommandExecution 测试完成项；不能推断它就是丢失的 launcher stderr。七项前置保护摘要也未保存，不能以恢复后的当前摘要补充为完整工作前后核对。

无需扩大权限的非模型检查发现，原沙箱已经允许 miniconda Python 3.13.5；精确路径与 SHA `51f439df30821a5b4b3ba153a1c6ae9291b106c7476be0e2fe6241cae525211c` 下公开三个测试重放通过。这仅证明当前修改的公开测试与运行时可行，不能追认原模型工作或效率计量通过。未来两臂需统一冻结解释器和完整命令。

监督器改为 spawn 前加载依赖和分类表，子进程结束后立即保存最小退出、身份与事件，再处理后置摘要和分类；任何采集异常保留失败并停止。模拟空 stderr、残行、敏感分类、分类异常、超时及后置摘要异常均已检查；模拟记录不计入真实会话。源码基线与公开校准均未变成正式评分，阶段零整体仍未通过。

保守排除安排 v2 经 `stage0_exclusion_review` 有限复核通过，SHA `1a6f5f7fdbeee32448ee8e9f1a104c3b1983ff955571a5cf7549a38e3547d912`。除 33 个未解名称范围，还永久承接六个 remote / 28 个 path groups、66 条封存关联（42 个唯一声明）；已绑定身份不会因解除 unresolved 而退出历史排除。231 条未解声明、递归缺口和 partial coverage 保留，候选 clearance 为空。不认证完整历史覆盖或正式选题。

新增私人证据包括 `first-work-supervisor-failure.json`、`own-work-metadata-recovery-result.json`、`own-work-prompt-identity-confirmation.json`、`own-work-declared-test-result.json`、`existing-profile-miniconda-test-check.json`、`supervisor-post-snapshot-unit-result.json` 与 `conservative-exclusion-arrangement-proposal-v2.json`，均带 `20261011-` 前缀。下一步仍须前瞻公开工作校准、版本/事件关联及独立评分安排复核；正式十二配对任务未选、未运行，阶段一至四未开始。

监督最终修复 SHA `78eb185b6934595693d2ced0dbfb120f0990d76213ffe9555566cfc5c242ef15` 随后经 isolation reviewer 有限复核通过，仅覆盖指出的分类及后置摘要异常路径；不代表所有磁盘/OS 故障覆盖，也不追认原工作。复核记录单独冻结。

### 2026-10-11 前瞻公开局部工作完成

独立 reviewer 具体审阅 v3 方案（SHA `5f7122c387f7087075165d47b313d9e98abf616ba7ece88bc8ad427bf78e1f4d`）后，只批准一场未执行过的 DEV-A-atlas；不重跑旧 control、不增加原四工作总额。余三初始任务在原 profile 下都能用 miniconda 运行公开测试，初始缺陷按预期返回 1，不把预期失败称为健康或修复通过。

DEV-A-atlas thread `01a12a7a-7713-7a23-993e-50ba37918754` 于 10:20:31–10:21:00 UTC 完成，退出 0、未超时、完整 stderr 空，七项保护前后摘要一致。真实工具清单为共同十项和已冻结四个 Atlas 方法，没有媒体能力。两次 sed 的实际完整输出分别对应初始函数 1–2 行与原测试 1–10 行；native FileChange 完成、精确 diff、成功 stdout 和重放后最终 SHA 一致。真实测试 CommandExecution 退出 0、三个公开测试通过。没有 MCP 查询，符合已知局部任务不增加查询的开发预期。

公共提示 SHA、同 thread/turn 的四项 native 操作、五组外层 request/output 与唯一完成事件均提取。采集 helper 只实现这一公开局部样例；其他格式及尚未实现的 rg/MCP 区域映射使采集 invalid，不能据此推断 B 的完整采集。receipt SHA `87ca1f607cb95a774363bbcf5b346f415fbe641e92d88ec25b451f7ec6f216ca` 尚待独立复核。实际工作累计 2、探针 9、评分 0；B 两场尚未启动。

前瞻补丁成功语义显式登记为 native FileChange 完成、精确自有路径/diff、成功 stdout/空 stderr 与版本重放/最终哈希共同成立。outer 输出 `{}` 不作为成功文本，缺项或未知格式停止后续。旧 control 的事后补丁版本 receipt 仅校准映射，不恢复其 measurement_invalid。

公开评分安排草案经独立设计审阅后修订 v2，明确正常重复读取有效、歧义事件关联才 invalid；修复后证据须确定性版本/行映射和真实完整读取，不以最终成功补认。匿名包 manifest、独立映射账本、四条准确重放命令及受测源码执行的 OS 边界验证仍为启动前门。没有启动评分者或打开正式真值；A 无有效配对，阶段零及正式收益仍未通过。

A-atlas 随后独立复核通过有限局部工作/采集门；同一时段三旁侧根与七保护摘要核对一致。评分安排 v2 也有限设计通过，实际评分启动门仍 pending。B 需要四 MCP 方法的身份/新鲜度/前编辑来源校验、rg 版本区域和连续 patch 映射完整采集器；已有非模型请求语法与 rg 行验证 13 项测试通过，但集成仍 incomplete，因此 B 尚未启动。

### 2026-10-11 公开跨文件工作及评分隔离准备

B 采集器的字面参数、四 MCP 方法、同身份/新鲜度/来源 fingerprint、rg 行版本和连续 patch 已集成。独立复核要求补数据库 ok、非空 fingerprint、单文件 rg 格式和未知 MCP 字段过滤；修正后的 13 项非模型用例通过并获有限复核。模拟材料独立标识，不计入真实会话或冒充 live 查询。真实工作仍使用原 v3 的准确 argv/profile/prompt，不扩大权限或追加样本。

DEV-B-atlas thread `01a12a85-e10c-7c61-8c00-b5923a718794` 已完成，10:32:59–10:34:02 UTC，62.823 秒、退出 0、无超时、完整 stderr 空、七项保护摘要前后一致。实际 status 精确自有且 fresh；locate_files 返回自身 options/batch 两个候选。四份源码/测试读取完整且 SHA 对应初始版本，确认 choose_timeout 后 analyze_change 使用准确路径与 compact；六项 completeness complete，来源 fingerprint 与本场 status 一致、无警告。之后局部 patch 与最终 SHA 一致，真实原测试四项通过，没有编辑后图查询。

初版 receipt（SHA `ecc308d7ab133632d0fafc8778191b1c5c0f1b7987c6ee9f8a21b33304b916f7`）有十组 request/output、九项 native 操作与唯一提示原点/完成，所有 IO 关联完整。唯一 unresolved 是 native `Reasoning` 类型标记，未读取或保存推理字段。已提出精确非 IO schema 过滤修订，原 receipt 不覆盖、改正版本另存；待独立复核，不把原 unresolved 默默抹去。Bcontrol 尚未启动；累计工作 3/4、探针 9、评分 0，未计算正式收益。

现有 A 两份产物已复制为两个随机匿名样本，复制源码/原测试摘要核对；原测试来自工作前 frozen 目录。标签到 root/session 的映射单独保管，评分副本移除绝对根和方案标签。旧 A-control 的采集 invalid 永久保留，复制后测试通过不恢复其模型计量。

评分进程的非模型 OS 前置检查已在准确只读匿名副本内运行两份重放测试。实际子进程拒绝读取工作源码、映射账本、产品源码及全局 AGENTS，拒绝改写评分源码和网络连接；源码/测试复制摘要保持不变。该 profile 只写自己的评分结果目录。仅两份样本准备完成，余 B 副本、完整 manifest、评分 prompt/工具语法与具体启动审查仍 pending，没有创建真实评分会话或打开正式真值。

新增证据为 `20261011-B-capture-integration-unit-result.json`、`20261011-B-atlas-collector-independent-review.json`、`20261011-atlas-WORK-DEV-B-atlas.launch-result.json`、`20261011-B-native-reasoning-filter-correction-proposal.json`、`20261011-public-anonymous-package-preparation.json`、`20261011-public-scorer-sandbox-preflight-partial.json` 及 `20261011-public-scorer-exact-argv-draft.json`。阶段零仍未整体通过，阶段一至四未开始。

B 的非 IO schema 澄清随后独立认可。唯一自有 thread 的非模型 v2 采集写入独立 receipt，SHA `831f06f0a6efb218ae9e62265f4b4513ca44cde012446936024535b0d45a82b1`；所有 request/output/native/clock/version/region/MCP 字段与初版逐项完全相同，unknown 为零。旧 SHA 和标记不覆盖，不追认旧计量分数。Reviewer 有限复核通过 B 工作/capture；本场实际未调用 definition，不声称四个工具均 live 通过。

Reviewer 又具体批准原 v3 的最后 DEV-B-control 工作；开始前仍由 launcher 核对冻结原文件。该会话已启动，未在当前记录中声称完成。它消耗原四工作最后一项，不增加、重跑或传播评分/正式集权限。B 候选匿名副本也已准备，评分包为 3/4；完整包与评分启动仍未通过。

CLI 官方 turn.completed 用量已另录：A-atlas input 102,382 / cached 83,584 / output 348 / reasoning output 0；B-atlas input 229,035 / cached 199,552 / output 783 / reasoning output 16，cache write 均 0。原 A-control stdout 丢失，该场用量未知，不以全目标 token 总数、源码字节或调用数代替。完整监督/准备/评分/恢复成本仍未闭合，不推算美元费用。

### 2026-10-11 公开四场工作结束，独立评分启动前门

DEV-B-control thread `01a12a8a-3aac-7473-b542-b443b17e9c03` 已完成，10:37:44–10:38:29 UTC，45.502 秒、退出 0、未超时、完整 stderr 空。工具清单仅共同十项，无 MCP；七项保护与三个旁侧根前后摘要一致。文件导航、四份初始源码/原测试读取、唯一 patch 和最终版本均准确关联，真实四项原测试通过。v2 receipt SHA `620ce81fc81b2d413eac5e1d191885fc3a19301d65ebd5f31f12ba392e659163` 经独立有限工作/采集复核通过；它不证明效率或阶段零整体通过。CLI 官方用量为 input 142,118 / cached 123,904 / output 677 / reasoning output 35，cache write 0。

原四工作预算已全部使用，探针仍为 9、实际评分为 0；旧 A-control 永久 measurement_invalid，无有效 A 配对。匿名四样本的修复产物重放均通过，不能借此恢复原失败模型计量。未追加或重跑工作，正式十二配对任务未选、未运行。

评分安排复核发现 v1 副本的 Bcontrol 状态显式暴露方案标签，已保留原副本，并建立只含 29 个声明文件的全新评分读根 `public-anonymous-v3`。状态投影改通用 capture，工具说明改摘要；原事件、时间、版本与源读取字段未改，工具名残余非盲性仍披露。非模型 v2 准备第一次扫描只命中工具说明中的普通 candidate 一词，失败副本另留在权限外；新完整扫描无显式方案标签。独立设计复核确认四样本源码、冻结测试与投影摘要对应，manifest SHA `74e102c66ea7b1f4a502507f515e779c4695836f3347b79ece31367474c435f4`。

新准确 scorer profile 的非模型检查完成四份原测试重放，拒绝八项边界访问（含旧副本与失败扫描副本），29 个评分文件摘要不变。补齐的评分采集器只接受共同工具清单、声明 sed/测试及准确 cwd，核对串行请求/原生操作/输出、自有 thread/turn、唯一提示与最终 JSON；未知输入 invalid，Reasoning 字段不保存。18 个模拟用例通过，模拟不计入真实模型会话。严格最终四样本身份/测试退出绑定与旧 invalid 的 null 时间均受检查；独立判断原字节在解盲前冻结，主 agent 不改评分。准确 v2 启动方案 SHA `9990c168962fb0117c7297f8d423ee69df92dbe090acff2cf56587fbcede1e82` 正等待独立权限/采集复核，尚未启动评分。阶段零仍未整体通过，阶段一至四未开始。

评分首次启动于 10:58:15.993–10:58:16.036 UTC 在 CLI Git 目录检查阶段退出 1；thread=null、events=0、完整 stderr 分类 unknown 一行。固定非敏感错误 `Not inside a trusted directory and --skip-git-repo-check was not specified.` 的 75 字节 SHA 与保存值精确匹配。七保护、29副本、映射摘要不变，不运行 native 投影、不恢复为评分通过。账本保留首次启动尝试；真实评分模型会话仍为 0。前瞻第二且最后尝试仅新增该 skip 参数，累计最多两次启动、最多一场真实评分；准确方案另经独立审查后才可执行，不修改 trust 或权限。


## 2026-10-11 真实匿名评分完成与契约修正

第二次且最后一次评分启动使用单独审阅的 `--skip-git-repo-check`，没有初始化 Git 或修改全局 trust。真实评分 thread 为 `01a12a9f-eceb-7441-ad46-24d3c70776fc`，监督耗时 272.202822 秒，退出 0、stderr 空。原生采集核验 52 次有限读取与 4 次冻结原测试，测试全部退出 0；29 个匿名包文件、保护文件和映射摘要前后相同。两个独立 reviewer 通过有限公开采集和评分可行性门。第一次创建 thread 前的失败仍保留，不计为真实评分会话，也不回填评分。

四份原始匿名判定 JSON 字节已在解盲前冻结，SHA 为 `42f75a5767866058a50d60c66577a850e60d8c78c204cd678d8a2f064a1ce459`。四个修复产物均正确，但旧 A 控制组计量永久无效，原始用量未知；其安全判定 invalid 表示安全证据不可验证，并非观察到越权行为。其余三场计量有效，有限公开包尚不足以验证全部安全门，因此安全字段为 unverified。没有解盲、计算配对效率统计或声称正式通过。

公开校准采用有限可行性出口：一个有效 B 配对、一个有效 A 候选以及真实普通组探针；原卡的两个完整有效配对没有达成。这一替代出口经独立 reviewer 明确认可，只验证采集和评分能工作，不修复旧 A 样本、不增加工作会话、不改变正式 12 配对或收益门。

正式评估设计修正版绑定预注册 12 个任务、其中 8 个适合任务、原始独立判断字节及其封存登记摘要。及时证据以指标期限为界；迟到完成、监督超时、任务失败和测量无效分别记录。失败任务的完成指标按期限计入，不能仅保留成功任务；只读测试和可写源码范围分开冻结。21 个计量/封存模拟检查与 22 个采集模拟检查通过，设计独立复核仍在进行。实际正式任务、隐藏真值和最终候选均未选择或打开；阶段二/三必须完成新的 Python 与 TS/JS 候选全链路验证后，才能冻结正式运行配置。

私人证据：`20261011-public-scorer-live-independent-reviews.json`、`20261011-public-development-and-scoring-cost-snapshot.json`、`20261011-task-neutral-evaluation-contract-unit-result-v2.json`、`20261011-formal-evaluation-arrangement-proposal-v2.json`。原始失败、v1 设计和样本全部保留。


阶段零最终整体出口复核：`stage0_final_exit_audit` PASS；明确仅允许进入阶段一。原卡两完整有效配对没有达成，有限采集/评分可行性替代出口接受；不需要新增会话修复旧 A 样本。后续真实候选资格、正式封存时序和准确权限仍待后续阶段实证，不能自动启动正式集。

### 2026-10-11 阶段二执行前协议准备

六个公开开发 fixture（Python、TypeScript 各三个）与全部变体已永久排除正式独立集。候选代码未变；前采集协议修订保留旧文件，当前采集器与监督器 35+13 项模拟检查通过。真实阶段二额度为两次只读宿主探针、六次普通开发会话，共八次启动尝试，无重置或隐含补跑。查询次数等上限由事后有效性检查判定；权限边界由 OS profile 执行，单次监督上限960秒。具体 G0/独立项目 setup/ready 冻結脚本待独立复核，尚未 apply、索引或启动阶段二模型。正式数据、真值、计分和效率收益均未运行。

### 2026-10-11 阶段二实际 setup 与 G0 加载缺口

六个私有项目 onboard/doctor/freshness 完成，唯一C5源码注入按计划触发stale；524项immutable当前匹配，71项明确PermissionError拒绝与canary清理经独立复核成立。104原操作保留。追加实际测试计数检查发现 TS 的旧rc1来自Node OpenSSL配置读取拒绝，未执行测试，因此整体G0尚未通过，阶段二模型启动仍为0。候选产品身份未变；只准备v3进程参数/环境修订与同权限追加加载检查，不重建索引、不回填旧G0、不重置会话额度。

### 2026-10-11 阶段二追加加载通过与首次真实探针失败

v3 仅追加 Node 进程参数 `--openssl-config=/dev/null --test-reporter=tap` 与 MCP 子进程环境 `OPENSSL_CONF=/dev/null`，OS 权限未扩大。八次只读测试实际加载数量和预期退出码一致，527 项 immutable 与各 fixture 字节匹配，索引仍为五 fresh、一按计划 stale；独立 reviewer 允许先运行一次 Python 探针，再审查其证据。

首个 Python 探针线程 `01a12b16-fd26-7f23-b53f-76c6770fcb39` 已运行，CLI rc0、无 stderr，但原生采集有七个 unresolved，结果为 `measurement_invalid`。采集器漏识别候选已有 coordination/schema_version 字段，并拒绝合法省略 depth（服务端默认2，输入 schema 本身无 default）。原 wrapper 未记录最终退出回执；随后只读精确 PID 检查确认 wrapper 和 MCP 子进程已不存在，退出码未知。源码、其他 fixture 和保护文件保持一致。该失败永久保留，不重采、不补评分；原卡使用 1/8 次启动，TS 与六次 work 未启动。

前瞻私有采集修复保留旧版本，39 项 schema/采集检查通过；10 项收尾检查覆盖首次回执失败、忽略 TERM、最终回执与双 PID 消失；17 项控制器检查覆盖启动/管道异常、finally 清理和有界超时。独立修复复核进行中，新有限卡尚未冻结或获准。上述均非产品收益或阶段二出口通过，正式任务和真值未打开。

### 2026-10-11 阶段二修复复核及有限 v4 卡

独立 NONMODEL 修复复核通过。v4 卡新增两个不同的公开只读样例（retention 与 tag normalization），原 TS 探针废止未启动；六个既定 work 仍各限一次。旧无效会话1次继续计入，新卡最多8次，阶段二累计最多9次，净增额度1已明确审查；任一新失败停止整卡，没有隐含补跑或无限扩额。八个公开 fixture 家族及全部变体、辅助程序和诊断均永久排除正式集，正式门槛未改。

具体卡 SHA `f2d6fb86d42c9d528f43df7d0646bb194a4aa57d8312904c4a4f2c0c4625cf3f` 已获得 setup-only 独立 PASS；只允许两个新私有项目一次 setup，六个旧项目保持原配置、索引和唯一 stale 注入。真实模型启动还必须取得绑定当前 ready SHA 的 actualready 独审许可。此时没有新 v4 模型启动或阶段二出口通过。

2026-10-11 v4 实际 setup 追加：27项操作完成，19项精确权限拒绝、两份实际2测试/rc0、685项当前immutable与旧527项冻结一致；8项目身份独立，7 fresh＋原C5 stale。actualready 独审给予有限 PASS，仅允许新的 QUAL-PY 一场后再次独审，尚未授权 TS/work 或正式评测。原失败仍计入阶段二累计账本。

2026-10-11 v4 首场 QUAL-PY 实际结果：线程 `01a12b38-3956-7a01-9e03-28f62aa84eea`，CLI rc0、无 stderr，冻结公共测试真实2 tests/rc0、各源码及保护摘要未变，但完整计量仍为 measurement_invalid（4处 unresolved）。独立复核确认 rg 无匹配有完整 native failed/rc1/空输出，旧采集将其当未完成；wrapper 收到SIGTERM、子进程−15发生于父收尾前，父未发信号，发送方未知，当前冻结合同分类 abnormal 成立。整个v4卡已停止，QUAL-TS与6 work未运行。阶段二累计2次实际启动，旧两次失败均不改判、不重采或补评分。新的非模型检查只验证未来严格无匹配业务分类与显式未知来源的完成后收尾分类，不能抵消旧失败。

2026-10-11 v5 剩余有限卡：42项严格采集及15项收尾纯检查通过独立 NONMODEL 复核；22项控制器检查通过。新卡只使用累计9次上限内剩余7次：QTS→独审→C1 Python读取/patch/test→独审→C4实际Python精确关系MCP→独审→其余4 work，原六普通任务及提示未改。旧两次失败原判保留，新卡任一失败停止，无自动重试或扩额。具体方案与只读 ready 脚本独审PASS后，只读核验694项immutable（旧685项均一致）、8项目身份、7 fresh＋原C5 stale及八根原文件集合/内容；未apply、刷新索引或重跑测试。实际ready独审待完成，没有新v5模型启动。

2026-10-11 v5 QTS 实际失败与 v6 前瞻准备：线程 `01a12b47-9e6e-72d1-8564-fe0e60cbc7d8` 的 CLI rc0、stderr0、公共 TS 测试真实2项通过，来源与保护文件一致；冻结计量仍 invalid，整卡停止。原生日志保存9个唯一 item_completed，没有 item_started；外层请求和完成结果完整映射，旧控制器的 start/end 集合相等条件使闭环证明失败。仅保存元数据诊断，不重采或重算该样本。累计3次启动、剩余6次，六个普通 work 尚未运行。

v6 新版本采集器按完整核心映射、唯一结束事件、同一线程/turn、未解决项为零验证闭环，仍拒绝实际未完成、重复或外来事件。另用候选真实 `_locate_payload` 和工具 schema 的纯源码调用核对定位输出，严格识别启发式标记、非穷尽标记、服务端预算及先读源码提示，保持定位结果不能充当源码证据。24项控制器、42项采集、7项真实来源契约检查通过；新合成 fixture 初次8项格式失败保留，随后只修正新 fixture 字段，历史轨迹不重评。产品四文件候选保持不变。

v6 方案 SHA `be4c658d08ffe3742edb65f08abb3a77b9a1e29c4090ee46e720ded724d17275` 非模型复核 PASS，仅允许只读 ready 一次。该核验实际 rc0，8身份/7 fresh＋原C5 stale/冻结文件一致；ready SHA `bcd3545effcbc122a4b908dcc4d6eb937d97213f0769c5484c67b8d12a360215`。实际模型许可待独审，顺序 C3→TS审计→C1→Python采集审计→C4→Python MCP审计→C2/C5/C6，各一次；任一失败停止，累计上限9不变。阶段二未通过，正式任务与真值未打开。

2026-10-11 v6 actualready 外部配置漂移：独立检查发现用户级 `~/.codex/config.toml` 与旧冻结摘要不同，其余702项一致；用户答复没有主动修改，来源未知。旧 ready 保留阻断，C3未启动，不恢复或覆盖用户配置。CLI本机帮助明确 `--ignore-user-config` 不加载该文件，既定实验准确argv已有该flag且固定模型/工具/权限/MCP/指引。仅构建新未来保护边界：新 ready绑定旧ready与漂移回执，唯一路径采用未来预期摘要，其他702旧项不变并追加3个新私有程序保护；每场前后精确一致，再漂移即停。8项纯边界测试通过，新入口相对冻结控制器仅新增明确绑定检查/使用新ready路径；旧失败不改判、累计3/9及6未运行任务不变。具体新边界独审待完成，不能声称阶段二通过。

2026-10-11 新未来配置保护边界独审有限 PASS，回执 SHA `33d1163afcff9cbe54cc3a7c1dbf8692188cf19a22dba3e8a5ac57b4762f01f2`。Reviewer重跑8项边界测试、确认706摘要当前一致和CLI忽略用户配置语义，只允许C3一次后实证审计；旧actualready漂移失败与未知来源继续保留。C3真实会话已启动，结果尚未完成；历史3次无效仍计入，上限9不变。

2026-10-11 C3单次真实工作结果：线程 `01a12b5e-a71c-72c1-ad96-76771db8e1d2`，CLI0、stderr0、无超时；修改2份允许源码，冻结公共测试真实3项/rc0，测试与706保护摘要及其他fixture一致，自有wrapper/child已消失。真实Atlas状态确认本项目fresh，精确路径compact分析保留partial/references缺口、not_run与exact边解析。完整计量仍invalid8，整卡立即停止，后续0。

具体缺口不同于前三次：实际 `rg -n 'chunkLimit|uploadChunks|chunks' src tests` 使用引号内pipe，公开capture约束明确禁止，属于真实输入约束违规；另一未识别事件为精确自身根下两个src绝对路径的patch，实际范围合法但原采集仅接受相对路径。不能以后一采集缺口掩盖前一违规或重新算valid。阶段二累计4次实际启动/上限9，剩余5个既定普通任务均未启动。原C3源码及失败轨迹保留，不回滚重跑或补评分；独立原始失败审计待完成，阶段二仍未通过。

2026-10-11 C3失败独审已确认：真实输入约束违规、绝对路径采集缺口、3实际测试通过、partial结果缺口披露及自有进程退出证据分别成立；不得算TS资格或有效计量。当前卡STOP，累计4/9，剩余5。只针对未来语法准备新私有helper和capture v8：单引号rg模式内literal `|`可识别，实际shell运算/扩展/重定向仍禁止；patch允许精确自身白名单src的canonical绝对或相对路径，拒绝路径逃逸和别名重复。7项语法拒绝边界检查＋44项完整合成采集检查通过，未重评任何真实旧轨迹。修复和是否有可接受的新有限实证方案仍待独审；没有新增模型或扩大已执行额度。

2026-10-11 最后一次 v7 具体方案待独审：累计上限提议由9明确增至10（净增1），仅5个未启动既定任务加1个新MIME大小写/空格/参数TS跨模块开发任务，没有旧样例重试；任一失败永久结束本轮阶段二，不再修订额度或追加任务。新样例4个冻结公共测试，永久排除正式集；源码及提示先冻结，私人root尚未创建。方案SHA `871d315cecd053f01f67d1fad5c88e5071d91b3d266c0c7403a93f0aae37063c`，setup仅新C7一处，旧8配置/index不变，C3授权修复已导致stale与C5历史stale均保留。24项控制器、44项采集与7项语法纯检查通过。当前未批准新增预算、setup或模型，所有4旧失败原判保留；阶段二未通过。

2026-10-11 最后卡启动前独审修正：v7未执行，review发现首个fixture写入前缺永久登记/外层失败边界，以及setup timeout在实际回执前抛错；v7b补独占marker与有界自有子进程尝试账本，4项账本检查通过，但review发现公共测试retain分支引用已移走的child变量，实际会NameError。两版均保留，未实际setup或消耗模型额度。新v7c改用实际rc，3项测试执行真实setup run闭包（假自有子进程）覆盖公共测试摘要、doctor结果和stderr拒绝；连同24项控制器、4项账本共31项通过。方案SHA `6340f9c0488b8f77d3d6e87d4f9ca951cd78c6b0f36d30340360c7be1a0b1785` 待setup-only复核；四旧无效与实际4/9账本仍不变，累计10仍为提案，未批准或执行。

2026-10-11 v7c setup-only独立PASS，回执SHA `f06095774c52370422ca1c774ad312684c67f45b748752f8178d8641a1636910`；明确批准最后有限开发卡累计10（历史4＋5未启动＋1新C7），任何新实际失败永久结束本轮。实际单次setup完成15操作，各有预登记和最终回执，无超时/stderr0；C7初始公共测试真实4项/预期rc1，9项精确权限拒绝、ownwrite canary已清除、onboard/doctor ready。只新C7配置/索引写入与一份已验证静态TS代码复用，旧八配置/index不变。865项immutable、9身份，7 fresh＋C5原stale＋C3已授权修复后的stale；后两者未刷新。实际ready SHA `21c6ad83146bed94d3bde73d5d3d3dbf285df956af40636e1b005370ed7902e6`。首场C7模型许可待绑定实际证据的独审，没有新v7c模型启动，阶段二未通过。

2026-10-11 v7c实际ready独审PASS，回执SHA `f8d49ac1e58e311c6691bf15783d457699beb0200c44be7b70f70defbfa0942d`，只允许C7一次后审计。C7线程 `01a12b78-7637-7fa3-821f-318129fa9b64` 实际结束，CLI0/stderr0/无超时；完整计量valid、unresolved0，native SHA `90097baa6ba10996670506784a27b072c300f34731ffc5977cc9b2fb80bb1a32`。真实顺序为源码读取→本项目fresh状态→精确路径compact关系查询→仅mime.ts修复→公共测试。865保护和其他root一致，自有进程退出；完成后SIGTERM发送方未知，按前瞻冻结规则记录，不推断sender或Provider健康。实际时长66.699秒仅作成本记录，不作为效率收益。正确性/TS资格独审待完成，C1及后四场未启动，累计5/10（旧4invalid原判保留）。阶段二尚未通过。

2026-10-11 C7实际TS资格独审PASS，回执SHA `748d1c38ed1b572188c3b4bb51284f95aee50a8e76b91d71699ad48fecf5ab26`：4冻结公共测试真实通过、精确compact六项完整性complete/无警告、9outer/8native映射和保护一致；仅许可C1一次后审计。C1线程 `01a12b7b-f58f-79e2-8b21-1685054c4780` CLI0/stderr0/无超时，计量valid/unresolved0，native SHA `6a3cc4b1a1e001913b2a8e68573826895172f0b387b6298ac8f1be6d8cd550ac`。6outer（inventory/navigation/2sed/patch/test）、0 Atlas MCP，final源版本匹配、865保护及其他根一致；正确性与实际Python测试独审待完成，C4/C2/C5/C6未启动，累计6/10且旧四invalid保留。

2026-10-11 C1有限Python工作与采集独审PASS，回执SHA `c72c4affec1912863cd15b74a48f316bbdc1352468832582312490aad36313ac`，实际2冻结公共tests通过且保留内部空白，0MCP符合已知局部任务。仅允许C4一次。C4线程 `01a12b7e-936d-7521-911d-a6eff993610c` 实际CLI0/stderr0/无超时、完整计量valid/unresolved0，native SHA `ae18cac7ac4f6be1565c03c2975a1ddff7b4a5ed5b773bbc871a86a804591e0c`；7outer inventory/navigation/rg/3sed/test，source未改、865保护及其他根一致，但没有MCP查询，不能算真实Python关系MCP资格。任务正确性及预置资格缺口独审进行中；C2/C5/C6均未启动，累计7/10、旧四invalid不重评。阶段二未通过，后续门保持关闭。

### 2026-10-11 阶段二本轮终止与独立收尾

C4独立复核确认任务正确性、安全与采集通过，但实际Python MCP资格缺失；按最后v7c冻结失败门永久结束本轮，不将C2事后替换为资格任务、不追加会话。累计7/10次实际启动：4次历史无效、3次有效采集；未启动C2/C5/C6。候选产品自阶段一以来修订次数为0，阶段三、四未开始，正式独立集与真值未打开。有限工程验证不能作为效率收益证明。

收尾结果 `20261011-stage2-round-terminal-result.json` SHA `2285b6349d4b5aa83b7c619de5892a5df4f7395d1c3bb1f2363f9484b79fbcbf`；新独立审计 `20261011-stage2-round-exit-independent-review.json` SHA `0ddb605708a9495f66f694044de7de6f3b7c625b3a3a4041f06bb2488092d525`，结论 `terminal_reporting_PASS_but_phase2_incomplete`。865保护项及候选四文件摘要匹配，基线干净、主仓库源码未改；21个精确模型相关PID当前不存在，不推断历史退出码或信号来源。用户答复“没有主动修改”已记录，外部配置漂移来源仍未知，未覆盖或恢复配置。

### 2026-10-11 完整阶段二缺口与继续边界

新的只读独立审查确认：阶段二未完成的不止Python MCP资格。C1局部任务有限通过，C7的TS精确关系任务有限通过；C2未知位置定位未运行，C4只证明源码owner消歧，不覆盖定位无匹配、不相关候选及partial处理；不可用/停用/busy/启动超时/身份错配缺少完整当前候选宿主链；C5/C6未运行，过期/刷新失败/旧代恢复/源码变化分支仍未闭合。旧C3无效不能补为这些分支证据。基础设施资格与普通问题下的按需使用应分别验收，不强制无必要查询。

准确冻结终止条款为v7c的resource_budget.failure_policy：“Any new failure permanently ends this stage2 round. No new cards, product revision, model, task or quota additions after failure.” 当前仅可盘点证据和维护报告；没有启动新实验或编写新执行卡。继续实证需要用户明确授权开启新的阶段二验证轮并覆盖此终止后的实验禁止边界，随后才准备完整预注册方案、有限累计额度并独立预审。旧7次启动/4次无效保留、六类全部要求及正式门槛不降低、阶段一修订上限不重置、全局配置与隐藏真值保护不变。具体决策边界保存在私人20261011-stage2-full-scope-gap-and-reopening-decision.json；此记录本身不是执行批准。
