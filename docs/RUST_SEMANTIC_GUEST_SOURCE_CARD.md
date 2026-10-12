# C0-G：语义 guest 源码范围修订（source-only 审查通过）

2026-10-07。原 A2 的 425 包库存出口失败，不修改其限额或结果。395 包只通过
独立 partial reusable-source 复核；并非构建资格。本卡转向先验证真实语义 guest，
不是分批补完 425 包后冒充原出口通过。不执行 Cargo、源码、宏或构建脚本。

独立 reviewer `rust_bridge_plan_review` 已复核卡和获取器，无 P1；允许一次显式
`--guest-missing-source`。该批准不覆盖 metadata、构建或任何语义资格。

## 固定范围与身份

沿用 Rust commit `88d9e12ae178fab0fb5cc050a94da85685d449ea` 和 RA Cargo.lock
SHA-256 `fad2ba1bf5035457b33fa83cac5a8edad65e8caa7d0e22ca1f0ba3cfdf7b6404`。
以该 lock 的唯一 local `ide 0.0.0` 为根，逐 dependency 完整匹配
name/version/source，保守闭包 211 包：30 local、181 registry。
这是包含 dev/optional/其他 target 的源码上界，不是 Cargo-resolved guest 图。
未来唯一 guest 根 manifest/features/target 和 native proc-macro/build 图另行冻结。

181 registry 中已有 170 包出现在独立复核的 395 包前缀；只读复用已物化源码，
不重新解压、不复制全部 395 包、不执行代码。缺少的 11 个准确身份如下，checksum
必须逐项匹配原 425 包 JSON lock（SHA-256
`879c8e224486ff45bdc10ccf1d93dee8545b0cb368eb04da3581d58dae1e7e8f`）：

- `winnow 0.7.15`、`winnow 1.0.3`
- `writeable 0.6.3`、`yoke 0.8.3`、`yoke-derive 0.8.2`
- `zerofrom 0.1.8`、`zerofrom-derive 0.1.7`、`zerotrie 0.2.4`
- `zerovec 0.11.6`、`zerovec-derive 0.11.3`、`zmij 1.0.21`

剩余 19 包不属于这个保守 ide lock 闭包，不能因此称宿主依赖已完整或五平台通过。
不为此卡取得 Windows native import libraries、WIT、xed、zstd 等其他包。
唯一未物化的 Windows i686 GNU 库不进入 guest 合格源码集。

## 明示新资源合同，不重置失败证据

原 A2 的 384 MiB 解压 tar 门不足以容纳全 425 包，保持失败。本卡不是沿用该门
的“恢复”；提出独立审查的 guest-focused 范围及新合同，未经复核不获取。
已核验前缀 tar 402,374,144 字节永久计入本合同基数；11 包新增 tar 合计
最多 32 MiB，累计硬计数必须同时 ≤448 MiB。既有普通文件 373,142,345 字节、
压缩归档 52,373,618 字节也保留为累计基数；新增普通文件 ≤32 MiB、压缩 ≤16 MiB，
累计普通文件 ≤448 MiB、压缩 ≤192 MiB。没有把未物化库从解压工作量中扣除。
这些是已核验内容库存计数，不是历次重试重复解压的 CPU 工作总量；历次工作与
失败耗时照常保留，不作全库存通过声明。

单归档 ≤8 MiB、单 tar ≤16 MiB、普通文件 ≤8 MiB，新增 ≤10,000 文件；拒绝
全部链接、special、traversal、duplicate、异常 prefix，不作任何大文件例外。
新输出目录磁盘采样 ≤96 MiB（检测门，不是硬配额）。不复制历史 395 包库存。
只复用准确已存在、普通非链接、限长且重算 checksum 的这 11 包归档，否则
固定 static.crates.io HTTPS name/version URL；禁代理/redirect/凭据/换源。

原 A2 三次 controller 合计 602.419 秒；从原 900 秒额度保守只剩 296 秒。
本卡外层 spawn 前安装 296 秒绝对期限，HTTP 每操作 ≤20 秒，新增获取只允许
一次，不自动重试。超限 kill 所属整组，共享 10 秒清理确认组消失且父进程
reaped；主动与清理时间分别记录。八小时原型起点 02:21:13 UTC 不重置。

## 出口

首次显式尝试在 8 包后获取 `zerovec 0.11.6` 时遇到 TLS unexpected EOF，
controller 19.241 秒失败，所属组消失/父进程回收；没有 source/build 执行。
317 个已记录普通文件、压缩 548,675 字节、tar 3,706,880 字节、普通文件
3,453,626 字节通过独立 partial 前缀复核，不称本卡通过。

一次网络失败恢复草案须重新独立审查：只读复用这 8 包、不再解压/复制；只取得
`zerovec 0.11.6`、`zerovec-derive 0.11.3`、`zmij 1.0.21`。固定上述失败 receipt
身份后，将其全部已核验字节计入 C0-G 新增计数；32/16/32 MiB 新增门不重置，
448/192 MiB 累计门不重置。原 900 秒合计已用 621.660 秒，恢复期限保守 276 秒。
来源、单文件/归档/链接拒绝与清理均不变；没有一般重试许可。未独立复核前
不写为恢复已批准或启动，不恢复完整 425 包库存目标。

上述三包网络恢复经独立审查后执行，但 5.227 秒内再次 TLS EOF，尚未取得任何
字节，所属组消失/父进程回收。之后只读发现本机既有 Cargo registry cache 的
这三份归档，128,583 /22,115 /26,665 字节，普通非链接且原锁 checksum 全匹配。
离线缓存复用 delta 须独立审查：显式 `--guest-cache-final`，仅固定这三份 cache
路径（机器路径仅忽略 helper/receipt），安全祖先与限长读取、重算原锁 checksum，
复制到新私有证据后有界解析/逐文件核验；cache 缺失或变化即失败，禁止网络
fallback，不写原 cache、不再次处理 8 包。保留相同累计大小门，原 900 秒合计
626.887 秒，离线处理上限保守 270 秒，一次、无自动重试。此项非构建或安装。

离线 delta 经独立审查后完成：controller 0.112 秒、worker verified、所属组
消失/父进程回收；3 包压缩 177,363 字节、tar 894,976 字节、100 个普通文件
共 814,062 字节。独立 reviewer 重验全部 archive/file/hash/字节/0600，无链接、
库存准确、许可证保留；完整 181 registry 集合也重验身份，无缺项或未物化库。
C0-G 保守 guest 源码出口通过，仍不是实际图、构建或语义资格。11 包合计压缩
726,038 字节、tar 4,601,856 字节、417 文件共 4,267,688 字节；含原前缀的累计
内容 tar 406,976,000 字节，未超本修订卡限额。历次失败不变。

卡与获取器先独立审查。获取后独立重算新增 11 包 archive checksum、全部文件
hash/字节/非执行权限和许可证，核对 170 个复用包准确身份及清单。仅可称
181 包保守 guest registry 源码可审计；metadata、真正 target/features 图、
构建脚本能力、ABI、宿主依赖、构建/C1/资格仍未通过。记录全部失败和机器证据
于忽略 STATE；不提交机器路径，不安装工具或修改用户项目/全局配置。
