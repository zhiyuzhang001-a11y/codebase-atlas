# Rust 官方工具接入：B0 研究结果

日期：2026-10-07。研究基线：`bbc1a8ebd7dde0fed646d3cc2d543bfd1ef862e3`。
属于[专项计划](RUST_OFFICIAL_TOOL_BRIDGE_PLAN.md) B0/B1，不是 I0 资格报告。
用户已批准只读研究；未运行官方工具、沙箱实验或 CI，未改变权限或部署配置。
验收 heartbeat 保持暂停，stable 0.27.0 Rust 保持关闭。

## 问题与官方调用路径

卡点不是 Rust 定义/引用查询算法，而是：允许未经修改的官方工具正常启动
合法子进程，同时在执行前拒绝项目配置诱导的任意执行，且保持五平台合同。

固定 Rust 源码 `88d9e12ae178fab0fb5cc050a94da85685d449ea` 中，RA 的
[cfg 探测](https://raw.githubusercontent.com/rust-lang/rust/88d9e12ae178fab0fb5cc050a94da85685d449ea/src/tools/rust-analyzer/crates/project-model/src/toolchain_info/rustc_cfg.rs)
启动 Cargo，失败时回退到直接 rustc；它们并不调用现有自有 broker 协议。
固定 Cargo 源码 `797e8a9bca276c1c9f9f738d2a20f484fa4eea9d` 的
[目标探测](https://raw.githubusercontent.com/rust-lang/cargo/797e8a9bca276c1c9f9f738d2a20f484fa4eea9d/src/cargo/core/compiler/build_context/target_info.rs)
构造带标准输入入口、多项 print 参数、环境配置和可能继承的 jobserver 的
原生 rustc 请求；[缓存实现](https://raw.githubusercontent.com/rust-lang/cargo/797e8a9bca276c1c9f9f738d2a20f484fa4eea9d/src/cargo/util/rustc.rs)
在未命中时实际执行。允许 executable 路径本身不足以绑定这些请求。

## 三条机制路线

| 路线 | 官方 API 已证明的能力 | 未证明或冲突 | 当前资格 |
| --- | --- | --- | --- |
| Linux seccomp 通知/跟踪 | 可无提权安装过滤器并继承到后代，在 syscall 边界介入 | 通知检查后 CONTINUE 存在指针参数竞态；可信代执行可能改变物理 parent；完整输入绑定及网络尚未证明 | 不通过；跟踪组合仍未知 |
| Windows debugger 子进程链 | 可在用户态代码运行前收到创建事件并暂停 | 已发生进程创建；是否符合合同需区分，不能只凭事件宣布失败或通过；argv/env/cwd/stdin 不可替换和完整网络尚未证明 | 不通过；非“不可能”结论 |
| macOS EndpointSecurity | 提供授权事件机制 | 普通 client 路径需要 root、Apple entitlement 和 Full Disk Access，违反当前权限边界；也未证明完整合同 | 当前范围不可采用 |

Linux 的[内核文档](https://docs.kernel.org/userspace-api/seccomp_filter.html)说明
BPF 不能读取指针目标，过滤器也不是完整沙箱；
[v6.14 UAPI](https://raw.githubusercontent.com/torvalds/linux/v6.14/include/uapi/linux/seccomp.h)
明确警告 CONTINUE 的 TOCTOU。ADDFD 的原子操作不是 exec 全输入原子绑定。
TRACE/ptrace 不是据此被排除，但仍需独立证明共享内存、并发线程、后代和
文件/输入替换不绕过强制边界。此处没有 runner ABI 或实验资格结论。

Windows 的[调试事件](https://learn.microsoft.com/en-us/windows/win32/debug/debugging-events)
发生在用户态执行前；[创建标志](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags)
说明 DEBUG_PROCESS 的后代链及新的调试链边界。这保留了研究价值，不能把
创建事件当成不可替换的命令批准合同。
[附加调试](https://learn.microsoft.com/en-us/windows/win32/api/debugapi/nf-debugapi-debugactiveprocess)
要求适当访问权；不声称调试自己创建的进程必然需要管理员权限。

Apple 的 [es_new_client](https://developer.apple.com/documentation/endpointsecurity/es_new_client(_:_:))
要求 entitlement 和 Full Disk Access；
[NOT_PRIVILEGED](https://developer.apple.com/documentation/endpointsecurity/es_new_client_result_err_not_privileged)
对应非 root；[entitlement 文档](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.endpoint-security.client)
要求向 Apple 申请。只读文档证据不能授权提权或新系统权限。

三条路线均没有闭合整个进程树的 IPv4/IPv6、TCP/UDP/DNS、Unix socket、继承
fd/handle 与 io_uring 网络/IPC 合同，也没有五平台官方原字节兼容性实验证据。
任何一项未知都不能计通过。上述结论只针对这三条路线，不是所有机制的
不可能性证明，也不放宽原 I1 威胁范围。

## 第二轮：Linux 跟踪组合补证

独立 reviewer 建议在同一机制族补查 syscall-entry-stop、全 mutator 停止、
私有 argv/env 重建、固定 executable FD 和冻结 stdin 的组合。
[ptrace 手册](https://man7.org/linux/man-pages/man2/ptrace.2.html)支持 syscall
进入前停止、后代跟踪和 EXITKILL，但操作按线程生效；仅停调用线程不足以
控制共享内存、cwd、fd table 或未完成异步写入。
[execveat](https://man7.org/linux/man-pages/man2/execveat.2.html)支持固定 FD 执行，
不自动绑定其余输入或 loader；
[memfd seals](https://man7.org/linux/man-pages/man2/memfd_create.2.html)可冻结内容，
不自动绑定消费者 FD/offset 与实际消费，FUTURE_WRITE 仍保留既有可写映射。
因此该组合是具体假设，尚不是闭合的安全桥。

[Yama](https://docs.kernel.org/admin-guide/LSM/Yama.html)说明现有 ptrace 策略会
影响普通账户可用性；未读取或修改 runner sysctl，平台权限仍未知。
威胁范围保持原合同：项目/config 并发修改及不可信输入，不扩大成同账户
完整调试/注入攻击防御。物理 parent、ARM64 ABI、stdin 与完整网络仍需证明。

## 独立审查与交接

第一轮独立核查确认三项 P1 资格缺口阻断 B2：Linux 完整输入/网络绑定、
Windows 合同与后代覆盖、macOS 权限冲突；不是研究文档本身的三项缺陷。
Windows 预用户代码边界保留，不得写成“禁止程序已经执行”。无新增 P2。
第二轮只读补证未形成可推荐 B2；独立修订复核通过，无新增 P1 文档错误、
过度结论或 P2。B0/B1 可交接，不晋级 I0/第二阶段。复核前报告 blob：
`3af6aadc302a878fbaaefec6ea94b476ab4ee77a`。
两轮研究于同一只读会话完成，主动与工具等待合计约 6 分钟，均未触及
每轮 90 分钟上限；停止是没有形成可推荐组合，不是预算耗尽或技术不可能。
没有推荐 B2 实验卡，未授权或启动 B2。只有找到具体可强制接入桥，才提出
有界实验并单独请求执行确认；若需权限、工具身份、parent 或平台承诺变化，
先交用户决定。原第二阶段后续并发/回滚、安全出口及第三至第五阶段均未跳过。
