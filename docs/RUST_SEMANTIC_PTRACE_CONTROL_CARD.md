# C0-O5：短命进程停止点控制卡

2026-10-08。仅为下一步实现/独立审查草案，未执行、未取得资格。
基线 `37c7af3e9e7fafb555f27be671fcec2ec2068b30`；沿用八小时主动工作账本。
本卡不授权 Cargo metadata、compiler/build.rs/proc-macro、guest 或用户项目执行，
不代替尚未闭合的 C0-M 执行卡，不改变五平台与原冻结查询/资源门。

## 要解决的具体缺口

v4 的三个短命 true 有完整 exec/退出日志，却没有 live PID/starttime/RSS。
提高普通轮询频率不能保证补齐。候选采用 Linux 内核 ptrace 停止点：
受控 root 在初始停止后安装 TRACEFORK、TRACEVFORK、TRACECLONE、TRACEEXEC、
TRACEEXIT、TRACESYSGOOD、EXITKILL；新子进程首次恢复前必须完成身份准入。
不以 strace 文本通知到达时该 PID 仍活着为前提，也不允许两个 tracer 同时附着。

依据：[ptrace(2)](https://man7.org/linux/man-pages/man2/ptrace.2.html)：
fork/vfork/clone 选项自动追踪新子进程；创建事件在父进程报告，子进程有独立
初始 stop，不能假定二者通知顺序。TRACEEXIT 是早期退出 stop，不是最终回收。
EXITKILL 是 tracer 死亡时杀死 tracee 的手段，不是已证明全部清理成功。

## 首轮只允许的控制

仅 Ubuntu 24.04 Linux x86_64 的私有临时目录，现有同仓 PR 内部 workflow。
无 sudo、capability、系统 ptrace/profile/sysctl、cgroup、网络或全局配置变更；
只追踪本控制器自己创建的同账户进程，不 attach 其他进程。权限/API 不可用
记录 incomplete，禁止换成静默漏采样。不引入新依赖、下载或用户代码。

外层控制器在启动前安装 20 秒绝对期限、全部输出及事件 trace 合计 1 MiB、
所属组 cleanup 10 秒。拥有关系为 controller → observer/tracer → tracee root
及其固定 fork children；controller 负责监督 observer/tracees 的共享期限和清理。
被追踪 root 为逐文件/同 FD 核验的系统 Python，以 `-I -S -c <固定控制代码>`
执行，env 仅 PATH、HOME、LC_ALL，cwd 为新私有空目录。固定代码只分别 fork
并 exec 两个已核验 true 控制：路径 execve、固定只读 FD 的 execveat；每个 child
立即退出，root wait 后退出。禁止传入任意命令、源代码、环境或路径选项。
root、工具、控制代码、观察器源码及原始证据 hashes 必须保存。
第三个原 v4 长参数控制暂不在本卡新增执行，旧证据保留，不声称本卡覆盖它。

root 在自身 TRACEME + SIGSTOP 后才允许进入固定代码；observer 在初始 stop
设置上述全部选项并核对成功，才首次恢复。实现须明确这段 bootstrap 的可信
源码及启动 argv，不能把初始 Python bootstrap 未受追踪区间说成已被完整观察。
任何创建事件先登记有界 pending child；收到该 child 初始 stop 后以 proc 目录
FD 读取 PID/starttime/ppid/session/group 并前后核对，再记录正 RSS，才恢复。
child 通知早于 parent 创建通知时只暂存，不猜 parent、不恢复；缺配对则失败。
预期初始/新子 SIGSTOP 与 ptrace event-stop 恢复时 signal=0；正常真实
signal-delivery stop（尤其 SIGCHLD）须区分并按原信号转发，不静默吞掉。
未知信号、额外 SIGSTOP/SIGTRAP、无法区分的 stop 均失败清理。
exec stop、exit stop 再核对同一身份并采样；最终 terminal wait 才标为 reaped。
拒绝 PID 重用、未知 PID、非预期事件，最多
8 个 lifetime PID、4096 个事件，超界失败并执行所属清理。不能将 proc 的 traced
parent 字段误认为原 physical parent；须保存 tracer 与创建事件两个来源并校验。
本卡仅接受固定源码的 fork 创建形状及相应事件/身份，其他创建事件拒绝。
TRACECLONE/FORK event 本身不提供通用 clone flags，不声称已识别或拒绝任意
线程/namespace/shared-state 创建；此能力必须以后在 syscall 层证明。
失败/超时不得 detach 后放行。发送 SIGKILL 后继续有界消费停止点与 terminal
通知，按实际 ptrace 状态完成必要的退出推进；共享 10 秒内所有已登记 lifetime
均有 terminal 证据、所属组消失且 observer/root parent reap 才算清理成功。
只发送 kill、依赖 EXITKILL、收到 early-exit stop 或 ESRCH 不算已回收；缺任一
证据保留私有原始文件并记录 cleanup incomplete。

首次只证明 kernel stop 能为两个短命 true 留出准入/正 RSS 的窗口。
保留 creation/child-stop/exec/exit-stop/terminal 顺序、每次 ptrace 请求和 errno、
proc before/after、RSS 开始/结束时间、父子源与 cleanup 原始证据。
不得仅记录最终 passed 布尔值。已有 sampler 对 t/T 停止状态的实际兼容必须测试；
早期 exit stop 无 smaps、零 RSS 或身份失败均 incomplete，不补造读数。

## 不宣称解决的门

创建/exec/退出 stop 采样不证明两次 stop 间的内存峰值，也不证明完整 syscall
argv/env/cwd/FD 来源或禁止网络/逃逸。只开 TRACEEXEC 无法捕获失败 exec 的参数，
所以不得用本控制直接运行 metadata。真实监督后续需要 syscall-entry/exit 解码、
正确信号转发/取消、每个 TID 生命周期、拒绝逃逸及内存机制的独立实现与审查。
当前首轮未知信号/多线程一律失败清理，不能靠丢弃信号改变控制程序语义后过门。

[wait4(2)](https://man7.org/linux/man-pages/man2/wait4.2.html) 返回被等待 child 的
资源使用；[getrusage(2)](https://man7.org/linux/man-pages/man2/getrusage.2.html)
明确 Linux ru_maxrss 的 KiB 单位，并说明 RUSAGE_CHILDREN 是最大 child 而非
整个进程树峰值。因此不能用 controller 的 RUSAGE_CHILDREN 替代完整树 RSS；
本卡不使用 wait4 maxrss 作门，不宣称逐 lifetime 峰值之和已经可靠取得。
hugetlb、线程/shared mm、exec 前后峰值、controller/observer 自身费用和初始化
阶段仍需单独覆盖；现有 audit_coverage 通过也只能代表指定样本覆盖，不是资源资格。

## 审查与执行顺序

先独立复核本卡；允许准备实现不代表具体实现获执行资格。代码与调用接线完成后
再独立检查准确 diff、固定代码/argv/env、超时/清理与负例，才可正常 commit/push
触发现有内部 CI 一次。不得先提交接线再补审查。控制执行若失败，保存 raw artifact，
只修复有证据的常规问题；不是 guest 原型修订，不重置任何主动预算。
需要观察器全门及冻结源码/root/config/tool receipts 后才可申请 C0-M 独立执行复审。

## 准备实现与独立复核（未执行）

新增 `scripts/rust_semantic_ptrace.py`：显式 Linux x64 ABI、lazy native adapter、
same-proc-FD stopped identity/RSS、ptrace 请求/errno、SIGCHLD siginfo 与有界
固定 fork stop loop。创建通知次序可交错，新 child 准入/正 RSS 前不恢复；
拒绝未知 stop/event、重用 PID、缺失 exec/early-exit/terminal、非串行固定
fork 形状与非有限/超过剩余 20 秒的 deadline。没有 spawn、attach、detach、
CLI 或 workflow 执行入口。Native adapter 只在明确构造时装载 libc；纯测试
从不读取真实 proc 或发出 ptrace。Linux 信号/wait 编码不借用 macOS 的常量。

独立 reviewer `rust_bridge_plan_review` 最新复核无剩余 P1/P2；独立 9 项
ptrace 和 9 项资源 pure/mock 测试通过，主 agent 相关 49 项回归通过。
复核只允许提交准备模块；没有批准实际控制执行或 CI 执行接线。
曾发现并修复 NaN/inf deadline 缺口及测试归属错误，失败不作通过证据。

后续准备差异增加 `FixedForkCleanup` 的纯注入式异常排空协议：保留失败后仍
pending 的停止点；已恢复任务只允许通过 outer owner 预先绑定的 lifetime handle
终止，禁止裸 PID kill。每个停止任务（包括 cleanup 新发现的 child）恢复前必须
先 bind_stopped 核验绑定 lifetime handle、kill_bound 实际发送 SIGKILL 成功，
之后 CONT0 仅推进退出；绑定或发送失败保持停止，交外层失败清理。不能依赖
CONT(SIGKILL)：非 signal-delivery stop 可忽略该参数，独立审查发现的 P1 已按
上述次序修复并加负例。支持 child 初始 stop/parent 创建通知两种次序；排空
SIGKILL/正常 terminal 与 ECHILD，ESRCH 不作 terminal 或回收证据；拒绝 PID
重用、observer 身份、非法 wait 编码及超出共享剩余 10 秒/PID/event 预算。
即使 known lifetimes 都有 terminal，仍显式 `outer_cleanup_complete=false`；
group absence 与外层 observer reap 不由该协议推断。没有 native kill/handle
接线，测试只使用 fake operations。NativeStops 资源模块改为显式注入，便于后续
将逐文件核验过的源码字节嵌入 `-I -S` observer，不依赖项目 sys.path/site。
主 agent 59 项相关 pure/mock 回归通过；本差异独立复核结论另记 STATE。

后续 `NativeStops` 准备实现增加 Linux pidfd capability 检查与绑定/发送适配器。
依据 [pidfd_open(2)](https://man7.org/linux/man-pages/man2/pidfd_open.2.html) 和
[pidfd_send_signal(2)](https://man7.org/linux/man-pages/man2/pidfd_send_signal.2.html)，
通过 handle 发送 SIGKILL，不做裸 PID kill；能力缺失不回退。
只有 observer 独占 wait 流中已消费、尚未恢复/terminal 的 stop 可以绑定。
同一个 proc 目录 FD 前后 stat 一致，核对 starttime/session/group、status 的
TracerPid/Tgid/Pid/四个 Uid、pidfd fdinfo 的 Pid 及 CLOEXEC；超过 0.5 秒失败。
首次恢复前 configure 安装 ptrace options 后即绑定 handle，再采样 RSS。
已有 handle 不重绑其他 starttime；kill 失败留 errno，不补造 terminal。
测试通过 mock 注入所有 OS API（含 Windows 缺失的 Linux 常量），共 67 项
相关 pure/mock 测试通过；close_handles 在每次关闭前移除 FD 所有权，失败保留
errno 并继续关闭其余，最后报告错误；禁止重试不确定 FD 以免误关复用对象。
尚未调用
真实 pidfd/proc/ptrace，不证明该 Linux adapter 在 runner 上可用。
仍须明确独占 wait、SIGCHLD disposition、单线程 bootstrap 和外层强取消，
pidfd 被关闭也不是回收证据。本差异独立复核结论记录在 STATE。

启动源码准备模块 `rust_semantic_control_code.py` 只生成定义，不调用控制程序。
固定 `ROOT_SOURCE` 定死 TRACEME/SIGSTOP 后两个串行 fork、路径 true 和只读
FD 的 execveat，明确 ctypes ABI，精确 wait/exit 状态和失败退出码。
`prepare_sources` 接收控制器核验过的 owned sibling 字节，逐份 UTF-8/64 KiB/
语法检查，hex 嵌入独立 ModuleType；输出资源/ptrace/root/assembled 哈希，
assembled `-c` 源码另限 100 KiB。不使用 site/sys.path/pyc 二次查找源码。
输入必须是可信控制器源码，绝非项目源码或任意用户命令；外层准确 clean SHA
与逐文件核验尚需接通，函数返回哈希不是来源已经获验证的证明。
主 agent 70 项相关 pure/mock/AST 测试通过，未 eval/exec 生成源码、fork 或发信号。
独立 reviewer 3 项源码/AST 测试通过，无 P1/P2，仅允许提交准备源码；不授权
运行。精确 libc/工具/源码身份与剩余 OS 接线仍须完整审查，不把生成源码当作
观察器执行资格。生成器曾遗漏对嵌入字节的语法检查，负例失败后已修复。

`rust_semantic_control_budget.py` 新增纯记账准备模块：一个共享 20 秒主动期限，
首次 cleanup 创建不可续期的 10 秒期限；拒绝非有限/倒退时钟，失败后不能恢复
active。stdout/stderr/trace 原始字节共用 1 MiB 留存上限，每次读块须不超过
64 KiB；超限先记录当前块的计数/hash/有界前缀，再失败。清理排空的输出仍计入
同一上限，省略字节明确记录，EOF 不当作进程终止/回收证据。纯测试不读取管道、
启动进程或发信号。此模块没有 OS 级强监督：外层仍必须在 tracer 阻塞时独立
执行 deadline、持续消费受限读块并证明 terminal/group/reap；不能把记账函数
当作已生效的原生限制。主 agent 74 项相关 pure/mock/AST 回归通过。
独立 reviewer 4 项纯测试通过、无 P1/P2，仅允许提交准备模块；实际接线须
另审，外层成功完成时也须再次检查主动 deadline，才进入同一清理阶段。
未授权控制执行或 CI 执行接线。

`rust_semantic_control_pipes.py` 是非阻塞排空的准备适配器，无执行入口：只借用
外层拥有且保持不关闭/复用的三个独立 pipe read FD，不创建/关闭 FD、不启动或
发信号。逐 FD 核对 FIFO 类型、当前 uid、dev/inode、只读/O_NONBLOCK/CLOEXEC；
这不独立证明管道来源，仍须外层的创建 receipt 和单线程 FD 所有权。
每 tick 每 stream 最多一次 64 KiB read，前后核对身份与共享阶段期限；先记录
实际读出的字节，后检查 post-read 失败，以保留异常证据。EAGAIN/EINTR 只在
下一 tick 重试，不内部循环或重置 deadline；验证/读错误锁住 active，清理仍可
排空。EOF 不当作回收，错误摘要限 128 条，tick 总数限 65536。全部 OS 操作在
测试中注入，主 agent 81 项相关 pure/mock/AST 测试通过；尚未读真实控制管道。
低层读长与 EOF 依据 [Python os.read 文档](https://docs.python.org/3/library/os.html#os.read)，
非阻塞标志依据 [Python blocking 文档](https://docs.python.org/3/library/os.html#os.get_blocking)。
Python 可能自动重试 EINTR，故不能用此调用替代独立强监督；外层和 tracer 的
完整接线仍须另审，未授权实际控制执行或 CI 执行接线。
独立 reviewer 7 项全 OS 注入测试通过，无 P1/P2，允许提交准备模块；不证明
管道来源、强监督或进程清理，不授权 metadata/build。

`rust_semantic_observer_wait.py` 新增 outer observer 的终止/回收准备适配器：
只借用外层准确 spawn 与 lifetime binding receipt 的 pidfd；不创建/关闭句柄、
启动、发信号或使用数字 PID wait。Linux `waitid(P_PIDFD, WEXITED|WNOHANG|
WNOWAIT)` 先核对 PID/uid/SIGCHLD/终止类型与状态，保留 waitable terminal；
明确 `reap()` 只允许已观察终止后的一次 consuming FD wait，结果必须一致。
ECHILD/ESRCH、空/不一致结果不算回收；模糊 consuming call 后不重试。
依据 [Linux waitid 文档](https://man7.org/linux/man-pages/man2/waitpid.2.html)，
WNOWAIT 保留待回收状态，P_PIDFD 选择同一 lifetime 且 kernel 检查 wait 归属。
它不证明 FD 的创建来源、外层独占 wait/FD 所有权、observer 会话身份或组消失，
调用者须先完成 tracer/tracee 排空；report 始终不宣布整体清理或资格通过。
主 agent 87 项相关 pure/mock/AST 测试通过，全 OS 注入，未真实 wait/reap。
独立 reviewer 6 项全 OS 注入测试通过，无 P1/P2，仅允许提交准备模块；
借用 FD 来源/独占所有权/tracer drain 顺序须完整 outer owner 与接线另审。
未授权 native 控制、CI 执行接线或 metadata/build。

`rust_semantic_control_observer.py` 继续准备 inner observer 编排：注入已拥有的
stop/cleanup operations，正常完成与失败都只进入一次 outer 提供的共享 cleanup
期限；最后一次 wait 后再次核对主动时钟，非有限/倒退/超时不算控制成功。
首次 stop 前失败使用 caller 持有的 root bootstrap pidfd 发送权，发送失败保留
错误后仍排空 wait，不以失败发送推断已退出。无 native 构造/spawn/CLI；关闭
句柄失败与部分 stop/terminal/native 记录保留，资格与 outer cleanup 始终 false。
这只是内层协议组合，未提供独立强取消、工具来源、FD 生命周期、组消失或
outer reap 保证。所有测试使用注入 fake，未实际运行观察器。独立复核另记 STATE。
独立初审发现清理异常会丢弃临时 cleanup 对象；已改为保留实例并导出 partial
known/parents/terminals/events/errors/stopped 原始状态，不把该状态标为清理通过。
负例先记录新 child 与 terminal 再抛错，确认失败证据仍保留。

`rust_semantic_outer_pipes.py` 准备 outer 独占 FD owner：只在显式 allocate 时
用 [pipe2 原子 CLOEXEC](https://docs.python.org/3/library/os.html#os.pipe2) 创建
三个匿名管道，读端 nonblocking，写端保留 blocking；不接收外来 inherited FD。
每份 created-pair 先记录，核对同账户 FIFO/dev/inode/flags 后才标 validated；
部分失败保留 allocation/close 原因并回收本次创建的 FD，borrow 返回副本。
调用者仍须单线程保持 FD 所有权，并且只能向后续独立审查的固定 observer
继承写端。父写端关闭不证明 child 已退出；不启动进程、不发信号、不提供 CLI。
close 前检查对象身份，替换则拒绝关闭；关闭前先去除所有权，记录错误、继续
其他 owned FD，不重试不确定关闭，以免关闭后来复用的对象，依据
[Linux close 文档](https://man7.org/linux/man-pages/man2/close.2.html)。
所有测试 OS 注入，尚未真正创建/继承管道；qualified/outercleanup 始终 false。
这补充管道创建来源/关闭协议，不是完整 launcher、强监督或进程清理资格。
独立初审发现最终 borrow 核验不在 allocation rollback 内；已将最终读/写
交接核验纳入同一 try，分别注入第 7/10 次 fstat 失败，确认六个 owned FD
全部关闭、ready=false、不能重新分配。复核结论另记 STATE。

`rust_semantic_root_launch.py` 准备固定 root 启动器，无 CLI 或 CI 接线：
准确 ROOT_SOURCE 字节生成 `/usr/bin/python3.12 -I -S -c` 固定入口与哈希，
语法检查不执行；只能由已装好 outer 强限制且完成 Python/libc/stdlib/true/
源码/cwd 逐项 receipt 的可信 observer 调用。一个新 owned fork、一个 bootstrap
pidfd，失败保留 root/已创建 FD，交 outer 清理；不使用 Popen、裸 PID kill 或
隐式 wait/reap，不重试 launch。源字节哈希不是来源验证，Python 路径也不是
工具已经验明的证明。当前没有实际来源校验或 supervisor，不能直接运行。
依据 [pidfd_open 的 fork 条件](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)，
owned child 到 pidfd_open 间必须不忽略 SIGCHLD、不设 SA_NOCLDWAIT、无其他
waiter 回收；仅 signal(SIG_DFL) 不足以证明全部条件。因此启动前强制要求
可信 verifier 实测 native disposition/flags 与单线程、独占 wait 归属，
返回准确 observer/threads/default/no-auto-reap/sole-waiter receipt；缺失或
非法返回值在 fork 前失败。该实测 verifier **尚未实现**，测试中注入的声明
不能替代原生证明；此限制是未闭合的执行前置条件，不是已通过安全门。
全部 OS/signal APIs 在 6 项测试中 mock，相关 105 项 pure/mock/AST 回归通过；
没有真正 fork/exec/pidfd/signal。独立复核结论另记 STATE。成功返回 PID/FD
也仍 qualified=false、outercleanup=false，不授权 metadata/build。

`rust_semantic_wait_state.py` 补充一次性只读 native 状态测量的准备实现：
仅 Linux x64、已由 caller 验明的 glibc 2.39；不自动装载 libc。按固定上游
[Linux sigaction 布局](https://raw.githubusercontent.com/bminor/glibc/glibc-2.39/sysdeps/unix/sysv/linux/bits/sigaction.h)
和 [Linux sigset_t 覆盖定义](https://raw.githubusercontent.com/bminor/glibc/glibc-2.39/sysdeps/unix/sysv/linux/bits/types/__sigset_t.h)
核对指针/ulong8、struct152、offset8/136/144，明确函数参数/返回类型；
不使用 generic sigset_t 的单 ulong 定义。libc 版本字符串不是完整资产身份，
仍需外层 receipt。只用 sigaction(17, NULL, oldact) 读取，不更改信号配置。
同一 own-pid task 目录 FD 前后核对 currentuid/目录/dev/inode/CLOEXEC，
两次 scandir 最多取两项（第二线程即失败），并核对默认 SIGCHLD/无
SA_NOCLDWAIT。两次观测与闭合耗时最多 0.5 秒；这仍是 cooperative 检查，
必须已有独立强监督。失败保留部分原始值和 errno；单次 close 不重试，close
异常单独保存且不抹去原始失败。无 spawn/wait/信号修改/CLI/CI 接线。
依据 [proc task](https://man7.org/linux/man-pages/man5/proc_pid_task.5.html) 与
[sigaction](https://man7.org/linux/man-pages/man2/sigaction.2.html) 读取指定状态，
不据两次快照推断后续永不创建线程或不存在其他 reaping callback；返回值
刻意缺少 sole_waiter，因此 **不能直接满足 RootLauncher 的完整合同**。
仍需准确可信源码/回调/线程策略和 outer 生命周期接线独立审查，才可运行。
本差异全部 libc/OS 操作只在 mock 测试中调用，未读取真实 proc/sigaction。
独立复核结论另记 STATE；qualification 与 outercleanup 始终 false。

外层 launcher/固定 bootstrap、源码/工具 receipt、observer/root/session 绑定、
bootstrap 初始 handle/真实 stopped handle 验证、20 秒强监督、合计 1 MiB 输出
及共享 10 秒清理的 OS 接线、
terminal/group/reap 均尚未实现/执行验证。必须连同准确代码和调用接线另审后
才运行，不能将已有 loop/cleanup 协议作为独立执行器，更不授权 metadata/build
或完整树/峰值资源门。
