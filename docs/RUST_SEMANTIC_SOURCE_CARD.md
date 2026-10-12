# C0-A：惰性源码获取执行卡

2026-10-07。用户已批准自有语义核心路线并要求持续实施。此卡只允许获取与
逐文件核验上游源码，不能据此构建、运行源码、获取 compiler/runtime 二进制、
运行 Cargo/rustup/build.rs/proc macro，或改变产品接线。完整构建卡仍是下一道门。
独立 reviewer `rust_bridge_plan_review` 已只读审查，无 P1，允许执行 C0-A；
本卡把原 C0 获取与构建拆开，不豁免构建依赖审查。

## 来源与身份

- Rust：公开 HTTPS `https://github.com/rust-lang/rust.git`，commit
  `88d9e12ae178fab0fb5cc050a94da85685d449ea`，根 tree
  `b10ad5e3f8014a7d88a67cb98273459a0d77e3ce`。
  仅物化 `src/tools/rust-analyzer` 与根许可证。
- Wasmtime：公开 HTTPS `https://github.com/bytecodealliance/wasmtime.git`，commit
  `3c8a3e79aa3188a1a12b3aebbaa097abccceac92`，根 tree
  `8f5adb74e49531cbbb19810849f129d3c07ed45d`。
  只物化 Cargo manifests/lock、源码及许可证；不获取子模块。

根 tree 来自官方 GitHub commit API，只是来源证据，不是签名认证。
本机系统 Git 为获取工具；记录实际路径/版本。每个物化普通文件的 Git blob
hash 必须等于固定 tree 条目，另记录 SHA-256。拒绝创建/跟随符号链接、gitlink、
异常路径；上游 mode 120000 只把其目标字节作为普通非执行文本存入独立证据目录，
记录原 path/mode/blob，不放入可构建源码目录。根许可证独立核验，不凭链接假定。
不把 Git object hash 当作签名或二进制资格。许可证随源码保留。

## 获取环境与限额

仅在忽略的 evaluation 私有新目录内创建源码 Git 仓；没有 Atlas/navigation 索引。
不复用未知目录、不覆盖已有内容，不使用项目凭据、Git hooks、模板、全局配置、
filters、submodules、LFS 或递归协议。使用清空后的 PATH/临时 HOME 和 Git config，
显式禁用 hooks/credential helper、全局与系统配置、文件协议、重定向与代理；
仅固定 HTTPS origin。先 fetch 对象，检查准确 commit/tree 与文件 modes，再
逐 blob 物化，不运行 checkout smudge 或 hooks。上游内容仅是数据。

每仓获取 wall-time 最多 300 秒；总获取和核验最多 15 分钟；磁盘最多 512 MiB
每仓，物化普通文件最多 10,000、每文件最多 8 MiB、总普通文件最多 256 MiB。
外层采样监控所属 Git 进程及其对象目录；这是超限检测而非硬磁盘配额，超限
峰值记录失败。禁自动 maintenance/gc，lazy fetch 仍仅同一固定 HTTPS origin，
其 remote-https/fetch-pack/repack helper 纳入所属进程组清理。超限中止所属
进程树，10 秒清理期限。
记录失败，不重复获取同一已验证源码。网络仅该阶段允许；查询期禁网门未开始。
首次八小时原型预算从本卡执行开始计时，获取/核验计入，不因阶段重置。

## 证据及出口

记录起止 UTC、命令/env 键、准确 commit/tree、所有 blob/SHA-256、文件数/字节、
磁盘峰值、退出/清理结果至忽略证据；机器路径不提交。失败资料保留可恢复，不删除
旧证据/资产。不触发 CI、不改 PR 状态、不恢复 heartbeat、不写 main/tag/Release。
出口仅是 verified source available；构建、WASM imports、语义/平台/资源资格均未完成。
