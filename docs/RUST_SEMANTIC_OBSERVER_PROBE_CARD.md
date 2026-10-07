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
