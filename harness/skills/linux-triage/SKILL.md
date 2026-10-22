---
name: linux-triage
description: Linux 主机故障的排查清单与判据，覆盖 CPU、内存、磁盘、网络、systemd、时钟与 SELinux。需要在多个可能方向之间做取舍时加载。
whenToUse: 已确认是 Linux 主机问题，但不确定该查 CPU、内存、磁盘、网络还是服务时
---

# Linux 主机排查清单

## CPU

- 看 `top` / `pidstat`：是单进程打满还是整体高。
- `%iowait` 高 → 转磁盘方向；`%sy` 高 → 中断、系统调用、锁。
- 单进程高且线程数多：怀疑锁竞争、正则回溯、GC。
- 中断分布不均：查 `/proc/interrupts` 与 IRQ affinity。

## 内存

- `Out of memory: Killed process` → 记录被杀进程、RSS、cgroup。
- `memory cgroup out of memory` → 容器/服务限额不足。
- 可用内存低但 cache 高：先看是否可回收，不要直接判定泄漏。
- 持续增长且不回落：按进程看 RSS 曲线，怀疑泄漏。

## 磁盘

- `I/O error`、`blk_update_request`、`Buffer I/O error` → 硬件/链路劣化。
- `EXT4-fs error`、`XFS ... corruption` → 文件系统损坏。
- `Read-only file system` → 通常是错误后内核自动只读。
- 指标看 `%util`、`await`、`avgqu-sz`，不要只看 `tps`。

## 网络

- `Connection refused` → 端口没监听或进程已退出。
- `Connection timed out` → 链路、防火墙、后端阻塞。
- `Possible SYN flooding` → 连接数突增或攻击。
- `No route to host` → 路由/ARP/安全组。
- 需要抓包时提示用户提供 netcap 结果（平台支持 netcap 解析）。

## 服务与 systemd

- `Failed with result`、`Main process exited`、`start request repeated too quickly`
  → 启动失败或重启循环，先看服务自身日志。
- `Unit ... not found` → 安装/路径问题。
- 依赖服务未就绪导致的启动失败，要区分「首次启动」与「重启后」。

## 时钟与证书

- 时间戳回退、跨主机时间不一致 → NTP / 时区。
- TLS 握手失败且日志有 `certificate has expired` → 证书过期。

## SELinux / 权限

- `avc: denied` → SELinux 拦截，记录 source/target context。
- `Permission denied` 先区分文件权限、属主、挂载 `noexec`/`nosuid`。
