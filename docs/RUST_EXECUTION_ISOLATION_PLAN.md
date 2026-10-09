# Rust 执行隔离修订计划

日期：2026-10-06。状态：用户批准调整安全方案；独立复核允许 I0 实验，尚未证明可行。
本文件补充 RUST_PUBLIC_ENABLEMENT_PLAN.md 第二阶段，不替代原五阶段出口。
稳定 0.27.0 Rust 继续关闭；不改变五平台承诺、冻结查询和资源阈值。

## 失败基线与范围

候选源码 `514d813673c5ae537d5edb299b51c2033cad5258`，scanner run
`37424646449`，Windows ARM64 job `112141434618`：热 T2 会话期间新增
fixture 项目 `.cargo/config.toml`，其 rustc-wrapper 指向受控测试程序。
查询返回 stale/no nodes，owned analyzer 退出，但 forbidden-wrapper 标记出现。
正向控制使用独立标记，执行前禁止标记不存在；不能当成控制程序的正常运行。
其他四平台本次通过不证明没有竞态。完整 Windows 子进程时序尚未取得；后台
配置重载是待验证解释，不是已经证明的根因。原始报告保存在忽略的本地证据目录。

安全目标：初始化前至最后一个后代退出，不能因用户项目/配置变化执行项目程序、
外来 wrapper、rustup/download、shell 或非允许探测；不得联网。检查后杀进程、
事后拒绝结果、观察不到执行均不能单独满足该目标。保留原失败用例与哨兵。

仅修改候选分支和 Draft PR21，试验只使用自有临时 fixture/store。普通用户账户，
不装驱动、不提升权限、不修改系统防火墙/全局 ACL/MCP/editor，不重新签名或
修改锁定官方 Rust 二进制来冒充原来源。需要任何新权限/发行工具身份时另行讨论。

## I0：先证明边界可实现，不先承诺某个沙箱

逐平台建立最小独立 launcher 实验，再接入生产路径。两种 Linux、macOS ARM64、
两种 Windows 都必须有实际结果；未知 API/ABI/权限或缺 runner 列 blocked。
原生机制只是候选，文档存在不是工具兼容性或防护成功的证明：

- Linux：研究无特权 Landlock 的文件执行/读写限制及继承，配合适合的 syscall
  网络/IPC 限制。必须检测 runner 实际 ABI；仅 TCP 限制不覆盖 UDP/Unix sockets、
  inherited descriptors 或 io_uring。低 ABI 不允许 best-effort 降级宣称完整通过。
- Windows：研究 AppContainer/restricted launch 的权限及所有后代边界，配合现有
  Job ownership。Job 是清理机制，不是 exec allowlist；低完整性和限制写权限
  不自动禁止读取脚本或让可信解释器执行脚本。必须证明原生 ARM64 工具兼容。
- macOS：研究官方 App Sandbox/helper 的适用性、继承和签名要求。不能假定
  Python 启动任意官方 CLI 自动继承沙箱；不把私有/废弃接口当作可发布方案。
  若需要新签名身份/重新打包官方工具，先报告所需授权，保持此平台 blocked。

