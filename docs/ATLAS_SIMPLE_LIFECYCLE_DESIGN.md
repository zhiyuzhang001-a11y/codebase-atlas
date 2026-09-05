# Atlas 四命令生命周期设计

状态：第一阶段设计冻结，等待第二阶段实现。

## 已验证边界

- Codex 的 MCP 注册与 Atlas 的项目状态重载是两层能力。官方 OpenAI 文档未提供运行中任务热加载新增 MCP 注册的保证；本仓库的部署规范要求 MCP 配置变更后新开任务验证。
- 因此，只有已经启动 Atlas 稳定引导服务的任务能在同一 MCP 连接内观察 `enable`、`stop` 和后端切换。一个从未加载 Atlas MCP 工具的旧任务不能由 Atlas 自行注入工具。
- 当前 `mcp-auto` 只在进程启动时执行一次 `resolve_project`。未配置时创建固定的状态服务；之后创建配置不会重建服务。现有针对性测试确认了这一行为。
- 项目刷新已有跨进程 `ProjectRefreshLease`、发布日志和回滚；共享 Provider 已有账户级入口、准确项目身份和短时全局准入锁。新实现复用这些能力，不另建索引或 Provider 生命周期。
- 当前代码只有版本通知和发布/迁移验证，没有可信 Release 的下载、校验、版本化安装与原子选择器。`update` 必须新增这一管理层，不能把版本通知包装成升级。

## 产品入口

发布包新增 `atlas = codebase_atlas.simple_cli:main`。现有 `codebase-atlas` 高级接口保持兼容。

`atlas` 命令存在的前提是机器已经安装过一次 Atlas 引导程序。`enable` 可以准备或复用受管运行时，但一个尚不存在的命令无法自举自身；安装文档只需解释一次引导安装，日常操作收敛为四个命令。

所有命令默认解析当前目录所属的准确 Git 根，也支持 `--repo <absolute-path>`。歧义、配置身份冲突、受管块冲突或仓库变化一律失败关闭。

统一输出结构版本为 1，至少包含：

```json
{
  "schema_version": 1,
  "operation": "enable",
  "status": "ready",
  "repository": "/exact/repository",
  "atlas_version": "0.x.y",
  "project_state": "ready",
  "index_status": "fresh",
  "connection_status": "loaded",
  "mutates": true,
  "next_action": ""
}
```

展示输出从这个结构生成；自动化只使用 `--json`、稳定错误码和退出码。

## 状态与身份

启用后的权威生命周期文件位于项目现有 `data_dir` 下的 `lifecycle-state.json`。它包含仓库规范路径、Provider 项目身份、所选 Atlas/Provider 版本、状态、操作代次、最后成功代次和失败信息。文件使用临时文件、`fsync`、原子替换发布，并拒绝符号链接或身份不匹配。

公开稳定状态为：

- `ready`：允许查询和自动刷新。
- `stopped`：拒绝查询和自动刷新，保留配置与索引。
- `removed`：拒绝查询和自动恢复，项目资产已经转移到恢复区。
- `failed`：最近一次操作失败；保留 `last_ready_generation`，是否可查询由回滚结果明确决定。

`enabling`、`stopping`、`updating`、`removing` 是带操作 ID 的事务状态。进程崩溃后，下一次同项目操作根据日志完成回滚或安全重试，不能仅依据状态名字宣布成功。

删除后的收据存放在账户级 Atlas 状态根，键为规范仓库路径的 SHA-256 与可读仓库名；恢复区只接收能够证明由 Atlas 管理且身份匹配的项目资产。收据记录原路径、恢复路径、摘要、操作 ID 和时间。业务源码、外部数据及共享安装永不进入恢复区。

## 锁顺序

新增每项目 `ProjectOperationLease`，身份算法与 `ProjectRefreshLease` 相同，负责四命令串行化。固定获取顺序为：

1. 项目操作锁；
2. 项目刷新独占租约；
3. Provider 全局短时准入锁。

查询不获取项目操作锁。每次请求先读取并验证生命周期状态，只有 `ready` 才获取刷新共享租约，然后进入 Provider。任何代码都不得持有 Provider 全局锁等待项目锁或刷新租约。`stop`、`update` 和 `remove` 持有项目操作锁后等待或有界拒绝正在进行的刷新，避免混合 generation。

不同项目只共享版本化程序资产和 Provider 准入机制，不共享配置、生命周期文件、操作锁、项目身份或索引事实。

## 稳定 MCP 引导层

`mcp-auto` 改为稳定引导服务，不再在启动时永久选择“固定状态服务”或“固定业务服务”。它绑定启动时验证过的准确仓库根，并在每个请求边界重新读取以下指纹：

