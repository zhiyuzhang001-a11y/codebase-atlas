# C0-A3：隔离 WASM 标准库获取卡

本卡只获取官方 Rust 1.98.0 `wasm32-unknown-unknown` 标准库组件作为数据。
独立 reviewer 已审查卡片与获取器，无 P1，允许一次 asset-only 获取；receipt
同时记录固定 channel manifest hash、compiler commit 与组件归档/逐文件身份。
不运行 Cargo/rustc/rustup/installer，不运行包内脚本，不修改已核验的 native
toolchain、全局设置或产品 release lock。完整构建卡仍未通过。

已读取官方固定日期 channel manifest，SHA-256
`3f7d139b73bbbd0004ef6e58b430831c68cdad2b1f64ee2eb35d54c09199489a`；
编译器源身份为 `88d9e12ae178fab0fb5cc050a94da85685d449ea`，与已核验 native
1.98.0 toolchain 一致。唯一组件 URL：
`https://static.rust-lang.org/dist/2026-08-20/rust-std-1.98.0-wasm32-unknown-unknown.tar.xz`，
归档 SHA-256：`3bb537c09555b96020a38a3a8810c38908b194eff3c2cb6184a45dda5c6828de`。
不解析 latest，不改来源/版本，不调用安装器或网络代理，不跟随重定向。

只查两个固定候选缓存：账户 canonical `_rust-downloads/v1` 与先前已核验临时
安装对应的 `_rust-downloads/v1`；准确机器路径只在忽略获取器/STATE 内固定，
不递归搜索账户或临时目录。候选包括所有祖先均不得是链接，必须为普通文件。
有匹配归档则安全限长读取并重新校验，不重复
下载。缺失才从上述精确 URL 下载到新私有忽略目录。压缩 ≤128 MiB，解压 tar
≤512 MiB，普通文件 ≤128 MiB/项、总 ≤512 MiB/10,000 项，磁盘采样检测 ≤768 MiB。
拒绝链接、special、绝对/遍历/重复路径和异常包前缀；逐文件 SHA-256，文件 0600，
固定唯一 tar 前缀为 `rust-std-1.98.0-wasm32-unknown-unknown`。必需普通文件为
该前缀下 `LICENSE-APACHE`、`LICENSE-MIT`、`components`、`rust-installer-version`
与 `rust-std-wasm32-unknown-unknown/manifest.in`。只 materialize，不执行任何文件。

外层所属进程组 supervisor 从 spawn 前设 300 秒绝对期限；HTTP 操作 timeout
20 秒，期限覆盖 TLS、读取、解压、解析、物化。超时/超限失败，10 秒共享清理门
kill/reap 并确认组消失；成功须 worker verified 与 supervisor 完成、清理成功。
源 manifest/归档/逐文件身份、起止、资源与失败 receipt 都保留。八小时总预算沿用
C0-A 起点，不重置。获取器与本卡独立审查通过后才执行。

后续拟把该独立组件作为受控 guest 编译的显式 sysroot；native build dependencies
仍使用原核验 native sysroot，不复制或改变共享安装。此接线和执行参数必须在
完整构建卡另行审查，不能由本获取卡视作已批准。尚无 guest/module、语义或平台
资格通过；stable 0.27.0 Rust 仍关闭。
