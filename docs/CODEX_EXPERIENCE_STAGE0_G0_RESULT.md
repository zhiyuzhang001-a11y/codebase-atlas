> **2026-10-11 收尾归档：开发已由用户决定暂停，不再自动推进。** 下文为逐时点历史记录，旧授权、待执行动作及“当前”状态不再构成启动许可；最终结论以[收尾报告](CODEX_EXPERIENCE_CLOSEOUT.md)为准。本机绝对路径以变量代称保留结构，私有原始证据未上传。

# 阶段零 G0 前置核对结果

日期：2026-10-10。状态：已执行只读核对；读取隔离前置条件未满足，未创建实验项目或会话。

后续用户已批准本文件提出的仅进程临时权限与一次安全探针。新增运行证据见 [安全探针结果](CODEX_EXPERIENCE_STAGE0_SAFE_PROBE_RESULT.md)；下文“尚未实施”保留为批准前历史状态，不覆盖新增实证。

执行依据：[第二版执行卡](CODEX_EXPERIENCE_STAGE0_EXECUTION_CARD.md) 的 G0。用户要求按计划执行，本轮已开始该只读步骤；没有运行样例或 Atlas 生命周期、查询替代命令。

## 实际取得的本机证据

- Codex executable：`${CODEX_EXECUTABLE}`。
- `codex --version`：`codex-cli 0.162.0-alpha.17.2`。
- 可执行文件 SHA256：`0d5ac7aae20c4eda6242428d5e218c55ba96d8763fda2a271088ff37104b9d28`。
- `codex exec --help` 确认本机支持 `-c`、`--cd`、`--json`、`--ignore-user-config` 与 `--strict-config`。只执行 help/version，没有提交 agent prompt。
- `codex sandbox --help` 支持 `-P/--permission-profile` 与 sandbox-state 参数。命令帮助并不能证明指定规则在模型会话和所有工具面生效。
- 只读取全局及产品项目 TOML 的 permission/sandbox 字段：用户全局为 `sandbox_mode='danger-full-access'`、`approval_policy='never'`；两文件均无自定义 `[permissions]` 表，未发现用户 profile 文件。
- `/etc/codex/config.toml`、`/etc/codex/requirements.toml` 与 `/Library/Application Support/Codex/requirements.toml` 这三个明确路径不存在。本轮没有枚举管理系统或断言绝无其他 MDM/Agent Security 配置。
- 可调用工具未提供 permissionProfile/list 或原始宿主事件导出专用工具。没有读取其他聊天或历史 rollout 正文。

## 判断与具体缺口

G0 的“使用已经可用、可限制旁侧读取的权限方案”条件没有取得。built-in read-only/workspace 不能仅凭名称当作读取隔离；0700、cwd 和禁止写入也不满足本卡要求。默认 full access 不可用于本卡对照会话。

当前还未取得能将公共提示接收、工具完成、输出与源码区间关联的原始事件路径及完整字段。`--json` 支持已证实，计分时间戳没有实证；未用接收端 shell 时间或最终回答替代。未来安全探针可以验证字段，但不能先忽略读取边界启动会话。

因此不物化四个项目、不首次索引、不启动探针或配对任务。阶段零设计不作废；本轮只报告这一执行门的确切缺口。

## 最小后续范围提案：仅进程权限规则

原卡明确不自行新增/修改 permission profile。最小扩展是允许为本卡真实终端进程提供临时 named profile 参数，而不保存到全局/项目配置、不修改 trust、不改其他项目。此扩展尚未实施；现有“按计划执行”不被解释为取消卡片的明确权限边界。

官方文档提供限制读取的 profile 语法。以下是供审阅的参数语义草案，不是已验证可运行 argv，也不是写入配置的建议；必须核对本机 strict-config 与有效规则，最终 receipt 使用真实私有根、Python 及其标准库路径。

```toml
default_permissions = "atlas_stage0_private"

[permissions.atlas_stage0_private]
extends = ":workspace"

[permissions.atlas_stage0_private.filesystem]
":root" = "deny"
":minimal" = "read"
":tmpdir" = "deny"
":slash_tmp" = "deny"

[permissions.atlas_stage0_private.filesystem.":workspace_roots"]
"." = "read"
"src" = "write"
"tests" = "write"

[permissions.atlas_stage0_private.network]
enabled = false
```

上文不能直接作为工作会话最终规则：`tests` 只允许追加的要求还需原测试摘要与评分重放守门；运行依赖的精确读取路径、MCP 连接相关 runtime 及必要私有临时路径也待核实。若必须开放临时路径，应使用该会话独有目录，不能扩大为整个共享 tmp。评分会话使用单独规则，不能复制工作会话的写权限。

官方说明 legacy `sandbox_mode` 或 `--sandbox` 可优先于 named permissions。拟启动方式使用 `--ignore-user-config` 和逐项 `-c` 覆盖，避免加载全局 full-access 字段；两组重新固定相同模型/推理与共同设置。必须确认其余加载层不重新引入 legacy sandbox 设置，不简单声称 -c default_permissions 已覆盖一切。没有必要修改全局文件。

Permission profiles 主要约束 local command execution，不覆盖 MCP、浏览器、Computer Use 或 connectors。后续完整 receipt 还必须禁用非必要能力并证明实际工具面；Atlas 自身仅绑定准确私有 repository，其他 Atlas MCP/技能禁用。只配置 shell profile 不能宣称所有工具均隔离。

最小扩展如获批准，先限定为规则解析及一个安全探针，不自动运行四个修复会话。原卡的 G1/G2/G3 仍须通过；若本机拒绝规则、允许旁侧读取或不能导出有效原始事件，则保留证据、停止配对路线，不增加 OS 用户、容器、hook 或新的采集服务来绕过问题。

## 本轮操作与未发生的动作

仅执行 Codex version/help、选择性 TOML 读取、二进制摘要与官方文档读取，并新增本结果文档。没有写配置、trust、源码、缓存或索引；没有开启新实验会话、执行测试、安装更新、Rust 或 heartbeat 操作。没有读取 auth 字段或输出凭据。

官方依据：[Permissions](https://learn.chatgpt.com/docs/permissions)，2026-10-10 实际读取。它明确 named permissions 与 legacy sandbox 不组合、workspace-only 的 root deny/minimal read 示例，以及 profiles 对各工具面的作用范围；本机是否执行这些规则仍须实际核验。
