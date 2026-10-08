# C0-O：临时 Linux CI 观察能力探针（限定执行复核通过）

仅验证临时 Linux runner 的 strace 是否能捕获一个受控、短命的成功 exec。
不运行 Cargo/compiler/上游源码/项目代码，不取得工具，不使用 root/sudo，不修改
用户机器或全局设置。缺 strace/ptrace 权限就记录 incomplete，不 skipped pass。
卡片、脚本和 workflow delta 独立审查前不推送或 dispatch。
独立 reviewer `rust_bridge_plan_review` 已复核下述修订，无剩余 P1；允许现有
授权 PR CI 执行一次限定探针，不授权 metadata、构建或产品接线。

使用本仓授权分支 Draft PR 的现有内部安全 workflow 增加一个 Ubuntu 24.04 job，
只对本仓 `codex/rust-public-enablement` PR head 生效。沿用现有 checkout action，
准确 head checkout、persist-credentials=false、contents:read；不添加 secrets
或写权限。记录准确 SHA 及 runner 提供的工具 hash/真实路径/root-owned 模式，
不能把第一次采集的身份当作已冻结、已合格的后续构建工具。

探针从空环境启动 strace，只有 PATH/HOME/locale，private owned cwd；strace
`-f -v -s 65536 -e trace=execve,execveat` 跟踪固定系统 Python `-I -S -c`，它只启动
`/usr/bin/true` 携带固定 ASCII token，无 shell。门内原始 trace/stdout/stderr 全保留，
超限保留有界前缀和遗漏标记；
必须观察完整 argv 的成功 exec。命令/身份不来自项目输入，不安装或运行包代码。
外层 20 秒 wall deadline、输出 1 MiB 检测/有界读取门、10 秒所属组清理/父进程
回收；CI job 自身 2 分钟上限。记录失败，always 保存原始 JSON artifact。

独立初审的 P1 最终退出限额竞态及 P2 失败日志丢失已修订，须复核后才推送：
清理后对退出前主动时间及输出总字节再次检查，再判断 observed；全部失败出口
保存总计 ≤1 MiB 有界前缀及原始大小/遗漏/缺失标记，超限保持 incomplete。
清理失败保留 private 临时目录，不在可能仍有所属子进程时删除；父控制器和
被观察 Python 都使用 `-I -S`，避免全局 site/.pth 执行。

出口仅 short-lived-control-observed，qualified=false。没有 execveat 正向控制、
完整 Cargo argv 解析、RSS 监督、禁网或 native-host 安全证明，不称 C0-M/C1 或
Linux 产品资格通过。这些缺口仍必须在真正构建/查询卡中解决。本探针不改变
原 8 小时原型预算、五平台门、stable 0.27.0 的关闭开关或两项暂停 heartbeat。

## 已取得 v1 原始证据

准确 head `1f62b8f6f3415a47e26bda0f4435dc77bf2801d1`，内部安全 run
`37567136266` 的 `semantic-observer-probe` 完成成功。2298 字节 JSON artifact
SHA-256 `dcb17c5496dc939f41e930462e82f1c82b890532a7645de716d53e3bfdb67421`。
原始 trace 含 Python 与固定 true 的两次完整成功 execve，无遗漏；主动时间
0.135233259 秒，trace 554 字节，stdout/stderr 均空，所属组消失/parent reaped。
`qualified=false` 保持；工具 hash 是该 runner 的实测身份，不是跨 runner 的固定身份。
没有 execveat、RSS、禁网、完整一般 argv 解析或编译能力证明。

## v2 限定增补（原始证据独立复核通过）

独立 reviewer `rust_bridge_plan_review` 已重算 v1 artifact 并复核 v2 卡片、
脚本和回归，无 P1/P2；允许现有授权 PR CI 一次限定增补。现已取得结果如下。

保持相同 runner、私有 cwd/空环境、可信工具身份读取、20 秒主动期限、1 MiB
总输出和 10 秒清理。仅增加两个 Atlas 固定受控 true 调用，不执行项目/编译器：
一个普通 execve；一个 Python `os.fork` 后 `os.execve(fd, argv, env)`，fd 只来自
固定 `/usr/bin/true` 的只读 `O_CLOEXEC` open。必须实测成功 execveat、空路径和
AT_EMPTY_PATH；libc fallback、权限不支持、失败/未知或交错格式均 incomplete。
不以 Python 支持 fd exec 的文档代替 syscall 正向证据。

