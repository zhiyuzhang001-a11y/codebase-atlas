# Rust 执行隔离计划独立审查

日期：2026-10-06；独立 reviewer：isolation_plan_review。
初稿 blob：`cb7c90f734a5ef9b4ee638c5f5f9ab36daad02e3`。
范围：只读计划及限定源码/官方文档审查，未运行产品或测试，未改 Git/配置/索引。
Atlas MCP 不可用，使用已知源码的有界检查。

初轮结论：允许自有临时 I0 可行性实验；不批准 I1 合同冻结、生产接线或晋级。
以下原始发现保留，不因修订删除。

## R1 [P1] 快照必须绑定复制后的实际字节

初稿54–61行仅写复制前后输入一致性，不足以排除复制期间短暂修改又恢复、
跨文件混合版本。隔离视图可不可变，不等于它属于声明的 generation。
应冻结完整 admission/逐文件 hash、类型、解析路径和上下文；防链接/reparse/
路径替换复制，验证复制后字节及集合匹配 generation，启动前再次核验。
加入修改—复制—恢复和跨文件交错负例；无法证明一致则 unavailable。
不宣称检测所有恢复后的历史修改。此门在 I1 前解决，不阻止 I0。

## R2 [P1，I1 可实施性出口] 原生资源沙箱不提供完整精确命令合同

Landlock 不检查 argv；seccomp BPF 不能解引用 argv 指针。broker 读取调用者
内存后再放行存在参数/路径/管道内容 TOCTOU。保存审查结果不等于执行边界绑定。
[Linux seccomp 官方说明](https://docs.kernel.org/userspace-api/seccomp_filter.html)。

AppContainer 继承约束不等于 argv allowlist，也需要 profile/受控资源授权。
[Microsoft legacy AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-for-legacy-applications-)。

Apple helper 教程的外部工具 entitlements/重签名要求，与保持官方 Rust 原字节
身份冲突，不能作为无需改变身份的既成方案；并未证明其他 macOS 方案都不可行。
[Apple helper 文档](https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app)。

I0 输出逐平台机制×executable/argv/env/parent/stdin/loader/解释器/网络证据矩阵。
缺少机制对应平台 blocked。签名/打包变更先请求决策。不得用文件/网络实验通过
代替 exact exec 合同通过。

## R3 [P2] marker 正向控制必须使用同等隔离身份

沙箱外控制只能证明脚本本身有效，不排除负例中 marker 因无写权限未出现。
应在同一 sandbox/token、scratch 授权、观察窗口证明写 marker 能力；分别保存
策略 deny、实际进程启动、marker 三种证据。无 marker 不代替执行前拒绝。

## 合理边界

保留 Windows ARM64 原失败；后台重载仍为推断。五平台不降低门槛、官方来源
不冒充、不 unsandboxed fallback、第一条指令前强制限制、后代/控制器死亡、
网络/IPC及独立资源门/公开授权要求均合理。

## 修订与复核

主 agent 已修订 R1–R3，修订版 blob
`9768cb52d69932017ba7c69460e32145666981e9`；独立复核完成。
R1–R3 已在计划层面解决，无新增计划阻断项，可开始 I0 独立临时实验。
这不批准 I1 冻结、I2 接线、阶段晋级或公开启用，也不代表任一平台已可行。
计划文字修订不证明任何 native enforcement 实现或平台资格已通过。
