# C0-A2：完整保守 registry 源码库存获取卡

2026-10-07。构建前需要审查精确依赖的 manifests/build.rs/proc macro；本卡只允许
惰性获取源码，不执行 Cargo、编译宏、build.rs、installer 或项目代码。
须独立审查本卡与获取器后执行；不授权构建或 product integration。
独立 reviewer `rust_bridge_plan_review` 已核对 425 个身份/checksum、缓存复用与
外层期限/清理，无 P1，允许源码获取；其缓存读取先限长的 P2 也已修订。

固定已核验 upstream Rust/Wasmtime Cargo.lock 的保守图（含 dev/optional/其他
target，不等同实际构建图），registry 唯一 name/version/source 共 425 包。
机器可读 C0_REGISTRY_SOURCE_LOCK.json SHA-256
`879c8e224486ff45bdc10ccf1d93dee8545b0cb368eb04da3581d58dae1e7e8f`。
输入 lock SHA-256 见 C0 证据；不解析最新版本、更新 lock 或使用 registry index。
先复用 A1 已核验的 12 份归档并重算 checksum，余项只从同一固定 static.crates.io
HTTPS 精确 name/version URL 获取。不承认缺包为构建资格通过。

沿用已审查 A1：压缩归档命中准确 Cargo.lock SHA-256 后，流式有界解压，
再解析 tar；拒绝 links/special/traversal/duplicates，不 extractall；逐文件 hash、
准确包前缀、非执行位、许可证保留。源码下载到新私有忽略目录，不修改原机器安装、
现有 A0/A1 或用户项目；不覆盖/重下载已验证资产，不改全局配置。

本批限额独立冻结：最多 425 包，每压缩归档 ≤8 MiB、总 ≤192 MiB；每解压 tar
≤64 MiB、总 ≤384 MiB；每物化文件 ≤8 MiB、总普通文件 ≤384 MiB/100,000 条。
总磁盘采样检测门 ≤768 MiB，不称硬配额。外层 controller 从 spawn 前执行
900 秒绝对期限，worker HTTP timeout 20 秒，覆盖 DNS/TLS/阻塞读取/解压/解析。
超限失败，kill 所属整组；共享 10 秒清理并确认组消失/父进程回收。清理异常写
失败 receipt；成功须 worker verified、controller completed、清理成功同时成立。
首次八小时原型预算仍沿用 02:21:13 UTC；不因 A2/平台/backend 重置。

首次 A2 在第 27 包 bit-vec 0.8.0 的 TLS 握手超时，55.11 秒失败，所属进程组
已消失且父进程已回收；未超限、未执行源码。保留失败 receipt。一次恢复允许
复用该失败批次中已下载的归档：仍先验证普通文件/非链接、限长并重算准确 lock
checksum，再复制到新私有目录重新逐文件核验；不覆盖旧证据。缺项继续同一精确
HTTPS 来源，无升限、换源或放宽校验。恢复获取器已独立复核，无新增 P1。
900 秒是 A2 获取尝试的合计额度：首轮 55.112 秒，恢复上限向下取整 844 秒，
不隐式重置。再次失败保留证据，不自动开始第二次恢复。

恢复在 393 包后遇到 Windows i686 的 `lib/libwindows.0.52.0.a`（13,204,988
字节）触发 8 MiB 普通文件门；所属组消失，controller 539.164 秒结束，失败保留。
新范围草案（须重新独立审查，不沿用恢复许可）：只在名字匹配
`windows_(i686|x86_64|aarch64)_(gnu|gnullvm|msvc)` 的固定 registry package 内，
对于 `lib/(lib)?windows[.0-9]*.(a|lib)` 且超过普通文件门的 native import library，
不物化/安装/执行；在原 ≤64 MiB 解压 tar 门内流式计算该条目的字节/hash，
receipt 明确记录 unmaterialized 和非构建资格。其他超大文件/links 继续拒绝。
归档 checksum 与所有源码/manifest/license 核验保持；没有提高任何大小门。
新增复用准确第二批已有归档，按相同限长/非链接/checksum 重验。若独立审查通过，
新范围执行必须显式 flag，合计 900 秒只剩向下取整 305 秒，不自动再恢复。
独立 reviewer 已审查该收窄范围和获取器，无 P1，批准一次显式 source-only
尝试。receipt 顶层标 scope/build_qualified=false；主动执行与最多 10 秒安全
清理分别计入证据，不重置原型预算。后续 directory-source 校验 bookkeeping
必须保留缺失 native 库，不能静默删去 unmaterialized 项冒充完整资产。
出口改为 425 包的源码研究库存，不是所有 native 资产完整安装/构建库存；之后
Windows 构建若需要这些库，必须单独资产获取/资格卡，缺项不能视为平台通过。

记录版本/URL/checksum、复用与新获取、逐文件 hash、限额/起止/关闭/清理/失败。
出口仅是完整保守源码库存可审计，实际 feature/target 构建图、C compiler/linker、
WASM target、ABI/observer、构建执行卡及 C1 全部仍未通过。失败不自动升限或换源。