参考（原生能力边界，不是已选实施方案）：
[Linux Landlock](https://docs.kernel.org/6.14/userspace-api/landlock.html)、
[Windows AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)、
[Apple helper sandbox](https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app)。

I0 出口必须说明：能否在不改官方工具身份、不增加系统权限的情况下完成以下 I1
合同。若某平台不能，不自动缩减承诺或改成仅 T1，提交独立审查后的阻断及选择。
I0 逐平台输出机制与 executable/argv/env/parent/stdin/loader/解释器/网络合同条目的
对应证据及缺口，不以一个“sandbox=true”汇总替代。Apple helper 教程要求的
工具 entitlements/重签名不能直接满足本计划官方字节身份约束，必须列出冲突。

## I1：冻结执行域合同

先明确威胁范围：对项目/config 内容的正常并发修改与不可信输入有效；不是对拥有
同一账户完整调试/注入权限的主动攻击者宣称安全。若防护需要更强账户隔离，另议。

控制器持有 exact repository/worktree、generation、源码 fingerprint 和 receipt。
分析器只接触本次已验证的隔离视图；不直接观察用户可变目录。视图是 copy 而非
hardlink/symlink，包含冻结 source scope、manifest/lock/cfg、允许依赖和验证 sysroot。
控制器在视图生成前后验证输入一致性；不能取得一致视图则失败，不发布部分 generation。
前后 fingerprint 相同不是复制正确的充分条件：必须对复制后的每个 scope 文件、
manifest/lock/config/依赖字节重新计算身份，与绑定该 generation 的逐文件输入身份
一致；枚举集合、大小写/路径别名和缺失项也需相同。捕获/复制过程遇到路径替换、
重解析/链接或短暂中间态造成任一内容不符即丢弃本次 staging。仅对视图字节生成
新 hash 却继续沿用旧 generation 不允许；不能取得一致输入则 structured unavailable。
后代启动前再核验视图，测试加入确定性修改—复制—恢复与跨文件交错；只证明实际
使用视图对应冻结身份，不宣称能检测已经恢复的所有历史修改。
复制工具与 source 的任何新位置必须重新核验相同文件/许可证/身份，不重复下载。
工具缓存、Cargo home、祖先配置和 HOME 必须属于该隔离上下文，不读取用户 global
配置；不能证明合法配置语义等价则明确拒绝，而不是静默忽略改变 feature/target。
隔离视图由控制器管理，后代不能改 source/config/tool 文件，只允许独立 scratch。
视图本身不算沙箱：它还必须配合强制文件/执行/网络边界。

执行合同逐 role 固定 executable 身份、argv、cwd、env、parent 和必要 stdin。
只允许已审查 exact version/metadata/config/print；拒绝 generic cargo rustc、额外
参数、response file、输出重定向、项目输入编译、shell、解释器、插件及下载。
不能只允许 cargo/rustc 路径：可信程序也能解释不可信脚本/调用 wrapper。
现有 diagnostic matcher 仅作证据检查，不能充当执行拦截。若采用 broker，必须
证明未获许可的 exec 在启动之前被拒绝，不能靠 PATH 包装或事后观察代替。
上述原生文件/网络机制本身并不提供 exact argv/env/parent/stdin 合同；I0 必须另外
确定能强制这些条件的机制。broker 读取调用进程可变内存再放行 exec 存在 TOCTOU，
不能算出口通过；须在可信执行边界实际构造/绑定不可替换的参数、文件对象和输入，
证明请求被批准后调用者不能改写。若只能约束路径而不能约束参数，列 blocked，
不把验后 matcher 或官方工具信誉替代这一硬门。
固定 combined rustc query 必须证明 Cargo-owned synthetic stdin，不能接受项目输入。

联网禁止覆盖 IPv4/IPv6、TCP/UDP、DNS、Unix/path/abstract sockets 与继承 handle/fd；
stdio 仅继承必要管道，不继承网络连接。确需本地 IPC 的机制列精确协议/对象/身份，
证明不能作外部网络代理；不支持的场景拒绝，不报告完全禁网。规则在 analyzer
第一条指令之前成立，并覆盖 fork/exec/spawn、后台重载及控制器断开后的后代。
启动失败不能 fallback 到现有 unsandboxed provider；返回 structured unavailable。

## I2：接线、身份映射与生命周期

只有 I0/I1 独立审查通过再接线 runtime、owned process launcher、provider 与
refresh/recovery。冻结 launcher policy/version 纳入 receipt/generation。
版本探测、scanner 和 analyzer 各执行域都受合同约束，不能只保护查询主进程。
准备下载仍在单独显式授权阶段，禁止给查询执行域临时网络权限。
临时 URI 到原 exact repository 的映射只接受冻结 admission 集合；相对路径、
Unicode、重复名称、依赖/外仓 URI、diagnostic 和返回位置必须保持语义与隔离。
原项目修改后结果 stale、旧 view 停用；重建视图/refresh 原子发布，异常回滚原
config/generation，进程退出后才清理所拥有临时目录。并发查询/mutation 共享 lease。
生命周期和所有新增准备/复制耗时共用已冻结调用预算，不能提高阈值消除失败。

## I3：原生负例与出口

每个平台覆盖：初始危险 config、热会话 idle 重载、查询期间重载、工具/source/config
替换、祖先/Cargo home/成员/依赖/sysroot配置、未知 cwd/argv/env/stdin；网络协议与
继承 handle/fd、后代逃逸和控制器死亡。每项测试区分策略 deny、执行尝试、是否实际
执行及 marker；允许向自有隔离 scratch 写控制标记，不能用“marker 无写权限”伪装
未执行。正向控制先证明观察器及 marker 有效，再证明强制阻断发生于 exec/connect
之前；marker 正向控制必须使用同一 sandbox/token、同一 scratch 授权与观察窗口，
仅在沙箱外写 marker 不计通过。保存明确策略拒绝和实际启动证据而不只看 marker。
非确定性压力测试补充确定性 barrier，不以多次未触发证明竞态不存在。
保留原 windows-arm64 失败用例和旧源码/run身份；修复后新源码五平台复跑，不删用例。

最少三次独立进程运行，记录完整树 argv/cwd/env/parent/exit、策略加载及拒绝证据、
来源 hash、源码未修改、generation/配置保护和清理；敏感信息只在隔离白名单上下文。
平台 native skipped/缺观察/未证明拒绝是 blocked。Python audit/Job/strace 各自
证明的有限范围须明列，不能互换成全平台禁执行/禁网证据。

全部 I3 与原第二阶段准备并发/失败回滚门通过才推进 installed-wheel 阶段；后续
五平台冻结语义/资源、两个外仓、真实新 Codex 授权、独立最终审查和公开授权不变。
独立审查应优先挑战可实施性、官方工具兼容、快照语义、可信解释器绕过、网络/IPC
边界与失败回滚。未解决 P1 不标记可实施，不触发正式启用或 Release。
