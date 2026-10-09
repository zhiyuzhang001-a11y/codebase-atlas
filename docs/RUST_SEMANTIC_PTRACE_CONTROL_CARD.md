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

`rust_semantic_outer_control.py` 准备 controller 侧阶段协议，必须与 observer
分属不同进程；复用预算、非阻塞 pipe reader、nonconsuming pidfd wait。
主动期限耗尽或终止进入一次共享 cleanup；内层请求 cleanup 时立即创建同一个
绝对期限，并经 owned IPC 下发，不等 observer 终止后另加 10 秒。正常 live
tracer 清理期间不提前 kill；失败、清理超限及 terminal 后的 leftovers 请求
一次 owned-group 取消，发送失败仍保留错误、继续排空，不从发送推断 terminal。
所有 tracee 消费终止证据、observer waitable terminal 与三管道 EOF 都满足后才
一次 observer reap；模糊消费不重试，reap 后只读核对 group absence，不再发组
kill；最后再次检查共享时钟。tick/错误有界，原始预算/wait/pipe/owner 记录保留。
`cleanup_protocol_complete` 只描述 trusted 注入协议条件；qualified 和
outer_cleanup_complete 仍 false。8 项 fake-only 测试不调用真实 OS 接口。
**native owner 的完整启动绑定、tracee 收养排空与 bounded IPC 完整接线尚未实现**，
也尚未证明这些操作不阻塞。不能把 controller 侧协议
或 mock tracer stall 当作真实独立强监督。完整固定启动/owned adapter/预装限制
仍须完成并另审；不授权执行控制、CI 接线或 metadata/build。

外层 launcher/固定 bootstrap、源码/工具 receipt、observer/root/session 绑定、
bootstrap 初始 handle/真实 stopped handle 验证、20 秒强监督、合计 1 MiB 输出
及共享 10 秒清理的 OS 接线、
terminal/group/reap 均尚未实现/执行验证。必须连同准确代码和调用接线另审后
才运行，不能将已有 loop/cleanup 协议作为独立执行器，更不授权 metadata/build
或完整树/峰值资源门。

