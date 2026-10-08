# C0-M：缩减 workspace 解析草案（未获执行资格）

目的：保守库存不是 actual build graph。独立审查发现根/features 尚未冻结的 P1，
因此当前不得执行。原 A2 全库存门失败，不能以它为已通过前提；本草案下一版
须以 C0-G 的完整 181 registry guest 源码复核及固定 wrapper root 为前提，
用已完整核验的官方 Cargo/Rustc 解析固定上游 guest 依赖，不编译，不运行
build.rs/proc-macro/rustfmt，不生成或执行 guest，不接线产品。卡片与获取器须
独立审查，无 P1 后执行一次有界 metadata；不因解析失败改源/版本或自动联网。

## 固定输入与允许命令

Rust commit `88d9e12ae178fab0fb5cc050a94da85685d449ea` 的准确已核验 RA 源码，
根 Cargo.lock SHA `fad2ba1bf5035457b33fa83cac5a8edad65e8caa7d0e22ca1f0ba3cfdf7b6404`。
使用同一 upstream lock 根 ide 的完整保守闭包（211 个 lock package，30 个本地
package）。在新私有临时副本中，只把 workspace members 缩为这 30 个准确 package
manifest 路径，移除无关 glob member；依赖 declarations/features 源码不改。
所有上游原证据保持不动。生成后的 manifest 身份独立记录，不冒充原字节。

C0-G 归档/逐文件复核成功后才可审查 Cargo directory source 的校验 bookkeeping；其
`.cargo-checksum.json` 必须使用固定 lock 的 package checksum 和 receipt 的全部
文件 hash，不向 registry index 解析版本。仅配置到新私有 CARGO_HOME；不改全局
或现有项目配置。固定 registry 源替换为准确已核验目录；Git dependencies 拒绝。

只调用准确已核验 Cargo 1.98.0：`metadata --offline --format-version 1
--filter-platform wasm32-unknown-unknown --manifest-path <owned-manifest>`。
允许 Cargo 对同一核验 rustc 的版本/target 元数据查询，不允许工具 shim/wrapper、
其他 subprocess、probe 编译或 linker。metadata 的准确 JSON 命令和全部真实子命令
记录；未观察到的执行面不能宣称已获证明。

metadata 可在所属副本产生 candidate lock：仅允许固定上游 lock 中现有
name/version/source/checksum 的严格子集；不改变版本/checksum、不添加未知依赖。
退出后逐项检查，无差异方接受 JSON 为构建前证据。之后构建才使用冻结 candidate
lock 的 `--frozen` 模式；本卡不授权构建。

## 环境与资源

副本/CARGO_HOME 均 private owned 目录；cwd 不位于用户项目/Cargo 配置祖先下。
先拒绝 cwd/工具/源码/配置路径中的异常链接或身份变化，检查全部可影响 Cargo 的
祖先配置。环境白名单从空环境构造，只含固定 HOME/CARGO_HOME、locale、已核验
Rust 工具路径、显式 RUSTC 和 CARGO_NET_OFFLINE；不继承 rustup、两个 wrapper、
RUSTC_BOOTSTRAP/RUSTC_STAGE、SALSA_DEBUG_MACRO、代理、loader、credential 或用户
rustflags。这个阶段不设置 target sysroot，不允许项目代码输入；WASM std 未接线。

外层所属 controller 先于 spawn 安装 120 秒绝对期限/所属树 RSS 1536 MiB 检测门，
输出各 ≤32 MiB、新副本磁盘检测 ≤768 MiB；共享 10 秒 cleanup，组消失且 parent
reaped 才成功。观察器覆盖初始化到退出；缺资源采样/子命令证据记未完成而非通过。
所有 source 身份、copied/修改后 hashes、env 键、commands/退出/cleanup、lock 对照
和失败记录保留于忽略证据，机器路径不提交。原型八小时预算不重置。

修订后的出口最多为缩减 workspace 的 Cargo-resolved 图，不是 guest 实际构建图。
`--filter-platform` 不排除全部 dev/member 对解析的影响；未来必须冻结唯一 guest
根 manifest、ide 与其他依赖的 features/default-features、resolver/target，从根
分层提取 guest normal 和 native build/proc-macro 边。修改后根、candidate lock、
全部本地 package 路径/身份及 directory source 配置 hashes 须绑定同一 receipt。