- 项目解析状态；
- 配置文件设备号、inode、大小和修改时间；
- 生命周期状态与操作代次；
- 所选 Atlas 版本和已发布索引 generation。

引导层自己处理 `initialize`、`ping`、`tools/list` 和 `project_status`，工具名保持稳定。业务查询只在项目 `ready` 且身份一致时转发；`stopped`、`removed`、歧义和迁移失败均在启动 Provider 前失败关闭。

业务后端必须是所选版本目录中的子进程，而不是引导进程内导入的 Python 对象。这样 `update` 才能在同一外层 MCP 连接中停止旧后端、清除 continuation/session 缓存并启动验证过的新版本。引导程序本身的新功能仍需宿主重连，不能承诺运行中的旧 Python 代码自行升级。

首版不依赖宿主私有 API，也不修改 Codex 私有状态。外部 `stop` 原子发布状态后，新请求必定被拒绝；引导层在观察到新状态时同步关闭它拥有的后端。主动立即回收一个完全空闲连接的子进程需要后续跨平台控制通道，不能影响“停止后不再执行查询”的正确性。

## 四个操作的事务

### enable

1. 只读解析准确仓库、现有配置、Codex 受管块、受管安装和恢复收据。
2. 获取项目操作锁，重新核对发现结果未变化。
3. 复用受信稳定安装；缺少时按 Release 清单下载 wheel、校验和及当前平台 Provider 包，逐个验证后安装到新版本目录。
4. 复用现有 `onboard` 计划与 apply，拒绝覆盖不同配置；从 `stopped` 恢复时保留索引并按需刷新。
5. 复用项目级 `codex plan/apply` 准备稳定引导入口。
6. 要求身份准确、doctor ready、fresh、Provider 深检健康、目标符号命中与跨项目负例通过，才原子发布 `ready`。
7. 分别报告项目可用和宿主连接状态；首次新增 MCP 时仍提示新任务验证，不伪装为当前任务已加载。

### stop

1. 获取项目操作锁并发布 `stopping`。
2. 有界等待项目刷新独占租约；超时保持原状态并返回可重试错误。
3. 发布 `stopped`，使所有后续请求在 Provider 前拒绝。
4. 已连接引导层观察状态后关闭自己的后端；不停止其他项目或不归本进程所有的共享 Provider。
5. 重复执行返回成功且 `mutates=false`。

### update

1. 仅接受最新非草稿、非预发布 Release，验证 wheel、清单和平台 Provider 相邻校验和。
2. 安装到新版本目录，保留旧版本；禁止覆盖当前目录。
3. 在项目隔离候选数据或可回滚备份上运行迁移预览、迁移、doctor 和真实正负查询。
4. 获取项目操作锁并重新核对源 generation，原子切换版本选择与兼容数据。
5. 引导层在下一个请求边界关闭旧后端并启动新后端；切换失败恢复旧选择。
6. 原项目为 `stopped` 时更新后仍为 `stopped`；未启用项目返回先执行 enable。

共享 Provider 的并存单位是 Provider 布局 ABI 与版本化根。未验证两个 Provider 版本能安全共享一个守护进程时，更新实现必须串行切换或使用隔离根，不能假定兼容。

### remove

1. 获取项目操作锁，执行 stop 语义并验证准确身份。
2. 只移动 Atlas 所有且可验证的项目配置、索引及 Atlas 管理的 Codex 块；保留 `.codex/config.toml` 中所有其他字节。
3. 将项目资产原子移动到唯一恢复目录，写入并验证收据后才发布 `removed`。
4. 保留共享 Atlas/Provider 安装和其他项目数据。无法证明所有权时失败，不做部分删除。
5. 重复执行通过收据返回相同结果；首版不提供永久清除。

## 实现切片

第二阶段按可独立验收的顺序实施：

1. `project_lifecycle.py`：状态、收据、原子文件和 `ProjectOperationLease`；覆盖幂等、身份、符号链接、崩溃与双进程测试。
2. `simple_cli.py` 与 `atlas` 入口：先接通 `enable`/`stop`，复用 onboarding 与 Codex project integration。
3. Release 安装/选择器：实现 `update` 的可信资产、版本化安装和回滚。
4. 恢复区事务：实现 `remove` 和收据验证。
5. 稳定 MCP 代理：先实现同版本未配置→启用→停止→恢复，再实现跨版本后端切换。

每个切片必须先通过目标单测，再跑全套测试。正式发布、向 PDF 项目部署或修改全局 Codex MCP 配置不属于本阶段授权。