`rust_semantic_owned_group.py` 补充准备态 owned-group adapter，仍无启动/CLI。
只能从另审的固定 spawn 借用 sole wait owner 的 pidfd，在 observer 创建 tracee
之前的 bootstrap stopped 状态绑定：直接子进程、独立 session/group leader、
同 UID/无 tracer、正 starttime、held proc directory 的 dev/inode/CLOEXEC，
pidfd 的 dev/inode/CLOEXEC 和 fdinfo Pid；前后 stat/status 与 0.5 秒检查。
取消前重新核对仍未尝试 reap 的同一 lifetime，单次 killpg(group, SIGKILL)，
错误保留且不重试。前提是固定 observer 无并发 reaper、FD 复用或 handle 修改，
组 leader 未 reap，因此不能把它当任意 PID attach/kill API。
发送成功不证明所有成员退出；仍需逐个 terminal/drain/EOF 和 observer reap。
reap 后只用 killpg(group, 0) 探测：ESRCH 是该时刻 group absence，EPERM 不是；
PGID 可能再用，不再发组 kill。依据
[killpg](https://man7.org/linux/man-pages/man2/killpg.2.html) 与
[kill](https://man7.org/linux/man-pages/man2/kill.2.html)。记录有界；只关闭自己持有
的 proc directory，不关闭借用 pidfd，模糊 close 不重试且独立保留 errno。
全部测试 mock-only；qualified/outercleanup 仍 false。完整 owner 尚未接线，
未实际验证原生组取消/收养/强时限，不授权执行控制程序、metadata 或 build。
独立审查要求补留每次核验的 bounded packet：stat 前后原始十六进制及解析身份、
status/fdinfo 原值、FD/dev/inode、时钟及失败 errno。caller 在初始化前持有空
evidence sink，即使 constructor 失败也保留 partial packet 与独立 close 错误，
主核验错误不被清理异常覆盖；不能仅用 sent 记录代替身份绑定原证据。

`rust_semantic_cleanup_ipc.py` 准备两个 borrowed 非阻塞 pipe 的固定单次握手，
未分配管道、spawn、close 或接入执行器。outer 接收 8 字节 CLEANUP1 后，仅
写一次 DEADLIN1 + network-endian double（共 16 字节）的共享绝对 deadline；
inner 成功请求后必须收到完整 grant 才允许调用清理。内层等待 grant 的兜底
上限是最初 active deadline + 10 秒，收到 grant 后保留原数值而不是 now+10。
每次一个非阻塞 read/write，前后 mode/CLOEXEC/currentuid/dev/inode 与单调时钟
检查；最多 4096 记录，原始字节先记录再做后置检查。固定长度允许分片读取，
同次超长、错误 magic、EOF、未知 errno、过期/非有限/续期 deadline 都失败。
read EAGAIN/EINTR 只留到调用者下一 tick；write 短写/失败/模糊返回不重试，
交 outer fail-closed 取消。按 [pipe](https://man7.org/linux/man-pages/man7/pipe.7.html)
的 byte-stream/nonblocking/PIPE_BUF 语义设计，固定 8/16 字节低于原子写边界。
单 writer/peer 身份、所有未用 endpoint 关闭、SIGPIPE disposition、继承名单、
bootstrap/source receipt 与调用者 tick/deadline 接线仍须在完整执行卡另审。
不是项目提供的 IPC/path，不接受 socket/regular file/blocking FD；FD 检查仍
依赖可信 sole owner 无并发复用。11 项 fake-only 测试不构成真实握手/清理证据。
read errno 独立保留，不被后置时钟/身份错误覆盖；grant 独立时钟/数值验证失败
同样留错误记录并 latch failed，不能在下一 tick 恢复或续期。
qualified/outercleanup 仍 false，不授权控制程序、metadata 或 guest 构建执行。

`rust_semantic_subreaper.py` 准备 dedicated owned controller 的进程局部收养前置。
仅 Linux x64 指针/long/ulong8、int4，借用已验明 glibc2.39/use_errno 的 libc；
不自动 CDLL。必须在固定私有控制器创建任何子进程之前调用，不在 Codex、共享
worker 或现有应用进程设置。前后 verifier 必须实测单线程/defaultSIGCHLD/
无SA_NOCLDWAIT，另有精确固定源码 sole_waiter 策略；当前组合 verifier 未接线，
NativeWaitState 本身不提供 sole_waiter，不能凭 callback 字典当原生资格。
固定 GET37(必须0)→SET36(1)→GET37(必须1)，variadic arg2–5 明确机器宽 ulong，
不用含糊 int0；未用参数均0。全部调用及前后 verifier 共用原 active 剩余且
局部最多1.5秒；完整返回值/output/errno/调用时钟保留，失败不 prepared。
一次 SET 尝试，模糊失败不重试、不自动 unset，以免放弃未证实清完的后代；
完整 private controller 仍须负责 cleanup/退出，不能提前返回成功。
依据 [subreaper](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)、
[prctl 参数宽度](https://man7.org/linux/man-pages/man2/prctl.2.html) 和
[固定 Linux UAPI 常数](https://raw.githubusercontent.com/torvalds/linux/v6.17/include/uapi/linux/prctl.h)。
此属性不从 fork/clone 继承；必须由外层 controller 自己设置，不由 observer 代设。
只证明准备态 setup 协议；仍无 launcher、已收养 lifetime admission、pidfd wait/
终止消费与排空实现，不使用任意 wait(-1) 回收外来 child。6项mock-only测试，
qualified/outercleanup 始终false；不授权真实 prctl/控制/metadata/build 执行。

`rust_semantic_adopted_wait.py` 准备单个 **已登记且已完成原生 admission** 的
adopted tracee 回收协议。它不发现子进程、不以调用者 PID 当 ownership，也不
打开/关闭 FD；完整 controller 仍须先证明只创建 owned observer、启用 subreaper、
完成原始 parent/lifetime journal 和 held proc/pidfd 身份绑定。trusted native
verifier 每次核对同一 starttime/UID/父进程/session/group/无 tracer/FD dev-inode，
以及单线程/defaultSIGCHLD/无SA_NOCLDWAIT/solewaiter/source journal 策略。
当前只有注入 verifier 合同，**真实 admission、原始身份 packet 和完整集合排空
尚未接线**，不能凭字典、单个回收成功或空 child 快照宣称整个树已经清理。
该协议初版要求 observer held/unreaped；文末集合回收修订取代此阶段不变量。
当前 adopted drain 必须在 observer 精确 consuming terminal 成功后；每 tick 最多一次 WNOWAIT 非阻塞 terminal
观察、一次精确 P_PIDFD consuming wait 和一次 stored pidfd SIGKILL；无内部重试。
先见原始 terminal，重核身份后才消费。模糊消费/身份/时钟错误 latch，不复用权限；
signal ESRCH/EPERM/EINTR 不算退出，不重发，但下一 tick 可继续收集真实终止证据。
所有操作与最后消费后检查共用外层冻结绝对 cleanup deadline，剩余最多10秒，不
更新期限；只在原始 terminal 匹配且末次时钟通过后标 known_lifetime_drained。
前后 verifier 记录均保留；最后消费后超时仍保留 consumed=true 与原 wait 原证据，
不计成功。8项 fake-only 测试；qualified/outercleanup 始终 false。
无实际 OS 收养/信号/等待、CI 控制接线或 metadata/build 执行授权。

`rust_semantic_adopted_identity.py` 准备上一协议缺少的 read-only 身份核验层。
仅借用已有 journal admission 的 proc directory 和 pidfd，绝不以新收到 PID
发现/附着进程或打开 pidfd。核对 held FD dev/inode/currentuid/CLOEXEC，读取
同 proc FD 的 stat/status/stat，确认 starttime/ppid/controller/session/group，
以及精确 Pid/Tgid/四种 UID/TracerPid=0；借用 pidfd 的
[固定 Linux v6.17 pidfd_show_fdinfo](https://raw.githubusercontent.com/torvalds/linux/v6.17/fs/pidfs.c)
须唯一且准确（负值/重复/其他 lifetime 不接受）。
[stat 身份字段](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)
使用既有有界 parser，不把状态变化当 lifetime 变化，接受 held zombie，但拒绝
dead/tracing-stop；缺失/权限/变化不是零值或“已退出”。前后 FD 与原绝对 deadline
共同检查，局部核验最多0.5秒、每文件一次最多8193字节 read、最多16原始 packet。
新开的 sample FD 单次关闭；read 与模糊 close errno 分别保留且不重试，不关闭
借用 proc/pidfd。每个 raw sample、解析结果、FD/dev/inode、时钟及失败独立保留，
返回的是 binding，不附造 thread/wait policy 或 journal_admitted 成功。
仍需 native wait-state+固定源码 solewaiter/journal verifier 组合和完整发现/收养
集合接线，准确控制程序整体另审后才执行；6项mock-only回归，不是原生资格。
qualified/outercleanup始终false，无实际proc读取或新增CI执行入口。
一般 fdinfo 读取权限另见
[proc fdinfo](https://man7.org/linux/man-pages/man5/proc_pid_fdinfo.5.html)；
持有 zombie 的 wait/pidfd 条件另见
[pidfd_open](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)，要求默认
SIGCHLD、无SA_NOCLDWAIT且没有其他 reaper。源码 reference 不是 runner kernel
资产 receipt；实际 runner 版本和同能力域正向控制仍须完整执行卡证明。

## 下一接线前的集合回收缺口（2026-10-09，未执行）

不得用 `/proc/<controller>/task/<tid>/children` 的空快照，或两次相同快照，
作为收养集合已排空的证明。[Linux children 文档](https://man7.org/linux/man-pages/man5/proc_tid_children.5.html)
说明，未全部停止/冻结的 child 在读取期间退出可能导致其他 child 被漏列。
当前固定源码与模块尚未证明读取区间全部 child 冻结；因此此接口最多提供
候选发现，不能产生全树完成或独占 ownership 的权限。

修订前 `OuterControl`、`AdoptedWait`、`AdoptedIdentity` 的准备合同要求先排空
收养 tracee，再消费 observer terminal。若发现改用 `waitid(P_ALL, WNOWAIT)`，
已 held 的 observer terminal 可能反复被选中；不能假定内核会轮转其他 child，
也不能将 `WNOHANG` 的无事件结果当作没有 child。此组合须在原生接线前修订，
已有 mock 通过不能消除该缺口。

候选修订，仅提交独立设计复核、尚不授权实现后的执行：

- dedicated controller 在创建任何 child 前实测并固定 subreaper、单线程、
  SIGCHLD 默认/无自动回收与精确源码的唯一 waiter；只创建 owned observer，
  cleanup 开始后不再 fork，也不允许未审回调/线程/其他 reaper。
  递归整树退出推论仅适用准确固定创建源码、无逃逸或其他收养者的控制；
  尚未证明这些条件的 metadata/通用进程树不得套用此推论。
- observer terminal 仍 held 时核验 leader/session/group 原始身份，完成原本
  唯一一次 owned group 取消尝试，保存结果/errno；失败不推断全组已退出。
  然后准确 pidfd observer terminal 消费一次；模糊结果不重试，绝不在消费后
  再使用数字 PGID 发信号。仅 consuming siginfo 与 retained terminal 完全匹配
  且成功，才转换到 census；模糊消费不进入该阶段。删除旧「drained+EOF 才
  reap observer」前置，EOF 只在 census 后最终出口核对；此消费不宣称清理完成。
- observer 成功消费后，候选以 `P_ALL`、`WEXITED|WNOHANG|WNOWAIT` 加
  固定 Linux `__WALL` 覆盖所有 child 类型。每次最多一次 nonconsuming wait，
  保留 siginfo；无事件保持 pending。仅对同一个 held terminal child 冻结
  proc/pidfd、UID/真实父进程/session/group/starttime，复核来源与未消费身份，
  然后精确 `P_PIDFD` consuming wait 一次；不能消费陌生或缺准入的 child。
- `ECHILD` 只有在上述实测/source policy、observer 成功消费、无新创建、
  所有已登记 lifetime 证据核对和最终共享时钟检查同时满足时，才能作为该
  dedicated controller 没有剩余 child 的候选证据。普通 PIDFD 的 ECHILD
  仍是失败；SIG_IGN/SA_NOCLDWAIT、未知 clone、其他 waiter 或缺来源均拒绝。
  最终通过还必须 allEOF、每个 registered lifetime 的精确 consuming terminal，
  只读 group absence 与末次共享时钟检查。缺任一登记终止记 incomplete，
  不计通过；post-reap group 检查不能修复缺失生命周期，EOF 不代替 terminal。

上述依据 [waitid/子进程选择、WNOWAIT 与 Linux wait flags](https://man7.org/linux/man-pages/man2/waitpid.2.html)
和 [最近存活 ancestor subreaper 收养](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)。
原始 tracer stop/exec/RSS journal 缺失仍记 incomplete；outer 的回收不能补造
语义或资源资格。全部操作保留原共享 10 秒、8 lifetime/4096 event 与输出门。
准备模块的阶段转换落实情况见下文；准确 owner admission、native policy、错误/模糊消费
与 pending/最终无 child 的负例、bootstrap/source/tool receipts 和整套调用接线
仍须实现及独立复审。不新增控制执行、metadata/build 或公开启用授权。

独立 reviewer `rust_bridge_plan_review` 设计复核：无 P1；P2 的 EOF/observer
消费阶段及最终出口澄清已纳入。结论仅允许 plan-only 准备卡提交，不证明
原生可用或执行资格。旧模块须整体更换阶段不变量，不能仅放宽 reap_attempted；
下一步以此顺序实现 mock 负例和准确 native admission，再独立复审完整执行卡。

### 阶段转换准备实现（未原生执行）

`OuterControl` 已先在 held leader 的最后组取消尝试后消费 observer，再调用
收养排空，不能在消费后 poll observer 或取消组。返回 consuming terminal 须与
retained terminal 完全匹配；模糊消费不重试、不进入 census。精确消费的记录
在后置时钟失败时仍保留，但不继续排空/不宣称完成。trusted owner 另需提供
`no_owned_children()` 的严格布尔合同，只有 native policy-qualified `P_ALL +
__WALL` ECHILD 才可 true；None/pending、普通 PIDFD ECHILD 或 proc 空快照不算。
此 native owner **尚未实现或接线**，注入 true 只是 mock，不是内核原证据。
全部 registered lifetime 准确 drain、该 census、allEOF、只读 group absence 和
最后共享时钟同时满足才标 protocol complete，qualification/outercleanup 仍 false。

`AdoptedWait` 同时检查 observer 已精确 reaped、native wait/source journal policy，
新增 observer_consumed、group_cancel_before_reap、group_signal_retired 合同。
group_cancel_before_reap 表示唯一取消尝试先于消费，不意味着信号成功或全部退出；
错误结果仍须保存。`AdoptedIdentity` 前后检查新 observer 阶段，但只返回已有
held lifetime 身份，不能生成 journal、wait、group 权限。`ObserverWait` 的消费
方法仍保持准确 pidfd、retained siginfo 匹配和一次消费；调用者责任说明已调整。
此差异不引入新进程、原生 wait/census、权限、CLI 或 workflow 控制执行入口。
mock 负例覆盖 pending drain/缺 census、错误 census 类型/未合格 ECHILD、模糊
observer consume、EOF 延后、消费后 deadline 与旧阶段/未退役组权限拒绝；真实
源码来源、observer/controller verifier、native census/admission、bootstrap 和
IPC/FD/工具 receipt 完整接线仍需完成并整体独立复审，不能直接运行控制程序。

### TerminalCensus 准备接口（未原生执行）

新接口每 tick 只调用一次 `waitid(P_ALL, 0, WEXITED | WNOHANG |
WNOWAIT | 0x40000000)`，固定 Linux `__WALL`，不消费、不 signal、不创建
或关闭 FD。observer 已精确消费的严格阶段检查及 trusted native/source policy
前后检查必需：单线程、default SIGCHLD、无 SA_NOCLDWAIT、sole waiter、
subreaper、组取消先于消费且组信号退役、无后续 fork/其他 adopter/escape。
该 verifier 仍须从固定来源与原生测量组合实现，普通调用者 dict 不构成权限。
每次保留 siginfo/errno、前后策略 receipt 与共享时钟；原 10 秒剩余期限、
4096 packet bound 不重置，错误锁死不重试。None 只是 pending；ECHILD 仅
产生 candidate_no_children，仍需 owner 核对所有 registered terminal、EOF、
只读组 absence 和末次时钟。terminal 的 PID/UID/siginfo 是未准入发现，
不授权打开 pidfd、消费或 signal，也没有生命周期集合完成布尔值。
原生 admission/journal、完整 owner、source-policy/bootstrap/IPC 接线尚缺；
该模块与 mock 测试不构成 C0-O/C0-M 或真实语义资格，不进入原生执行入口。

### TerminalAdmission 准备接口（未原生执行）

每个实例只尝试一次、只针对一个可信 creation/lifetime journal 里的准确
PID/starttime/session/pgrp。此 journal 必须来自 reviewed source/controller，
不能接收项目字段或把相同 UID/PID 当成 journal。先由 TerminalCensus 保留
WNOWAIT terminal 且与 journal PID 一致，pending/ECHILD/未知 PID 不分配 FD。
重新检查 native/source policy 后打开 owned CLOEXEC/NOFOLLOW proc directory，
再次检查 policy 后创建 pidfd。sole waiter/default SIGCHLD/no auto-reap 与
未消费 terminal 是 numeric PID 尚未复用的必要前提，不是普通 PID 查找授权。
AdoptedIdentity 对新 held FD、准确 journal starttime、UID/真实收养父进程、
session/group/tracer=0 做前后原生核验；P_PIDFD WNOWAIT siginfo 须与发现结果
完全相同，再检查 policy 和 identity，末次共享时钟且 admission packet <=0.5秒。
此处不消费、不 signal；返回的 binding 仅允许完整 reviewed owner 后续借用，
原创建事件与 exec/RSS journal 缺失仍不能补造资格。错误保留所有已分配 FD 与
原始 receipt，显式 close 每个 owned FD 先退休再尝试一次，close 失败不重试、
不等于 process cleanup。借用期间不得 close；完整 owner 尚须实现这项约束。
native/source verifier、跨进程准确 journal receipt、完整 owner/bootstrap/IPC
接线仍未完成；本接口 mock 测试不代替 native admission、C0-O 或 metadata 门。

### CleanupPolicy 原生/源码证据组合准备（未原生执行）

Subreaper 新 readonly measure 在准确 dedicated controller 已成功 arm 后，
每次最多一个 `PR_GET_CHILD_SUBREAPER=37`，不重复 SET、不 unset。历史 prepared
不当作当前状态：GET 原始 output/result/errno 与共享时钟保留；值不是1、错误、
超过共享10秒或0.5秒 packet/4096 record 即锁失败，未知状态不能变为资格。
GET 语义依据 [Linux PR_GET_CHILD_SUBREAPER](https://man7.org/linux/man-pages/man2/PR_GET_CHILD_SUBREAPER.2const.html)。

CleanupPolicy 在每次 census/admission 前后的调用中组合独立 receipt：固定
controller source-policy 核验、当前 GET、fresh NativeWaitState 的 task identity/
单线程/default SIGCHLD/无 SA_NOCLDWAIT 原生前后测量、再次 GET 和 source 核验。
完整 composite packet 共用原 cleanup deadline 且<=0.5秒，不能给各项续预算。
准确 observer consuming 与组取消先于消费阶段同时要求。OwnedGroup 只有在
成功核验 held observer 尚未 reap 后、sole signal 尝试前才冻结 scalar phase
receipt；失败/晚调用的 cancel_attempted 不生成 chronology。CleanupPolicy
前后核对此 receipt，不能由最终 attempted/reaped bool 倒推先后；信号失败仍
保留阶段与 errno，但不是 terminal 或回收成功。错误保留 partial native
receipt，锁失败不重用。source hash 匹配只证明字节，不证明 sole waiter、无未来
fork/其他 adopter/escape：完整 source verifier 仍须来自独立审查的固定bootstrap，
不能接受项目 bool/hash 或普通调用者声明。此 verifier 和整体 bootstrap/owner/
journal/IPC 仍缺，故仅准备接口与 mock 证据，不是 nativepolicy/C0-O执行资格。

### 固定 first-stop journal 传输准备（未原生执行）

ControlJournal 只解码 dedicated observer 的固定三生命周期二进制协议：
magic/version、严格连续序号、PID、creation parent、starttime、session/pgrp。
root parent 必须是 observer，两个串行 child parent 必须是 root；拒绝未知形状、
重复 PID、零 starttime、controller/observer PID、错组/会话或截断帧。总原始保留
最多三个44字节 frame；有界 overflow 的 seen/omitted 单独计数并锁失败，不重试。
fragment 可以逐字节进入，EOF 只结束 byte transport，零/部分记录明确不完整。
返回复制的身份与 raw receipt，lookup 不接纳未知 PID，也不授权 pidfd/consume。

必须另有固定 observer first-stop 原生核验后、CONT 前的 emitter 与专用 pipe
身份/继承/peer/source 校验；这些仍未实现。正确解码甚至 transport_complete
不是 authenticated journal，不证明 exec/RSS、terminal 或树清理。本纯字节模块
不创建管道、不执行项目代码、不进入 nativecontrol/Cargo/构建入口。

### 首次 stopped sample 到 journal 的准备接线（未原生执行）

FirstStopEmitter 接受 FixedForkStops 首次 sample 的准确 before/after 原生身份：
PID/真实 creation parent/starttime/session/group、stopped state、正 RSS 与采样
时钟。整个采样至发送完成<=0.5秒，共用原20秒 active deadline；未来、倒退、
旧采样或身份变化拒绝。按固定三生命周期协议先记录本地 frame，再最多一次
调用借用的非阻塞专用 pipe writer；short write、EAGAIN/EINTR/其他错误或发送
后超时均锁失败，不重发。原 packet、raw frame、写入计数/errno/末次时钟保留。

FixedForkStops 的可选 preparation emitter 在 configure/native sample 后、CONT
前调用；未返回 exact True 或发送抛错不 CONT，已有 admission/sample/pending stop
保留供原清理路径处理。后续同一 PID 的采样不重复发送。observe_fixed 仅转交
该 emitter，不构造 native writer；省略 emitter 的旧 replay 不获得 journal 资格。
这些测试仅 fake sample/byte collector，未运行 ptrace、fork、proc 或 pipe write。
实际专用 pipe FD/peer/inheritance/source 校验与独立 outer owner 仍缺；注入普通
callback 不是权限证明，不以 source_authenticated=false 的 report 宣布 C0-O/C0-M。

### 专用 journal 借用端点准备（未原生执行）

JournalPipe 只借用 controller 已创建并冻结 device/inode/UID 的单向 Linux pipe。
每次操作前后核对同一 FIFO、当前 UID、准确读/写 access mode、O_NONBLOCK 与
CLOEXEC；同一 owner 必须单线程且不关闭/替换借用 FD。此校验不认证 writer peer，
不能把项目提供的 expected inode 当 trusted receipt。未创建/关闭 FD，不变更继承。
writer 组合 FirstStopEmitter，每个44字节 frame 本地顺序提交后只尝试一次 write；
short/error/late 发送锁失败不重发。reader 每 tick 最多一次133字节 read，真实
raw bytes 在 postcheck/时钟前保留并交给132字节总量的 ControlJournal；第133字节
overflow 留原始计数与有界前缀，锁失败。EAGAIN/EINTR 仅允许后续 tick 读，未内部
重试/续预算。partial EOF、错帧或 FD 变化拒绝；EOF 仅 byte transport，不是 exit。
每次操作<=0.5秒、最多4096记录；reader cleanup 借用 outer FIRST共享<=10秒
deadline，第一次 transition 失败亦退休并锁失败，不能重新领取宽限。
专用 pipe 创建/close与实际继承端点列表、peer/source校验、整体 raw-output 计账、
完整 outer owner 仍须接线审核；本模块仅 injected mock，不代表原生管道资格。

### 专用 journal 创建所有权准备（未原生执行）

OwnedJournalPipe 在明确 allocate 时只创建一次专用 pipe2(CLOEXEC|NONBLOCK)
双端，准确读/写 access mode、同 UID/FIFO/dev/inode、非继承属性必须一致。
所有返回的有效非负整数 FD 先登记，再检查范围/重复/标志；部分失败退休并
一次关闭全部本次 owned FD，模糊 close 留 errno、不重试，继续其余关闭。
持有已核验身份的 FD 若被替换，拒绝关闭外来对象。创建和最终 borrow 校验
处于同一 rollback 范围，共用原20秒 active deadline，创建 packet<=0.5秒。
只有创建 controller 可借用/关闭；返回 endpoint 和 expected 身份的副本可交给
JournalPipe。parent writer 关闭接口仅供后续 reviewed spawn 继承完成后调用，
不是 peer 身份或 child exit 的证明；本模块不启动、不变更继承、不读取 journal。

全部测试注入 OS/clock/flags，不实际创建/读写/关闭管道。实际继承名单及未用
endpoint 关闭、单 writer/source/peer 认证、SIGPIPE bootstrap、source/tool/cwd
receipt 和完整 outer owner 仍缺；single-threaded owner 必须防止 FD 并发替换。
qualified/source_authenticated/outer_cleanup_complete 始终 false；不授权原生控制、
metadata 或构建，不把这些准备测试当阶段出口。

### 固定控制源码同 FD 读取准备（未原生执行）

SourceFile 仅接受固定23个 controller sibling basename，不接受路径/项目文件；
完整装配补入 terminal/adopted wait 直接依赖的 observer_wait，旧22项不是完整集合。
caller 必须先冻结准确 clean PR commit、owned directory dev/inode/UID 和每文件
size/mtime/ctime/hash receipt；本接口不生成或认证这些来源，不以传入 SHA 当授权。
同 controller 从借用目录 FD 以 RDONLY/CLOEXEC/NOFOLLOW/NONBLOCK 打开一次，
核对同 UID、regular/单链接/非 group-other writable 文件；目录前后同身份。
一次最多65537字节 read，保存最多65536字节 raw 前缀、计数/省略/hash，必须与
冻结 size/hash 完全相符。前后同 FD stat 与不跟随 symlink 的目录相对 path stat
核对 inode/时间/大小，拒绝替换、变化、短读、overflow、非 UTF-8。
新 sample FD 一次关闭且尝试前退休，读错误与 close errno 分别保留，不重试；
不关闭借用目录。最后时钟包括 close，整个 packet<=0.5秒并沿用 shared20秒。

仅返回已匹配源字节，不 import/eval/exec；全部文件/时钟测试注入，没有实际
源码读取或执行。完整 manifest 来源、精确 PR/工具/cwd receipts、固定 source-policy
audit 与 bootstrap/owner 仍须接线；hash 不是 solewaiter/noescape 的证明。
qualified/source_authenticated/source_policy_audited 始终 false，不授权原生控制或
metadata/build，不代替完整执行卡的独立复核。

### 完整固定模块装配准备（未原生执行）

prepare_module_bundle 冻结23个 sibling 精确集合与拓扑加载顺序；逐文件最多64KiB、
源总量192KiB、生成 artifact 最多512KiB，UTF-8/syntax 与声明式 import 逐项检查。
未知/relative/未装配或循环 sibling import 拒绝，不自动从目录补齐。标准库名单
固定，但其 runtime/动态库来源仍须独立工具 receipt；AST 检查不是动态 import
或源码安全审核。生成定义为独立空 __path__ 的 scripts package，准确 sibling
ModuleType 先登记再载入其冻结十六进制字节；已有 scripts package 则拒绝。
此函数仅 compile/AST，不执行生成源码、不构造 native adapter、不调用控制。

生成体是需要 owned regular artifact/FD 交付的字节，明确**不是 -c 参数**；
未接入启动器，不放宽旧 ROOT_SOURCE 的100KiB -c 门。实际 artifact 创建/同FD
加载、继承、来源认证、固定 controller/observer entry、源策略审计和强监督接线
仍未实现，必须连同 bootstrap 和工具/stdlib/cwd receipt 整体另审后才原生运行。
测试只使用合成字节及当前23个准确固定源码的有界只读/AST 检查，未 eval/exec
artifact；qualification/authentication/source-policy flags 全部 false。

### 冻结 artifact 借用 FD 读取准备（未原生执行）

ArtifactFD 是独立 bootstrap 侧读取准备，不加入23个控制模块集合，不构造或
执行 bundle。caller 仍须创建 owned regular artifact，冻结准确 clean PR/source
manifest 与文件 receipt，并独占 open-file description，禁止其他线程/进程调整
offset 或替换借用 FD。读取器不 open/close，也不重置 offset；仅 SEEK_CUR 0
查询。前后同 FD 必须当前 UID、单链接 regular、所有写权限关闭、RDONLY/
NONBLOCK/CLOEXEC，dev/inode/size/mtime/ctime 匹配；初始 offset=0，后为准确大小。
一次最多512KiB+1 read；raw前缀512KiB、计数/省略/hash先保留再判定精确大小/hash/
UTF8。短读、EINTR、变化或超时均失败并锁定，不内部重试；整个 packet<=0.5秒，
沿用 shared20秒。仅返回字节，无 eval/exec/compile/launch 或借用FD关闭。
测试全部注入，无实际 artifact 创建/读取/执行。来源认证、继承后 CLOEXEC恢复、
固定入口和完整 owner/IPC/资源监督仍未接线，整体另审前不授权 native控制或
metadata/build；qualified/source_authenticated/source_policy_audited 仍 false。

### Owner 全生命周期对账接线草案（未原生执行、不是完整入口）

ControlOwner 将 OuterControl 的 request/grant、首次共享 cleanup transition、
journal drain、observer 已消费 terminal、adopted admission/drain、final census
及 group absence 接成同一状态流，不再以任意 drain=True 直接跳过生命周期账。
OuterControl 在 budget 首次转换后必须通知 owner，即使没有 inner request；
owner 转换失败仍尝试已绑定组取消和 observer 精确回收，不提前放弃 observer。

新增固定 ATTERM01 终态摘要：36字节 header（observer/source SHA/count）加最多
3个16字节 PID/starttime/Linux consuming-wait status。从 observe_fixed 的
stops.terminals 与 cleanup.terminals 合并，冲突/未准入 lifetime 拒绝；early-exit
stop 绝不升级为 terminal。仅 encode/decode 字节不是 peer/source authentication。
未知/重复 PID、starttime 不符、错误 observer/SHA、非法终态、截断/多余字节拒绝。
真实专用 transport/继承/唯一 writer 与源码身份尚未接线，普通回调不获得权限。

Owner 只在 observer consuming terminal 已确认后对账。journal 必须完整固定3项
且 EOF；摘要可为0..3个已由 observer 消费的 lifetime，其余必须经可信 census
发现、按 journal 身份 admit，再用 held AdoptedWait 真实 consuming receipt 匹配。
admission 对象在 admit 前登记，失败不重试或丢失 owned FD；显式 close 前退休，
保留关闭错误和退休后证据。ECHILD 若仍有登记 lifetime 无 terminal 则 incomplete；
全账匹配后另取 policy-qualified census，再结合输出 EOF 与只读组消失。
callback 返回 True、EOF、组信号或摘要 source SHA 都不能独立证明真实执行/清理。

本差异是整体 owner **协议部分**的集成草案：测试使用真实预算/journal/outer状态机
和 fake OS/transport/admission，不读取真实 proc、fork、wait、signal 或执行artifact。
固定 bootstrap/launcher、可信 tool/stdlib/cwd/source manifest、FD/peer继承、真实
summary 交付、native factory/source-policy、启动前独立强监督仍未完成。未新增
CLI/CI 原生入口，不修改23-module artifact 集合或公开产品开关。全部资格 false；
必须继续补齐整体入口并独立审查准确 diff 才能运行 C0-O5，不能把本差异当出口。

## 完整入口故障恢复合同修订（2026-10-09，未实现、未授权执行）

独立 reviewer `rust_bridge_plan_review` 复核了独立监督者及登记前死亡窗口。
本节取代上文三角色启动树及跨阶段统一 `no_other_adopter` 的假定；历史准备
模块和 mock 结果保留，但不能作为新拓扑的原生资格。准确完整代码及执行卡
仍须一起复审，不能只修改 verifier 的布尔值后运行。

固定故障实验的候选树为：可信外部 harness → 被测 controller →
observer/tracer → root → 两个串行 true。外部 harness 保持存活是本次实验的
明确可信前提，不追踪其他进程、不做第二 tracer、不与正常 waiter 抢消费。
harness 自身失败时实验 incomplete；harness 成功恢复不能改写为被测 controller
自身监督成功，也不放行 C0-M、metadata、编译或产品接线。各层 bootstrap 与
等待/收养阶段必须以准确源码和原生测量闭合，不能继承旧 sole-adopter 声明。

harness 必须在创建任何下层角色前拥有独立期限、初始 containment 和取消
能力。observer 离开初始 containment/进入新 session 前先交接准确 lifetime，
收到确认后才转换；新 session 核验确认后才允许 root。每个首次 stopped
lifetime 在 CONT 前完成原生身份、正 RSS、原始 journal 和 harness 恢复句柄
交接；缺失/短写/迟到确认保持停止，不放行。所有输出、journal、握手与终态
证据共用原合计 1 MiB；整个 bootstrap 和运行共用原20秒，第一次清理固定
唯一10秒绝对期限，角色故障、收养或传输重试不续期。

正常 group retirement 必须完成 held leader 最后一次成功组取消，且每个已
创建、仍可能存活的 lifetime 都有准确 harness-held 恢复句柄或 consuming
terminal 原证据；包含尚未首次 stop 的 root/child。确认退休后永久禁止旧
PGID 信号，再准确消费 observer。组取消、EXITKILL、父进程死亡后备信号、
early-exit stop、EOF 或关闭 FD 单独都不是 terminal/清理成功。

登记前死亡是独立失败分支：现有 ControlOwner 正常路径继续拒绝未知 PID、
partial journal 或缺失终态，不放宽正常 admission。完整入口可另设严格的
terminal-only recovery admission；前提是相关上层角色的准确终态/消费、
实际收养阶段、实测单线程/default SIGCHLD/无自动回收、唯一 waiter/subreaper
及独立审查的固定源码无其他孩子/逃逸。P_ALL WNOWAIT 只发现真实 terminal；
核对 held terminal、proc/starttime、当前 harness parent、UID/session/group，
绑定 pidfd 后复核 P_PIDFD 同一终态，再只消费一次。未知 PID 不获得 CONT、
运行或通用 signal 权限；未知 live/stopped child 不能套用此恢复 admission。

若缺登记使正常 coverage 门无法满足，只允许单向失败转换：最后一次准确
held-leader group cancel 成功 → ABANDON_GROUP_AUTHORITY_FOR_FAILED_RECOVERY
→ 永久禁止 PGID signal → 精确消费角色 → terminal-only 收养回收。这是放弃
组权限，不是完整 coverage 或正常 retirement 成功；发送成功不证明全部退出。
该路径仍可能不能在10秒内完成全树清理，须原样记录 cleanup incomplete；
回收部分未知 terminal 不能把原控制改判通过。取消失败不得走此发送成功分支。

recovery receipt 与正常 journal/控制结果分别保存。缺 first-stop/exec/RSS 的
lifetime 永远不计控制通过；取消后仍未终止、身份错误、模糊消费、缺证据或
超过原10秒都 incomplete，不猜 PID、不延长预算。本节只冻结失败恢复边界，
尚未解决或实现全部 bootstrap、FD/peer、组权限交接及原生 recovery 接线。

固定 SIGKILL parent-death 后备只可补充上述监督：每次 fork 前冻结创建者 PID，
child 在 setsid/ACK/fork/CONT/exec 前 SET_PDEATHSIG=9、GET=9 并核对 getppid；
fork 清除此设置，所以每条创建边都重新安装。工具必须验明非 setuid/setgid、
无 file capability、无 credential mutation；原版 true 无执行后 GET，不能把
执行前回读写成执行后实测。父线程尚未实际终止时级联可能延迟，因此这不是
十秒清理或 terminal 的保证，也不替代 harness 句柄和退出原证据。依据
[PR_SET_PDEATHSIG](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html)
与 [PR_GET_PDEATHSIG](https://man7.org/linux/man-pages/man2/PR_GET_PDEATHSIG.2const.html)。

2026-10-09 最小拓扑复核允许取消独立 guardian 层，其职责必须真实落到外部
harness。原 guardian-failure 用例改为 controller-failure/外部恢复用例；正常
controller 保持唯一 waiter/最近 subreaper，harness 只在故障阶段收养。
当前 root launch 和两个固定 true 的 parent-death 安装代码是未提交的完整入口
草稿部分：仅 mock/语法验证，不是原生资格。controller/observer 创建边、工具
无特权核验、全树 barrier/恢复以及完整入口独立审查未闭合，不能执行试验。

根进程 fork/exec 草稿现在强制专用启动闸门：创建前 pipe2，分别退休父读端/子写端；
子进程先安装 parent-death 后备，再读取完整固定 permit 和 EOF，才可 exec。
父进程持有 root pidfd 并完成恢复句柄交接确认后，只写一次 permit；失败关闭闸门，
短写不重发，碎片/EAGAIN 等待仍共用原20秒，关闭 FD 不代表终态。
父/子角色选择和子许可等待均在检查前锁定单次尝试；错误父身份或超时后
不能恢复条件重选/重读。失败清理仅退休当前 fork 副本准确继承的 FD，
不授予角色认证；关闭错误与原始拒绝分别保留，成功等待也不能再次读取许可。
`handoff` 尚无原生 harness 实现；其 Python 返回字典不能作为准确身份、句柄
交接或安全资格证据。父/子预算副本也尚未接到 harness 全树合计账本，不能用
各自1 MiB充当整体上限。此接线仍仅 mock 草稿，完整入口和原生审查门保持关闭。

本地句柄接收草稿采用预先创建并独立绑定的 Linux LP64 AF_UNIX SEQPACKET
端点与 PASSCRED；每条固定消息只接收一个 fd，所有 SCM_RIGHTS 交付的 fd
先登记为拥有，再保存 raw/ancillary/flags 并验证凭据、序号和截断标志。
失败只退休本条新收到的 fd，保留以前的恢复句柄；不重试模糊关闭。
它尚未创建/认证实际端点，也未验证收到的 fd 确为声明的 pidfd/lifetime，
不发送 ACK、不发信号、不提供启动权限；未加入已冻结的执行 bundle 或 CI。
不能把 SCM_CREDENTIALS 的 PID 当作精确进程 lifetime/source 审核，socketpair
的 SO_PEERCRED 也不能当作 fork 后发送者的新凭据。依据
[unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html) 与
[recvmsg(2)](https://man7.org/linux/man-pages/man2/recvmsg.2.html)。

接收草稿增加首个 pre-trace root 的只读绑定尝试：拥有带 NOFOLLOW/DIRECTORY/
CLOEXEC 的 proc 目录，目录相对读取 stat/status，核对 fdinfo 的唯一 Pid，
在前后两份 stat 中核对 PID/starttime/observer 父进程/session/pgrp、同账户
四项 UID 与 TracerPid=0。前后核对持有 FD 身份，四份样本保留 raw 并计入
共享输出账本，采样共用20秒且单次不超过0.5秒；失败保留恢复 pidfd，
仅退休本次拥有的 proc/sample FD，模糊关闭不重试。正常 adopted 身份与
unknown-refusal 合同不改变。这不是 ACK 或真实安全资格：procfs/端点原生
来源、发送者精确 lifetime/source、所有创建边、全树账本/恢复、独立宿主
监督仍未接线；测试只注入 OS/mock API，原生执行门保持关闭。pidfd 与普通
proc 目录的差异依据 [pidfd_open(2)](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)。

端点与传输草稿现在由 HandoffPair 在任何角色 fork 前创建并登记两个专用
socketpair 对象：SOCK_CLOEXEC/NONBLOCK、PASSCRED、两端 domain/type/UID/FD
身份核对，最终借用校验也计入0.5秒。角色选择只退休不用的继承端点，
并不认证发送者；尝试标记在父身份/时钟检查前锁定，失败后不能恢复条件
重新选择，但仍可清理当前 fork 副本准确拥有的端点。失败/模糊关闭不重试，
变化或无法核验的 FD 先 detach
Python wrapper 以消除以后析构误关外来 FD 的能力，再明确拒绝关闭外来 FD。
参见 [Python socket.detach](https://docs.python.org/3/library/socket.html#socket.socket.detach)。
HandoffSender 只进行一次 root SCM_RIGHTS 发送；短写、模糊发送或 EAGAIN
直接失败。确认消息要求凭据/序号/PID/starttime/无额外 FD/无截断，raw 先保存
再合计；错误确认中的额外 FD 先登记再一次退休，原借用 pidfd 不关闭。
匹配确认只返回 transport_ack_matched 与 harness_authenticated=False，
不返回 recovery_held。与 RootLaunchGate 的 mock 联动证明该结果仍拒绝
写 permit；本模块没有 ACK 发放路径。peer lifetime/source、原生 procfs
来源、全树预算/监督/恢复和角色 bootstrap 仍未闭合，未加入冻结 bundle，
不能执行原生 IPC、控制、metadata 或构建，不把本次 mock 传输当成资格。

2026-10-09 整体只读复核确认三个入口阻塞：创建下层角色之前的独立外部
监督及 controller/observer 身份与继承绑定；observer/root/两个 true 的全部
lifetime 恢复句柄、正常退休覆盖门及 controller-death/登记前死亡恢复；
harness 实际汇总所有 raw/IPC 的唯一1 MiB账本与终态/消费/组权限退休顺序。
传输确认、Python callback 或 fork 后独立 budget 副本均不能替代这些证明。
后续应先直接装配同一入口的正常流和 controller-death 流，再审准确源码，
而不是增加局部 helper 或把旧 CI 结果用于本草稿的原生执行批准。

### 完整拓扑的通道接线约束（只读复核，尚未实现）

现有 root 消息的 kernel sender 必须是实际 observer：`bind_root` 同时要求
root 的父进程、session/group 为该 observer。controller 转发 root pidfd 时，
真实 SCM_CREDENTIALS 属于 controller，不能改写消息 PID、放宽 parent 校验或
把“转发成功”当成 observer 创建证明。此冲突已有 mock 拒绝回归；不是原生证据。

最小接线保持固定拓扑：harness 在任何 fork 前创建专用 harness↔observer
通道，controller 仅按完整入口冻结的继承清单把发送端原样传给 observer，
并退休自身两端副本；controller 不发送 root 消息。该中间角色的身份与
端点退休代码尚不存在，不能再调用已锁定的 HandoffPair.select_role 来假造。
harness 先原生绑定 controller/observer 的准确 lifetime、来源和物理父关系，
才冻结 peer=observer；observer 的准确句柄在其 session 转换前已交接，
session/group 转换后的再次测量确认先于 root 创建。root 由 observer 直接
发送，harness 持有并核验恢复句柄后才能发确认；确认发放仍未实现。
两个 true 的句柄必须在首次 stop、任何 CONT 前同样交接，不能复用首 root
的 R/S/pre-trace 绑定条件。所有创建边、终态/回收与 raw/IPC 共用 harness
同一账本；正常 group retirement 保留全创建覆盖门。缺登记仍进入独立失败
恢复，不据 sender 凭据、close、EOF 或 ECHILD 单项宣称清理通过。
本节冻结接线顺序，不增加执行权限，完整入口源码及原生门继续未完成。

### 同一完整入口的首份代码草稿（2026-10-09，禁止原生执行）

`rust_semantic_supervisor_entry.py` 将固定 harness→controller→observer→root→
两个串行 true 写入同一源码，而不是继续新增独立 helper。controller 在准确
句柄交接的 ACK0 前不创建 observer；observer 先安装 parent-death 后备再退休
继承端点，pre-session 交接与 post-setsid stopped 确认分开。observer 直接发送
root/首次 stopped pidfd；harness 保存身份/RSS 后 ACK 才允许原 observer CONT。
正常流将 tracee consuming summary、最后组取消及权限退休、observer consuming
receipt、controller consuming receipt、全部输出 EOF、最终全集 census、只读组
消失与末次共享时钟串在一起；失败流单独处理 pre-session 未取得组权限、
controller 死亡及 terminal-only 收养，不能给 unknown live/stopped PID 权限。

本草稿的公开 `run_control` **无条件拒绝**，没有 CLI/workflow/bundle 接线。
内部未审实现不能作为获准调用的后门。源码/工具/libc/stdlib/procfs/kernel、
实际 private cwd 和完整继承 FD 清单的来源门尚未实现；协作时钟及 harness
独立角色不是已安装的强监督，真实阻塞/取消仍是执行阻断。初轮独立审查
发现创建 barrier、模糊 consume 重试、bootstrap 恢复与 FD 登记/证据保留缺口；
已修订并补纯回归，但实际角色编排、全树 raw 输出汇总与异常排空尚未获得
原生资格。消费尝试在 syscall 前永久锁定，late/异常/None 留证据且不重试；
消费不确定性分支只继续已持有 pidfd 的安全取消和输出排空，仍报 incomplete。

stdout/stderr 专用 pre-fork 管道及 child raw-native 路由已写入草稿；测试仍仅
ledger/AST/全 OS 注入，不读取真实 proc、不创建管道/fork、wait、ptrace 或发信号。
不能用这些测试或旧 clean-head CI 宣称完整入口、C0-O5、C0-M 或阶段2通过。
下一步仍是闭合来源/独立强监督/全树资源所有权，审准确整份代码与执行卡后
才考虑受控原生运行；不授权 metadata、build、guest、部署或公开启用。

### 独立 watchdog 接线草稿（2026-10-09，仍禁止原生执行）

同一入口增加一个 harness 直接创建的 watchdog，固定六个子 lifetime；它只
持有逐次原生核对的 pidfd 并进行单次取消，不 wait 控制树、不设 subreaper、
不运行项目代码。它在 controller 创建前完成 ready；controller/observer/root/
两个 true 的启动或 CONT 许可须先通过 harness 和 watchdog 的双持有 ACK。
正常清理包含 watchdog 的准确消费，不能活着就声明最终 ECHILD。失败路径
先处理控制角色和已登记 tracee 的终态，再单次取消/消费 watchdog，随后
进行 terminal-only 未登记收养 census；没有模糊消费重试。bootstrap 纳入同一
try/failure/finally；active 操作前核对 watchdog held terminal，死亡永久拒绝
后续许可但不阻断 FIRST cleanup 时钟。正常活 controller 路径核验 observer
parent=controller；失败取得 controller WNOWAIT 终态后，内核已经重亲 observer
到 harness subreaper，即使 controller 尚未消费也核验实际收养者。不把主动恢复
策略的“先消费 controller”门误当作内核收养发生时点。见
[exit(2)](https://man7.org/linux/man-pages/man2/exit.2.html)。

独立设计审查认可上述最小双进程监督方向，但源码审查仍有 P1：fork 后尚未
登记的 watchdog 失败窗口没有闭合；watchdog 早期故障与 harness 的 FIRST
期限还未实际共同冻结/通知处理；失败取消 raw 只在 watchdog 本地，退出时
可能丢失。来源、完整继承 FD/private cwd/kernel/procfs receipts 同样未闭合。
正常/失败路径纯 mock 回归不解决这些执行阻断，也不证明真实强监督成立。
此段不是原生执行卡批准，不扩大原 lifetime 上限或预算，不进入 metadata/build。

### watchdog 故障/创建窗口修订草稿（仍无原生批准）

在同一完整入口接线 watchdog 的单次 fault/取消消息；harness active check
只进行一次非阻塞读取，用严格重入门避免 check→receive→check 递归，普通
ready/ACK 保存到至多8条 pending，不伪造 ACK 或无界积压。fault 包先保存原文，
核对真实 UID/GID/PID 凭据及无FD、有限绝对期限，单向锁失败并采用
min(本地首次期限, 收到期限, 原active期限+10)。迟到仅耗尽原预算，不开新宽限；
双方各自 min 不代表已共同冻结，分别记录 local/received/adopted值；正常最终
闭合还要求真实 watch-cleanup-applied 包，未收到则未证明。通知发送失败不阻断
已持有句柄的取消；保留失败类型。watchdog 到期发送走独立有界非阻塞路径，
不走已失效的 active check，不因模糊发送重试或推迟后续取消。每条取消结果
单次传输，失败回收后有界排空；watchdog_evidence_complete 默认 false，只有
正常未取消路径完整终包可置 true。失败日志缺记录或缺最终界限继续 incomplete，
不能以空raw表示没有取消；全体收到消息仍计入同一总输出账本。

执行合同有一个窄的创建失败回收例外，不再声称“harness全程仅用pidfd取消”：
仅 harness 自身一次明确 fork 正返回，立刻保存固定 watchdog/controller PID，
且子角色未获创建下层许可；fork 前的默认SIGCHLD/无自动回收/单线程测量和完整
源码唯一waiter政策仍须核验。已创建但尚未消费的直接子（live或zombie）PID不复用，
该源创建权可单次 SIGKILL；随后 P_PID WNOWAIT 核对 PID/UID/SIGCHLD/terminal，
先永久锁 consuming 尝试，保存原返回且仅消费一次，None/异常/late不重试。
已经取得的pidfd仍立即登记拥有，不等identity成功。创建源权不接纳未知收养或
外来PID，不授予CONT/PGID/正常registration；独立失败receipt仍 qualified=false。
原FIRST10秒/共享输出/固定六child范围不变，不扩大系统权限或项目执行能力。
独立设计审查认可此窄例外方向，但准确源码/卡片仍须独立复核，不能原生执行。

失败取消流另接入单次序号、role/PID/starttime、严格 sent/error/errno schema，
全部取消尝试后仅发送一次 count/身份清单/原记录 SHA256/发送失败数终包。
收到未知身份、缺记录、重复、乱序、摘要差异或发送失败数非零均拒绝；终包
之后不接纳更多取消记录。终包只证明已收到的有界日志，不证明 EOF、准确
exit124、共同 FIRST 或全树清理。当前失败恢复仍单次强制取消 watchdog，
因此即使终包已收到，失败 evidence_complete 仍 false；不可宣称此门闭合。
丢失 handoff ACK 的 pending 身份对账与独立 failure-retire/held-terminal→
EOF→唯一 consume 次序仍须装配、独立复核；不得延长原 FIRST 或追认正常许可。

后续同入口草稿已接 pending 身份在 send 前保存，ACK 后才正常确认双持有。
pending 仅允许核对失败取消日志，不接纳外来身份或追认 CONT/创建许可；终包
至少覆盖全体 confirmed，pending 未实际收到不能伪造为已持有。失去 ACK 的
已核验身份可与真实日志对账，任何未知身份仍拒绝。
失败退休只在每个已登记控制 lifetime 有准确 terminal 后允许，单次发送前锁
attempted，watchdog 沿用最早 FIRST 并真实确认期限，单次取消→终包→exit124。
harness 分别观察 WNOWAIT 终态、正确端点 EOF，再唯一 consume并核对原终态，
末次原 deadline 检查后仅日志传输 complete 可置真；不提升全树清理/控制资格。
发送不确定走原有单次强制取消并保持日志不完整，终包、共同期限、EOF、准确
exit124或消费缺失均不完整。不延长原预算，无第二 waiter或模糊消费重试。
上述新增路径仅纯注入测试；共同 FIRST 并发故障及来源/全 FD/privatecwd/kernel/
procfs receipts 仍待证明，run_control 仍无条件关闭，不授权原生执行。

共同 FIRST 的纯注入回归现覆盖父先故障/独立 watchdog 先故障两种消息顺序、
更早期限使旧确认失效、较晚确认不得续期、缺确认保持未证明；故障的永久锁
先于收到期限的解析/过期校验，即使晚包或非法期限抛错也不恢复启动许可。
整个 watchdog_role 在完全注入 OS/transport/signal/clock 下验证初次故障14秒
期限被父12秒收紧，非法较晚退休不续期、fault/cancel/end均仅一次、准确124
失败出口。它没有创建进程/FD或发信号，不冒充共同期限的原生进程间证明。
终包身份清单额外拒绝 float/bool冒充整数 PID/starttime。全部原生来源、完整
继承 FD/privatecwd/kernel/procfs receipt与准确执行卡仍须闭合并独立审查。

完整入口的源码交付检查发现旧23-module artifact既没有supervisor，也以单文件
64KiB上限拒绝当前约78KiB入口。因此在原prepare_module_bundle旁加入固定
prepare_supervisor_bundle合同：9份真实直接/传递依赖按固定顺序装配，拒绝缺项、
多项、未知/相对/前向import；仅准确supervisor文件允许96KiB，其他仍64KiB，
总源码192KiB/产物512KiB上限不变。旧23模块合同不扩容、不隐式采用新入口。
同FD SourceFile读取也仅对准确supervisor文件名使用96KiB，保留前后文件/
目录/路径身份、单次读取/关闭与原0.5秒检查。准备与测试仅syntax/AST/mock，
从未eval产物。装配结果包含原run_control无条件关闭；9份摘要仅字节身份，
不是clean源码来源、独立审批或工具可信证明。不新增执行权限或CLI/CI接线。
此改动解决完整入口无法由现有受限通道交付的问题，但可信专用启动器仍须在
任何repo import/eval前核对准确审查 manifest，并取得Python/libc/stdlib/true、
空privatecwd/完整继承FD与kernel/procfs原生证据；当前仍未满足原生执行条件。

专用单文件rust_semantic_supervisor_bootstrap草稿不导入checkout scripts。
唯一固定输入FD为0..6：stdio、manifest、完整artifact、true、空privatecwd。
在任何源码eval之前检查-I/-S/ignore_environment、无foreign scripts、净env/
不变凭据，保留/proc/self/fd完整观测与fstat/flags身份；scandir FD保持打开采样，
闭合后只允许准确一项目录FD消失，其他FD必须保持身份与精确集合。未知FD
不关闭、不借此授信；读manifest/artifact/true均同FD前后核验、O_RDONLY/
NONBLOCK/CLOEXEC、零初始offset/准确长度/hash与末offset，失败不重读。
空owned0700目录实际fchdir，并核真实cwd的dev/inode与HOME，再核FD清单；
不是只把cwd赋给字段。操作保持首次20秒期限；尚未与后续harness共用接线，
不能另起20秒。全部OS/FD/时钟只在注入测试中调用，真实调用次数0。
公开run与脚本__main__无条件抛错，即使传入hash或fake准备成功也不能解锁。
这是完整启动器的准备段，**不是已经完整的安全入口**：独立runtime安装信任根、
动态loader/libc/libpython/stdlib/native扩展/true来源、实际loaded映射/kernel/
procfs、单线程/solewaiter、准确审批manifest，以及prep→受测字节eval→共用
deadline→正常/死亡恢复调用与各角色FD退休仍须实现和整体另审。APPROVED
manifest常量尚为空，任何调用者声明或本Python自身测量不得代替外部信任根。
不授权原生控制实验、metadata/build或生产接线。

专用bootstrap准备段到完整harness的调用链现有未执行接线草稿：原started值
交给NativeHarness构造，构造前用当前clock拒绝未来/过期origin，不能另起20秒。
runtime/来源与artifact evaluation gate仍无条件抛错；没有任何来源bool/callback/
环境变量能解锁。gate后方仅在全mock测试中替换为fake模块，按固定模块名构造，
再次核相同started/deadline，退休准确manifest/artifact/cwd FD3/4/6，保留stdio
与trueFD5，再把bootstrap记录送进同一harness aggregate trace账本，调用同一个
normal/controller-death完整入口。未实现gate时上述后方原生路径不可达。
FD退休只接受准备时持有的完整身份；身份变化不关闭替换FD，close前永久标记，
模糊失败不重试但其他准确owned copies仍退休。任一退休失败禁止创建控制进程；
未传入任意FD列表或任意模块/factory。mock替换不是任何原生运行权限。
准确runtime安装信任根、approvedmanifest、loadedmapping/kernel/procfs/
singlethread/solewaiter与实际artifact核验eval仍缺失；子角色继承/退休的最终
完整对账仍需整体审查。预算接线/mock通过不冒充完整入口安全出口或原生资格。
