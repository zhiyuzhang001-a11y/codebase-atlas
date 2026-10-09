# C0-A1：关键 guest 依赖源码审计卡

2026-10-07。仅惰性获取和静态审计以下 12 个 registry package，不运行 Cargo、
编译宏、build.rs 或其中任何代码，不获取 runtime/compiler 可执行资产。须独立
审查本卡及获取脚本后执行；不能因 A0 通过就直接构建。
独立 reviewer `rust_bridge_plan_review` 复核外层期限与响应清理修订后允许获取。
其控制器清理异常 receipt 建议也已补齐；成功须 worker verified、控制器 completed
且所属进程组清理确认同时成立。

准确版本/checksum 来自已逐文件核验的 Rust upstream Cargo.lock，SHA-256
`fad2ba1bf5035457b33fa83cac5a8edad65e8caa7d0e22ca1f0ba3cfdf7b6404`。
已冻结机器可读子集 C0_KEY_SOURCE_LOCK.json，SHA-256
`f6271460e2a28c3ae0bfa24cf706a2af81ad50d96e7d611e43c1829436fa9c10`，12 包：
boxcar、inventory、jod-thread、parking_lot、parking_lot_core、portable-atomic、
proc-macro2、rayon、rayon-core、salsa、salsa-macro-rules、salsa-macros。
不得解析 latest、改版本或更新 lock。此子集仅定位线程/时钟/初始化适配点，不是
完整 feature-resolved 构建依赖锁；完整保守 lock 闭包 425 registry 包不授权获取。

只读公开 HTTPS static.crates.io 的固定 `crates/<name>/<name>-<version>.crate`，
不使用 registry index/Cargo、凭据、代理或重定向。先校验压缩归档 SHA-256 命中
lock；流式解压先施加长度限额，再解析 tar；只接收准确 package 前缀的普通文件/
目录，拒绝链接、特殊文件、绝对路径、dot/dotdot 路径及重复条目，不调用 extractall。
逐文件 SHA-256、许可证与 Cargo manifest 保存；物化一律非执行普通文件。

新私有忽略证据目录，禁止覆盖或复用未知数据。最多 12 个归档，每份压缩 ≤8 MiB，
总压缩 ≤64 MiB；每份解压 tar ≤32 MiB，总解压 tar ≤96 MiB；物化每文件 ≤4 MiB、
最多 20,000 个文件/总普通文件 ≤96 MiB。磁盘采样检测门 256 MiB（非硬配额），
超限记录失败。所有获取/核验共 300 秒；HTTP I/O 超时 20 秒，启动/每块/最终
出口检查总 deadline。外层可信 Python 控制器单独拥有一个获取 worker 进程组，
从启动前施加 300 秒绝对期限及磁盘监控，不依赖 HTTP read 返回才中断；超限
kill 整组，10 秒共享清理期限确认组消失和父进程回收，OS 关闭 socket/file。
worker 内部另做前后检查和响应 context cleanup；没有未拥有的后台进程。
worker 被强制中止时由控制器保留失败 receipt，不把未写完的 worker 证据标通过。
首次八小时原型预算仍沿用 02:21:13 UTC，不因 A1 重置。

记录 lock 身份、准确 URL/版本/archive SHA-256、逐文件 hash、字节/时间/失败/
响应关闭证据，不存机器路径于提交。失败不升限、不自动改源或启用 Cargo获取。
出口只代表这些源码可供审计；编译/实际 imports/语义/禁网/五平台仍未开始。
