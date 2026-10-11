> **2026-10-11 收尾归档：开发已由用户决定暂停，不再自动推进。** 下文为逐时点历史记录，旧授权、待执行动作及“当前”状态不再构成启动许可；最终结论以[收尾报告](CODEX_EXPERIENCE_CLOSEOUT.md)为准。本机绝对路径以变量代称保留结构，私有原始证据未上传。

# 阶段零临时权限与真实宿主安全探针

日期：2026-10-10。状态：一次普通组真实终端安全探针完成；未运行配对开发任务，阶段零未全部通过。

用户批准在本次实验进程中设置临时读取权限规则、先做一个安全探针。执行继承原卡边界，不修改全局/项目配置或 trust，不进行 Atlas 索引、查询替代、更新安装、Rust 或 heartbeat 操作。

## 已完成的实证

1. 单独 OS sandbox 检查：允许根内合成文件读取退出 0；旁侧合成文件读取退出 1（Operation not permitted）；允许根内写入也退出 1，没有生成写入目标。
2. 一次真实 Codex terminal exec：正常完成、进程退出 0。agent 读取允许的 canary 成功，对旁侧读取和写入各尝试一次，均被 Seatbelt 拒绝，没有重试或提权。
3. 实际工具发现：agent 通过真实 ALL_TOOLS 按 Atlas 名称/描述筛选，输出为空；不是从配置文件推定无工具。未调用 Atlas。
4. 实际加载指引：本次原始 rollout 中有私有 allowed 根的 AGENTS instructions 标题，与本卡探针指引路径一致。没有把产品 AGENTS/交接聊天复制给工作会话。
5. 原始事件来源：本次宿主 rollout 有 UserMessage 和 CommandExecution 的 started_at_ms/completed_at_ms，以及 custom_tool_call/output 的 call_id 和真实输出。终端 stdout JSONL 本身没有这些时间戳，不能只保存 stdout 计分。
6. 完整性：全局 Codex 配置、产品 MCP/Atlas 配置、父仓 STATUS.md 与 docs/IMPLEMENTATION_PLAN.md 的前后 SHA256 相同。私有前端已退出；未停止共享 Provider。证据和私有根均保留，未清理历史资料。

## 准确会话与权限身份

- 宿主：真实本机 Codex CLI `0.162.0-alpha.17.2`。
- 模型/推理：`gpt-6.1-sol` / `medium`，复用现有用户配置值，没有切换模型。
- 探针 thread：`01a12516-ecc6-7920-8824-fca95e0a37bf`。
- 临时 profile：`atlas_stage0_probe`，通过参数设置，extends `:read-only`；root deny、minimal read、私有 allowed 根 read、全局 tmpdir/slash_tmp deny，command network restricted。
- 真实 turn_context 的 active_permission_profile 与 restricted filesystem 条目吻合。实际执行中仍保留宿主注入的 shell/runtime 精确读取路径；不把 minimal/runtime 例外宣称为绝无其他读取。
- 启动使用 `--ignore-user-config --strict-config`，没有传 legacy `--sandbox`；进程参数关闭 web search、apps、remote_plugin、multi_agent、hooks、memories、shell_snapshot，并设 MCP map 为空。

这组参数并不证明每类能力都完全消失。stderr 仍出现本机 ngs-analysis plugin manifest 的提示，turn_context 的 disabled_plugin_ids 为空；本次未调用这些能力。因此只确认 Atlas 工具为空、实际 shell 隔离成功，未宣布完整插件/其他工具面的隔离。

## 本次事件关联

本次合成读取对象为 `source_canary.txt` 第 1 行，文件 SHA256：`526fcb069f2c85cb6f9bc7a44cb4fd06e06c2fc0b55fa2e24ddd57bda85cb5f0`。

宿主原始 UserMessage completion：`1791623627041` ms；该文件读取 CommandExecution completion：`1791623632861` ms。两者来自同一个真实 turn，实际 head 命令及输出吻合。相差 5820 ms 仅是本次安全探针的校准记录，不是 Atlas 性能成绩。没有使用外层 subprocess 开始/结束时刻或 shell duration 代替宿主事件。

输出存在两个层面：成功命令有原始 CommandExecution item；被拒绝的两项操作没有进入终端 stdout 的 command items，但在原始 custom_tool_call/output 和 Seatbelt violation 日志中有匹配的拒绝证据。后续评分不能只采集终端可见成功项，也不能把缺 command item 当作没有尝试。

本次仅对一行公开 canary 成功关联；真实多文件读取、版本变更、截断、长命令、partial、MCP tool completion 与所有失败计分分支仍未验证。正式评分合同须明确选择原始 user item 的完成事件为任务接收时刻，并冻结来源与 schema，不混用 thread creation 或 task-start 的准备时间。

## 保留的精确证据

私有基目录：`${LOCAL_ATLAS_HOME}/_experiments/experience-stage0/permission-probe-20261010T091156Z-84d72c0b`。

`evidence/` 内：

- sandbox-enforcement.json：OS 检查参数、退出码及保护对象摘要。
- agent-launch.json：实际 argv、公开探针提示、PID、退出码；observer timestamps 明确不是评分时间。
- agent-events.jsonl、agent-stderr.log：真实终端输出与拒绝日志。
- probe-rollout.filtered.jsonl：只保留本次必要用户事件、工具请求/输出、实际权限与事件；没有导出 reasoning。
- probe-event-mapping.json：来源 item ID、原生时间戳、文件摘要与读取区间。
- post-probe-integrity.json：保护对象前后摘要。
- probe-summary.json：结论及仍缺的门。

原始本次 rollout 位于宿主自身 sessions：`${CODEX_HOME}/sessions/2026/10/10/rollout-2026-10-10T17-13-41-01a12516-ecc6-7920-8824-fca95e0a37bf.jsonl`。只读取这一明确探针文件，没有扫描其他聊天正文；导出副本过滤推理及不必要内容。

## 尚未闭合与下一步

本次关闭的是“本机真实终端可执行临时受限 shell 读取”和“公开 canary 有原生宿主事件关联路径”两个具体可行性问题。不是 Atlas 候选接入或完整 G1/G2/G3 验收。

仍需：其他能力/插件的有效边界，Atlas MCP 自身权限与准确私有 repository/fresh 查询，候选指引及其实际来源，多文件/变更/失败事件采集合同，写入型工作会话与评分型规则及公开配对校准。readonly 参数不能直接当作修复会话的写权限。

本次授权先做一个安全探针，不扩展为更多会话或配对任务；既有执行卡仍供下一步范围审阅。新 canary、探针任务和观察结果加入历史排除，不能用作正式独立任务。