两组固定 ASCII argv 均含空字符串、引号/反斜线、换行/tab/CR，以及 4096 字符
长参数和尾标记。断言同一行的完整参数数组与成功返回，禁止猜测截断；仅为
精确受控参数渲染器，不宣称它能解析一般 Cargo argv、非 ASCII 或所有交错调用。
只继承已清空的三键环境，child 退出需由父 waitpid 证实；controller 仍清理整组。
源码、脚本及执行卡已独立复核，普通 push 后现有 PR CI 运行一次，不手动重复
dispatch。出口最多 short-lived-controls-observed，始终 qualified=false。
实际 Cargo metadata 的 roots/features/source receipt、完整 exec/cwd 解析与 RSS
等卡项仍待冻结；此增补不授权 metadata 或构建，不重置八小时主动工作总预算。

准确 head `3a47d188f0d9f49668c045b9b4434c8c5cf965f3`、run `37646034861`。
JSON 28815 字节，SHA-256
`f3f3f33d507a0e70ba6356b0da67666e3adca704ff7ab76e1a4ea3665d01c924`。
独立 reviewer 重算身份并逐项核对三个固定 true 控制各成功一次；空参数、转义、
长参数/尾标记、实际 execveat 空路径/AT_EMPTY_PATH 完整。主动 0.158510949 秒，
trace 18206 字节，stdout/stderr 空，无遗漏，所属组消失/parent reaped。
仅 C0-O 窄出口通过，qualified=false；不批准 metadata/build 或平台语义资格。

## v3 受控 RSS 正向能力增补（独立执行卡复核通过，实测待完成）

保持 v2 三个 exec 控制与工具、runner、空环境、私有 cwd、20 秒主动期限、
1 MiB 输出、10 秒清理和 2 分钟 job 上限。不引入 Cargo/compiler/项目源码、
安装、网络或 shell。执行前独立复核此卡、脚本、测试及现有 workflow。

只增加 Atlas 固定 Python 内存控制：父进程逐页触碰 16 MiB，然后 fork 子进程；
子进程再逐页触碰自身 16 MiB，通过固定私有 pipe 发出就绪字节，保持 3 秒退出。
父进程收到就绪后输出唯一固定 token 和两 PID，并 waitpid 确认子进程正常退出。
不接受项目提供的代码/参数/PID。controller 在同一个已启动的 strace session 中，
对 tracer/父/子三个明确 PID 读取内核 stat，检查 PID/starttime/非死亡状态、
所属 session/group、controller→tracer→父→子 ancestry；缺失或异常 incomplete。

使用准确 checkout 内已审查的 sibling `rust_semantic_linux_resources.py`：从
固定脚本旁路径一次有界读取最多 64 KiB，记录 SHA-256/大小，直接 compile/exec
该测量字节，不使用 sys.path、site、package search 或 pyc。不把这个实验加载方式
当作用户项目插件执行或最终产品加载路线。工具和模块身份都写入 source_sha receipt。

只对三个已准入 PID/starttime 进行三次 smaps_rollup RSS+hugetlb 采样，保存原始
读数、开始/结束时间和 admission。父/子各至少 16 MiB，总和最多 128 MiB，
每次采样最多 0.5 秒、相邻间隔最多 0.5 秒（目标间隔 0.1 秒）；缺失、拒绝、
PID 重用、组变化、低读数或限额失败均 incomplete，已有采样及输出保留。
三次取得后停止采样，仍等待固定控制退出并清理整组。旧 exec 控制仍必须全通过。

出口仅受控 exec 与受控已准入 RSS 能力，qualified=false。这不是完整 tree 发现、
短命进程/逃逸覆盖、并发峰值或连续资源限额证明；不批准 metadata/build/C1，
更不算五平台资格。独立 reviewer `rust_bridge_plan_review` 已复核当前脚本、卡片、
测试和现有 workflow，无 P1/P2，独立 10 项 pure/mock 回归通过，允许普通 push 后
只使用现有 PR CI 一次触发，不重复 dispatch、不借旧 artifact 认定新 SHA 通过。
20 秒/采样时长异常可事后拒绝；不证明阻塞内核读取的硬抢占或连续 enforcement。
