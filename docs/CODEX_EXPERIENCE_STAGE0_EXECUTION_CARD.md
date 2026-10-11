> **2026-10-11 收尾归档：开发已由用户决定暂停，不再自动推进。** 下文为逐时点历史记录，旧授权、待执行动作及“当前”状态不再构成启动许可；最终结论以[收尾报告](CODEX_EXPERIENCE_CLOSEOUT.md)为准。本机绝对路径以变量代称保留结构，私有原始证据未上传。

# 阶段零私有宿主校准执行卡

> 2026-10-11 权限更新：当前连续执行、阶段复核与会话调整以[总计划第四版第十二节](CODEX_EXPERIENCE_AND_WORKFLOW_MASTER_PLAN.md#十二-连续执行与独立阶段门2026-10-11)为准。下文保留历史授权及实证；新授权不改变失败、未运行或未通过的结果。

> G1-v2 替代开发校准合同已完成独立设计复核，见总计划末节；原 G1-v1 失败保留。仅具体新版 argv/账本复核通过后执行，新合同不称完全无 Atlas 背景。

日期：2026-10-10。版本：2。状态：已批准并开始执行；G1 指引隔离失败，配对路线停止，详见 [执行结果](CODEX_EXPERIENCE_STAGE0_EXECUTION_RESULT.md)。工作时间不设上限。下文“未执行／拟授权”保留为审批时的设计记录，不代表当前状态。

本卡只回答四件事：真实 Codex 宿主能否隔离普通组与 Atlas 组；能否记录可复核事件；能否完成两个公开开发任务；能否恢复本卡所属连接而不影响现有项目。它不实现产品改动，不证明效率收益，不使用正式独立集，也不授权后续阶段。

阶段零缺口与历史来源见 [缺口闭合说明](CODEX_EXPERIENCE_STAGE0_GAP_CLOSURE.md)。本卡替代阶段零报告中原 120 分钟执行卡；旧卡的工时分配不再有效。

## 1. 审阅对象与拟授权范围

本次仅编写本文件及相关阶段零说明，没有创建下列目录或会话。

建议批准本卡时明确包括：

- 在产品仓、父评估仓及其历史目录之外创建一个权限为 0700 的私人实验目录；其中建立四个 Git 根、各自的源码副本、配置和证据目录。Git 可为 unborn，不提交源码，不复用用户项目配置。
- 使用既有稳定 0.26.1 frontend 与既有 Managed Provider、Serena 和 Python；私有候选项目首次索引。不得更新安装、迁移用户数据或刷新产品仓。
- 使用真实 Codex 终端宿主，以仅进程有效的配置分别运行普通组和 Atlas 组；不修改全局配置、编辑器、信任记录或其他项目。
- 先做最多三个宿主前置探针会话（普通组一个、两个 Atlas 根各一个）；探针通过后最多运行两个全新开发任务的四个独立工作会话，再用一个独立评分会话。本卡最多八个会话，不包括未来正式独立评测。探针会话和评分会话单列记录，不假装包含在四个工作会话中。
- 仅执行本卡第 5 节公开样例及列明的测试命令；保存这批私有会话的必要原始事件、公开提示、工具请求与结果。原始结果可能包含这些合成源码，采集范围不包括用户项目源码、其他聊天、凭据或模型私有推理。
- 用独立评分会话人工核对公开开发任务的事件与正确性，并执行第 5 节的冻结测试重放；不实现或运行新的评分器脚本。评分会话不参加修复、不把对方轨迹发给工作会话。可见开发答案不属于正式隐藏真值。
- 退出明确属于本卡的前端进程并恢复本卡临时配置，保留样例、证据和 receipts；不删除目录，不终止共享 Provider daemon 或其他项目进程。

不需要现在创建产品工作区或新分支：宿主与计量可行性先复用既有稳定安装。建议的产品开发源码基线仍为 `6ce74df210bc4af4b1be705fb95000a46d9a3e8d`；未来基于该 SHA 的实现工作区、构建和候选接入另列执行范围，不能用本卡 0.26.1 的结果验收 0.27.0 源码或未实现的改动。

## 2. 精确身份与两组差异

机器路径只属于本地审阅材料，不应随产品提交发布。执行前生成 receipt，核对实际文件存在、可执行性、包版本、可取得的安装来源摘要和 Provider manifest；任何不符先报告，不下载替代版本。

拟复用：

- frontend：`${LOCAL_ATLAS_HOME}/_installations/v1/0.26.1/environment/bin/codebase-atlas`。
- Provider：`${LOCAL_ATLAS_HOME}/providers/0.10.8-atlas.2+e088a41b/macos-arm64/codebase-memory-mcp`；历史已核对二进制 SHA256 为 `c97fa1977baf10f7cd401a42ce84a509d9fdae4b2fc1681affaf319ef5d6690f`，本卡运行前重新比对。
- Serena：`${WORKSPACE_ROOT}/.evaluation-tools/serena-venv/bin/python`。
- Node：`${WORKSPACE_ROOT}/.evaluation-tools/m17-node-download/node-v24.19.0-darwin-arm64/bin/node`。本卡样例不执行 Node；它作为既有 Atlas runtime 前置依赖保留。

两组同一模型、推理设置、非 Atlas 指引、权限、工具版本及公开任务。先记录实际 Codex 可执行文件版本和摘要，不指定或自动升级模型。私有根以 `DEV-A-control`、`DEV-A-atlas`、`DEV-B-control`、`DEV-B-atlas` 区分；每对源文件字节相同，绝对根和项目身份各不相同。源码初始状态以排序后的相对路径及 SHA256 冻结；unborn Git 明确记为无 HEAD，不能编造 SHA。

普通组没有 Atlas 工具、Atlas 专属 AGENTS/技能或 server instructions；Atlas 组仅额外拥有准确版本工具及下面的候选指引。不得把本产品 AGENTS 或交接聊天复制到工作会话。

指引载体为每个私有根的 `AGENTS.md`，本卡获批后才写入。普通组文件只含以下共同文本，Atlas 组含逐字相同的共同文本再追加候选文本；分别冻结完整文件和两个文本段的摘要。实际加载由宿主来源和内容证据核对，不能仅凭文件存在认定生效。

共同文本：

> 先用有界源码阅读理解任务，保留已有修改。只修改本任务允许的 src，并可追加相关测试；不删除或放宽已有测试。只运行执行卡列明的测试命令。不要读写其他项目、相邻任务或评分证据目录。说明实际修改和测试结果，不把未运行、失败或缺失的证据报为通过。普通源码读取不需额外询问。

候选追加文本：

> 已知文件的局部修改直接阅读，不为此主动查询 Atlas。位置未知且 Atlas 已加载时，先检查 project_status 的 repository 与当前 Git 根一致且 fresh，再最多一次 locate_files、最多阅读两个候选；候选不是根因。普通源码确认准确 symbol 和 target_path 后，需要跨文件关系或测试证据才调用 analyze_change，显式 response_mode=compact。保留 partial、not_run、error 与截断状态，缺失证据用有界源码检查补充。无工具、停用、身份错配或过期时直接普通源码回退，不 enable、首次索引或刷新；busy/startup 最多一次重试，共享最多60秒等待。不要向 Atlas CLI 或 stdio 自行发查询代替宿主 MCP。

两组均以进程设置禁用其他 Atlas 技能，避免候选文本与旧的强制查询路由叠加；稳定安装的 server instructions 和工具说明属于候选组真实干预，照实摘要保存，不能声称其字节与上文相同。不得运行 `codex apply` 或 lifecycle enable 来安装额外路由。onboarding 只接受不改写本卡 AGENTS/技能/MCP 配置的准备计划；若实际版本计划会生成或替换这些文件，暂停审阅该精确计划，不隐式覆盖或叠加。源自系统的无法禁用路由使两组条件不成立时停止。

## 3. 启动与隔离前置门

G0 是物化四个私有项目、首次索引或创建任何探针/工作/评分会话之前的只读预检。先取得本机已经可用的 permission profiles、准确参数语法和有效读取/写入规则，以及可用的原始事件导出来源；只读列举不代表已批准修改配置。未取得具体安全启动方案时，不先启动广泛可读的会话来碰运气。

G0 的待填写 receipt 必须列出：Codex executable/version、宿主类型、选定既有权限配置 ID、准确 argv、模型/推理身份、任务允许读根（仅本任务源码和经核验运行依赖）、允许写根（仅本任务 src/新增测试）、拒绝读取的旁侧任务/评分/用户项目路径、策略来源与可用只读验证方式、原始事件来源及导出操作。评分会话采用独立权限，允许读评分副本、写其自己的结果，禁止修改工作证据。路径模板不是有效权限证据，所有项必须在会话创建前填实。

0700 只限制其他 OS 用户，不能限制同 uid 的 Codex 会话；`--cd`、提示词约束和仅写入沙箱也不足以证明读取隔离。若现有 profiles 只能限制写入而不能防止读取旁侧证据，则本卡配对路线阻断，报告限制。不得自行新增/修改 profile、OS 用户、容器、trust、全局配置或绕过管理策略；需要另一种隔离方案时单独审阅。G0 不要求独立收益或未来候选运行证据，只要求有限实验可安全启动。

官方文档支持仅进程 `-c` 覆盖、MCP `enabled` 与 per-skill enablement，但不能据此宣称本机已隔离。项目配置受 trust 影响，本卡不以添加 trust 修复启动。优先使用进程覆盖；若本机不支持，不自动改全局或切换宿主路线。

执行前仅针对隔离所需读取有效配置名称、继承指引路径、Atlas 技能路径和插件状态，不记录配置中的 secrets。普通组逐个禁用所有可发现 Atlas server 和 Atlas 专属技能；两组同时固定 memories、非必要 apps/plugins 与其他辅助工具条件。只忽略 user config 或只禁用一个 server 都不能证明指引隔离。禁止调用会持久化的 `skills/config/write` 或 `codex mcp add`。

以下是待版本前置核对的参数模板，不是已运行的命令。大写占位符须在 receipt 中换成经过核验的绝对路径；以参数数组传递，不能拼接未经转义的 shell 字符串。

```text
普通组额外覆盖：
-c mcp_servers.codebase_atlas.enabled=false
-c skills.config=[{path="ATLAS_SKILL_DIRECTORY",enabled=false}]

Atlas 组额外覆盖：
-c mcp_servers.codebase_atlas.command="VERIFIED_FRONTEND"
-c mcp_servers.codebase_atlas.args=["mcp","--config","PRIVATE_ATLAS_CONFIG","--auto-update","off","--version-check","off"]
-c mcp_servers.codebase_atlas.enabled=true
```

上面的 server 名只涵盖已知入口；实际发现的其他 Atlas 入口也必须禁用。`skills.config` 使用技能目录，不是 SKILL.md 文件名。若合并规则、继承技能或管理配置不能保持隔离，停止对照路线。

真实终端任务可用 `codex exec --json --cd PRIVATE_ROOT -c ... -` 作为待核验启动模板；stdin 是相同公开任务。它运行真实 Codex agent，Atlas 必须由宿主加载；禁止自行向 Atlas stdio 发查询来冒充宿主验收。不得使用 `--ephemeral` 丢弃所需宿主记录，不加跳过审批或信任的参数。准备命令不能自动获得受测代码执行权限。

首次索引只限两个 Atlas 私有根。采用对应已验证 release 的 canonical 只读 onboarding 预览，审阅 JSON 精确 apply 参数后再在本卡授权内 apply；不把模板硬编码为所有版本都可运行。索引和数据必须在各自私有目录，各配置的 repository 必须是其自己的 Git 根。CLI doctor/deep inspection 可作准备健康证据，真实查询验收仍只能来自宿主 MCP。

G1 普通组探针：新会话真实工具清单没有任何 Atlas 工具；加载指引来源及可见技能摘要没有 Atlas 路由。无法观测来源时不能计通过。

G2 Atlas 组探针：真实 `project_status` 的 repository 精确匹配私有根、fresh；doctor ready、deep healthy 的准备记录齐备；`definition` 查询独有 canary 精确返回该根的 `host_canary.py`；请求另一个私有项目独有 canary 返回零 foreign facts。canary 在两个 Atlas 项目分别使用 `atlas_host_probe_a` 和 `atlas_host_probe_b`，仅返回各自标识字符串。探针不触及样例任务的 bug 区域，探针会话不复用于修复。

G3 轨迹探针：完成一次已知相对路径及区间的合成文件读取。必须从宿主记录得到公共提示接收、工具完成、读取输出及版本映射；只有 stdout 到达时间、shell 自行计时、进度摘要或最终回答都不够。成功须报告事件字段和来源，而非只写“支持 JSON”。

三个门全部通过才运行任务。任何身份错配、foreign facts 或隔离失败停止该路线；连接丢失报告原始状态，不重装、不重建用户索引、不改 trust。

## 4. 轨迹与评分合同

只使用该批私有会话的真实宿主原始事件。最小字段为 task/session/turn/item/request ID、事件顺序、宿主原始时间与精度/时区、事件类型、工具业务状态、读源码相对路径和区间、文件版本摘要、输出截断、模型/配置/包身份、结束状态及测试退出码。完整请求或工具输出仅存私有证据；去标识评分副本删除方案标签及绝对路径，保留判断所需相对位置和业务状态，不采集推理。

原始事件缺提示接收时间、工具完成时间、输出或区间/版本关联时记 `measurement_invalid`，不以线程 createdAt、command durationMs 或接收端时间替代。当前官方 app-server 示例提供 item started/completed 和 MCP/command 项，但尚未证明本机导出具有全部计分时间字段。`read_thread` 摘要不是原始事件。不要为补字段新增 hook、日志代理或重型采集服务；需要新采集方法时提出单独设计。

首证据时间从公共任务被宿主接收到首次完成读取预先定义实现区域；搜索到名字或 Atlas 返回路径不算。多区域同一输出使用该工具完成时间，不能推测内部更早时刻。输出被截断且看不到必需区域时不计有效读取。重复阅读按同一文件摘要下行区间交集计算；变更版本另算。只有文件名、测试答案或最终结论不能替代实现读取。

两个开发任务的真值可见，评分员先验证样例预期和判定逻辑；不把它们升级为封存独立任务。采集合同先用纸面事件例检查正常、仅文件名、版本变更、区间重叠、缺事件、截断、乱序、超时、零基线九种分支。本卡只由独立评分员人工核对，不实现或运行新评分器；正式独立验收所需评分器仍须另列源码、输入输出、命令和审查范围。

配对效率公式沿用 `(B-C)/B`：B=C=0 记 0；B=0、C>0 记 -1 并单列。有效轨迹中未取得证据或未完成才按冻结超时计分；无效计量不是超时。15 分钟是待校准的每个开发会话参数，不是工时上限；正式验收前根据公开校准冻结最终值，不在结果出来后改门。两个任务不计算或宣称统计收益。

## 5. 可审阅的两个公开开发任务

两任务均为本轮新写的合成 Python 样例，公开用于校准。它们之后永久列入历史排除。此小卡只校准本机 Python 宿主；TS/JS、其他平台和全部六类合同仍需后续验证，不能用这四个会话判整体通过。

DEV-A 已知文件局部修复。公共提示：

> `src/labels.py` 的 `normalize_label` 应去掉两端空白；只改这个局部行为，保留内部空白和大小写。请修复并运行现有相关测试，说明结果。

初始文件如下。tests 位于根 `tests`，src 与 tests 不需第三方包。

```python
# src/labels.py
def normalize_label(value):
    return value.lstrip()
```

```python
# tests/test_labels.py
import unittest
from src.labels import normalize_label

class LabelTests(unittest.TestCase):
    def test_edges(self):
        self.assertEqual(normalize_label("  Hello  "), "Hello")
    def test_internal(self):
        self.assertEqual(normalize_label("A  B"), "A  B")
    def test_blank(self):
        self.assertEqual(normalize_label("   "), "")
```

评分区域是冻结初始 `src/labels.py` 的函数两行；正确性要求三个测试通过且大小写未被改写。候选组应无需 Atlas 调用。已有文件定位无需借助图查询；记录任何额外调用及其原因。

DEV-B 未知位置与跨文件合同。公共提示：

> 超时设置显式传入 0 应表示禁用超时，缺省才使用默认值；负数应报错。请修复两种入口中的这个行为，保留正数和原来的返回格式，运行相关测试并说明影响范围。

工作会话不附函数名或目标路径。目录公开，初始源码如下；测试为公开开发测试。

```python
# src/options.py
def choose_timeout(value, default=30):
    return value or default
```

```python
# src/request.py
from src.options import choose_timeout

def request_options(value=None):
    return {"timeout_seconds": choose_timeout(value)}
```

```python
# src/batch.py
from src.options import choose_timeout

def batch_options(value=None):
    return ("batch", choose_timeout(value, 60))
```

```python
# tests/test_options.py
import unittest
from src.request import request_options
from src.batch import batch_options

class OptionTests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(request_options(0), {"timeout_seconds": 0})
        self.assertEqual(batch_options(0), ("batch", 0))
    def test_none(self):
        self.assertEqual(request_options(), {"timeout_seconds": 30})
        self.assertEqual(batch_options(), ("batch", 60))
    def test_positive(self):
        self.assertEqual(request_options(5), {"timeout_seconds": 5})
        self.assertEqual(batch_options(5), ("batch", 5))
    def test_negative(self):
        for entry in (request_options, batch_options):
            with self.assertRaises(ValueError):
                entry(-1)
```

评分区域是冻结初始 `src/options.py` 的函数两行；正确性要求两入口的 zero/None/positive/negative 合同均通过。关系查询应在工作会话普通源码确认 `choose_timeout` 与路径之后进行，显式 compact，保留 completeness。修复不要求修改所有关联文件。

允许执行的受测命令，仅在对应私有根：

```text
VERIFIED_PYTHON -B -m unittest discover -s tests -p test_labels.py
VERIFIED_PYTHON -B -m unittest discover -s tests -p test_options.py
```

每个命令只对应本任务存在的测试。使用既有 Python 3.11–3.14；不 pip/npm install、不运行用户项目测试、不 import 产品仓源码。可用 rg、sed 和标准库生成/核对样例摘要。工作会话可以修改本任务 src，追加本任务测试；不写相邻根、证据、配置、.git 或产品源码。上述代码只是文档样例，本轮未物化或执行。

评分必须用执行前冻结的原测试断言检查修复后源码，另核对新增测试和 diff；删除或放宽原测试不算正确。评分会话不参与修复。原测试快照保存在工作会话不可读取或改写的评分目录；这里属于公开开发答案，所以隔离目的是保护判定完整性，而不是声称答案隐藏。

具体重放：准备者在工作会话启动前保存本节原测试的冻结字节和摘要；会话完成后只读核对产物，将修复后的 src 文件复制到独立评分目录的 `replay/DEV-A/control`、`replay/DEV-A/atlas`、`replay/DEV-B/control`、`replay/DEV-B/atlas`。每个目录的 tests 来自冻结原测试，不来自工作会话修改后的 tests。复制时拒绝符号链接和范围外路径，记录每个源码摘要及工作产物到评分副本的对应；评分不回写工作根。

在 DEV-A 的两个重放根分别执行上面的 test_labels 命令，在 DEV-B 的两个重放根分别执行 test_options 命令，记录 cwd、Python 身份、退出码和输出。独立评分员以去方案标识的副本核对事件，复制映射由准备者保管；工具名导致的残余非盲性照实记录。追加测试只作补充，不替代原冻结断言。上述私有复制、摘要核对和四次测试重放属于本卡拟授权执行范围，无新的脚本或第三方依赖。

## 6. 顺序、恢复和结果出口

执行顺序：G0 只读权限与采集预检 → 准备与 receipts → G1/G2/G3 探针 → DEV-A 普通/Atlas → 核对事件有效性 → DEV-B Atlas/普通 → 冻结测试重放、独立人工评分与恢复。配对交替顺序，不 fork/resume 前一工作会话，不共享另一组修改、轨迹或评分答案。模型无法读取旁侧项目和证据须在创建会话前由实际权限证明，不能仅写在提示词中；无法隔离文件读取时停止。

探针通常使 Provider 已热。记录真实冷/热与共享 daemon 条件，不宣称后续查询是冷启动，不为制造冷状态终止共享 daemon。不同组缓存不能严格匹配则报告混杂因素；这批只校准，不用于效率收益结论。

恢复保存临时配置原始存在状态、字节和 SHA256。仅恢复仍与本卡写入版本一致的文件；出现外来修改报告冲突，不能覆盖。退出精确本卡 frontend，依 PID、启动时间、argv 和私有根核对所属，不按名称批量杀进程。共享 Provider 保持运行。恢复不删除私有源码/数据/证据，不移动历史资料；核对现有产品、父仓及全局配置摘要未因实验改变。

本卡合格结果须同时有：隔离与身份证据、有效轨迹、每任务两组正确性及调用行为、准备/执行/监督/评分/恢复实际成本、恢复成功。连接成功但轨迹无效只能记 transport 可用；样例修复成功但路线不合合同须独立记录，不以总成功数抵消。任何 partial/error/not_run 都明示。token 未取得官方用量事件则标未测。

完成本卡并不自动进入阶段一。仍需历史排除覆盖、独立评分者安排、三组任务分离与最终阶段零出口核对。阶段一实际实现按审阅后的范围启动；提交、推送、PR、发布、用户部署、Rust 与 heartbeat 不在本卡内。

## 7. 官方依据与本机待证事项

- [配置层与进程覆盖](https://learn.chatgpt.com/docs/config-file/config-basic)：项目层受 trust 限制，CLI overrides 优先级最高；本卡不改变 trust。
- [MCP 配置](https://learn.chatgpt.com/docs/extend/mcp)：server enabled、工具白/黑名单及 instructions；是否本机生效必须实际发现验证。
- [配置字段](https://learn.chatgpt.com/docs/config-file/config-reference)：skills.config.path 为技能目录，enabled 为开关；不通过持久化 API 修改。
- [终端命令](https://learn.chatgpt.com/docs/developer-commands#codex-exec)：真实 exec 可输出 JSON 事件；没有因此证明时间戳足够计分。
- [宿主事件与指引来源](https://learn.chatgpt.com/docs/app-server)：item started/completed、commandExecution、mcpToolCall 及 instructionSources 是候选证据；本卡不启动新 app-server 或替代桌面会话，不把接收端时间冒充宿主事件时间。

这些资料于 2026-10-10 读取。版本能力、有效合并、权限隔离和事件导出尚待本卡探针实证；没有新增运行资格声明。

### 阶段零 G3-v2 可观察事件计量合同（2026-10-11，设计有限通过）

原“宿主实际接收提示”的时间语义未获证实，旧 G3 保留未确认，旧探针不回填效率分数。在正式独立集尚未运行、未产生分数的条件下，明确修订为可观察的原生事件指标：从唯一公共任务 `UserMessage item_completed` 的 outer timestamp，计至首次满足冻结任务真值和条件的 completed 工具证据事件。此指标排除前端启动与提示事件前的输入处理，不宣称端到端收益；这些耗时单独报告。完成耗时采用同一原点。

精确 schema、输入 UTF-8 字节 SHA、thread/turn/item/call ID 关联与异常规则在私人 `20261011-native-event-metric-contract-v2.json` 固定。outer timestamp 为唯一权威，payload 时间只记录差值，不混用 observer 或 CLI 无时间戳事件。缺失、重复、倒序或无法绑定的事件使计量无效；失败及超时保留，不删除。正式主指标存在无效配对时不能通过收益门。监督超时、指标超时及公平资源条件在前瞻开发校准后、正式运行前冻结，不据正式结果调整。

独立 reviewer 已有限认可这一明确操作原点的设计；具体 schema 与前瞻校准仍需复核实证，独立评分者须接受盲化关联。20% 改善、完成耗时不超过基线 1.10 倍、零 control-only 成功退步及独立集一次运行均不变。设计修订不等于阶段零通过或真实会话启动许可。

### 2026-10-11 源码基线冻结更新

旧 v0.27.0 建议只作为历史提案保留。经独立设计及官方发布身份复核，准确源码基线更新为 v0.27.1 `dd6c00eec85f834ead52ed59d01ef3a2cefe666c`，tree `c213d99f8cd2c88c38befae8f380df3e71ff1655`。已用禁用 hooks 的单命令 Git 原生 worktree add，在私人实验基目录下创建 `source-baseline` detached 工作区；核对精确 Git 根、HEAD、版本及初始 clean 状态并冻结 receipt。主 checkout HEAD 和用户修改保留，不继承当前 Rust 开发增量、不执行 setup、新 Rust 工作或移植。

v0.27.1 的查询新鲜度复验属于基线行为，不能算本轮干预收益。校准安装继续为 0.26.1，校准结果不验收新源码或未来候选。源码基线出口已物化；未做产品实现，阶段零整体仍未通过。冻结证据为私人 `20261011-source-baseline-freeze.json`。

A2 有限读取/query/runtime/capture 门独立复核通过。G3-v2 的具体采集 schema 在私人 `20261011-native-event-metric-contract-v3.json` 澄清：outer request/output 绑定自身 call ID，nested item 绑定自身 item ID；同精确 thread/turn 内按完整 argv+cwd 或 server/tool/arguments 双向一对一关联，重复、缺项、多匹配或未知均不确认，不以时间猜测。起终点及阈值不变，旧失败与分数不回填。原已复核 B2 在此登记后可单次执行；完整计量及阶段零尚未通过。