执行尚缺具体完整子命令观察机制；进程表轮询会漏掉短命 rustc，不得作为无其他
subprocess 的证明。此项未关闭，不运行 metadata。下一步可继续惰性源码审查，
不为这道门申请系统权限或把缺口写为通过。尚须审查真实 build
scripts/macros、runtime graph、编译器/linker动态装载、ABI 和构建/执行卡；不称 C0、
C1 或 Rust Atlas 可用资格完成。

候选唯一根已写为 `prototypes/rust-semantic-guest/Cargo.toml.in`：resolver=2、
edition=2024、guest target 必须显式 wasm32-unknown-unknown；ide/ide-db/hir
均 default-features=false、无额外 features，cfg 明确 tt/syntax；serde_json、
triomphe、rayon 精确固定到原 lock 版本。此模板没有 src/lib.rs 或已生成 workspace，
不能当作已经冻结的最终编译输入。未来 metadata 应以该单一根提取 normal/build
边，不能只缩减 RA workspace 后宣布 actual graph。owned wrapper 本地新包身份
须单独绑定；其余依赖不得新增原 lock 外 name/version/source/checksum。

观察器下一步只做 [C0-O 临时 Linux 探针](RUST_SEMANTIC_OBSERVER_PROBE_CARD.md)，
不要求 macOS 提权；探针也不是 metadata 执行资格。原生 proc-macro 的 target 条件
不得错误地使用 guest wasm cfg；所有缺失来源或观察面继续记 incomplete。

C0-O v2 原始执行控制已独立复核通过。新增纯准备模块
`scripts/rust_semantic_exec_decode.py`，对完整 execve/execveat 记录按 C 字符串
解码 argv/env，拒绝未知/截断/指针/NUL/无效 UTF-8，保留 fd 与 flags 而不猜路径。
它不是执行器或安全策略，也不负责 syscall 成对、cwd/FD 来源/所有进程覆盖。
主 agent 用它离线解码 v2 四条真实 exec，三个固定 true argv 完整一致；合成回归
覆盖转义/Unicode octal/缺失/超限。尚未接线完整 metadata 观察器；RSS、确切允许
命令、复制配置/receipt 与实际 roots/features 图仍未闭合，不执行 metadata。

后续纯准备新增 `pair_records`：只接受有界 exec-only 子序列，完整解码后按 PID
配对交错 unfinished/resumed 返回，保留失败尝试，只有明确结果 0 标为 launched。
同 PID 未完成时再次 exec（包括另一 syscall）、缺失/重复/错配返回、未知结果
或续行追加参数均拒绝。上限 4096 行、单行 1 MiB、合计 16 MiB；不运行外部程序。
它不证明筛选前原 trace 完整、PID 生命周期、cwd、FD 来源或 RSS，不接线执行器。
对已留存 C0-O v2 的四条真实完整 exec 行离线解析通过；交错/失败/异常由纯合成
测试覆盖，尚未取得真实交错控制资格。不把旧 artifact 当作新 head 的运行证据。

目录路径准备另增 `decode_chdir_record`，复用同一严格 C 字节转义解码，避免
把 strace 路径当 JSON 导致 Unicode/octal 路径无法解析。只接受完整 absolute
chdir 的明确成功/失败结果，拒绝相对路径、`..`、双斜杠/非规范字符串、未知结果、
截断/未完成记录及 fchdir。路径字符串不证明目录 inode，也不证明子进程继承、
共享 cwd 的并发顺序或 FD 来源；没有接线/更改旧 native observer。下一步仍须
完整进程创建/目录事件配对与 inode/FD admission、受控正例及所属树 RSS 监督。

RSS 准备模块 `scripts/rust_semantic_linux_resources.py` 只读取调用者已准入的
PID/starttime 集，在同一 proc directory FD 下先后核对 PID/starttime/session/
process group，读取有界 `smaps_rollup`。累计 RSS 与显式 hugetlb 项，不拿 PSS
代替 RSS；缺字段、零值、权限失败、进程消失或身份变化不生成通过样本。
根据 [Linux proc 文档](https://docs.kernel.org/filesystems/proc.html)，普通 RSS
计数可不精确，smaps/rollup 给出映射统计；单次读取仍不是跨进程原子快照或连续峰值。
目前只有纯/模拟测试，不读取真实 proc、不 spawn、不接线 metadata。调用者仍须
证明完整进程准入与创建/退出/逃逸覆盖、采样间隔/延迟、deadline/超限清理及受控
正例；短命子进程和两次采样之间的峰值不能据此声称已覆盖。资源门仍未通过。
