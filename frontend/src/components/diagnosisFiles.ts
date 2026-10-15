/* 智能诊断页: 共享类型 + 内置演示文件内容
   原型阶段文件内容由本地 mock 提供; 后续对接后端文件读取接口时, 只需替换 readFile 实现。 */

export interface Attach {
  id: string
  name: string
  path: string
  size?: string
}

export type FileKind = 'text' | 'image'

/* 阅读器标签页: original 用于「撤销」, content !== original 即未保存 */
export interface ReaderTab extends Attach {
  kind: FileKind
  content: string
  original: string
  lang?: string
}

export interface FilePayload {
  kind: FileKind
  content: string
  lang?: string
}

const IMG_EXT = /\.(png|jpe?g|gif|svg|webp|bmp)$/i

const svgDataUri = (title: string, inner: string) =>
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="720" height="400" viewBox="0 0 720 400" ` +
      `font-family="Segoe UI,PingFang SC,Helvetica Neue,sans-serif">` +
      `<rect width="720" height="400" fill="#ffffff"/>` +
      `<text x="28" y="34" font-size="16" font-weight="600" fill="#1b2a41">${title}</text>` +
      inner +
      `</svg>`,
  )

const TREE_SVG = svgDataUri(
  '北京A 集群拓扑 · 采样时刻 10:05',
  `<rect x="30" y="70" width="130" height="54" rx="10" fill="#ffffff" stroke="#c9d8ee"/>` +
    `<text x="95" y="102" font-size="13" fill="#1b2a41" text-anchor="middle">Nginx LB</text>` +
    `<rect x="285" y="58" width="150" height="54" rx="10" fill="#fff2f0" stroke="#e2574c" stroke-width="2"/>` +
    `<text x="360" y="90" font-size="13" fill="#b23a2c" text-anchor="middle">node-01 · CPU 95%</text>` +
    `<rect x="285" y="150" width="150" height="54" rx="10" fill="#ffffff" stroke="#c9d8ee"/>` +
    `<text x="360" y="182" font-size="13" fill="#1b2a41" text-anchor="middle">node-02 · CPU 41%</text>` +
    `<rect x="285" y="242" width="150" height="54" rx="10" fill="#ffffff" stroke="#c9d8ee"/>` +
    `<text x="360" y="274" font-size="13" fill="#1b2a41" text-anchor="middle">node-03 · CPU 38%</text>` +
    `<rect x="545" y="58" width="145" height="54" rx="10" fill="#eef4ff" stroke="#a8c2ea"/>` +
    `<text x="617" y="90" font-size="13" fill="#1b2a41" text-anchor="middle">PostgreSQL</text>` +
    `<rect x="545" y="150" width="145" height="54" rx="10" fill="#eef4ff" stroke="#a8c2ea"/>` +
    `<text x="617" y="182" font-size="13" fill="#1b2a41" text-anchor="middle">Redis Stream</text>` +
    `<path d="M160 97 H285" stroke="#8fa8c8" stroke-width="1.5"/>` +
    `<path d="M435 85 H545" stroke="#8fa8c8" stroke-width="1.5"/>` +
    `<path d="M435 177 H545" stroke="#8fa8c8" stroke-width="1.5"/>` +
    `<path d="M360 112 V150" stroke="#c9d8ee" stroke-width="1.5"/>` +
    `<path d="M360 204 V242" stroke="#c9d8ee" stroke-width="1.5"/>` +
    `<text x="30" y="330" font-size="12" fill="#6b7c96">采样: top -b -n 3 · batch=3 · 采集窗口 10:00–10:20</text>` +
    `<text x="30" y="352" font-size="12" fill="#b23a2c">异常: loginsight-parser 481% CPU, %iowait 1.2 (排除磁盘 IO)</text>`,
)

const FLAME_SVG = svgDataUri(
  'CPU 火焰图 · loginsight-parser (峰值 481%)',
  `<rect x="30" y="74" width="660" height="34" rx="4" fill="#e2574c"/>` +
    `<text x="40" y="96" font-size="12" fill="#ffffff">loginsight-parser 481%</text>` +
    `<rect x="30" y="116" width="300" height="34" rx="4" fill="#ef8354"/>` +
    `<text x="40" y="138" font-size="12" fill="#ffffff">re2 regex_backtrack 41%</text>` +
    `<rect x="338" y="116" width="200" height="34" rx="4" fill="#f4a261"/>` +
    `<text x="348" y="138" font-size="12" fill="#ffffff">shard_2 parse 26%</text>` +
    `<rect x="546" y="116" width="144" height="34" rx="4" fill="#f7b267"/>` +
    `<text x="556" y="138" font-size="12" fill="#ffffff">json encode 12%</text>` +
    `<rect x="30" y="158" width="176" height="34" rx="4" fill="#f9c784"/>` +
    `<text x="40" y="180" font-size="12" fill="#5b4527">line_len 71204</text>` +
    `<rect x="214" y="158" width="116" height="34" rx="4" fill="#f9c784"/>` +
    `<text x="224" y="180" font-size="12" fill="#5b4527">超 64KB 分支</text>` +
    `<rect x="30" y="232" width="660" height="34" rx="4" fill="#5b8def"/>` +
    `<text x="40" y="254" font-size="12" fill="#ffffff">kernel / sys 18%</text>` +
    `<text x="30" y="300" font-size="12" fill="#6b7c96">采集: perf record -F 99 -p 6821 -- sleep 30</text>`,
)

const IMAGES: Record<string, string> = {
  'f-topology': TREE_SVG,
  'f-flame': FLAME_SVG,
}

const TEXT_FILES: Record<string, Required<Pick<FilePayload, 'content'>> & { lang: string }> = {
  'f-messages': {
    lang: 'log',
    content: `Oct 15 10:02:11 node-01 systemd[1]: Started loginsight parser (task=20261015_1011).
Oct 15 10:03:04 node-01 loginsight-worker[6720]: INFO  ingest shard=1..3 files=42 size=418MB
Oct 15 10:05:33 node-01 kernel: Out of memory: Killed process 6821 (loginsight-parser) total-vm=8421332kB
Oct 15 10:05:47 node-01 loginsight-worker[6844]: WARN  rule=E-TIMEOUT-504 elapsed=6412ms shard=2 status=timeout
Oct 15 10:06:02 node-01 loginsight-worker[6845]: ERROR regex backtracking pattern="^(\\S+\\s+){2,}\\S+$" line_len=71204
Oct 15 10:06:18 node-01 nginx[1180]: upstream timed out (110: Connection timed out) while reading response header
Oct 15 10:06:41 node-01 loginsight-api[6912]: WARN  analyse task=20261015_1011 p99=2841ms
Oct 15 10:07:05 node-01 systemd[1]: loginsight-parser.service: Main process exited, code=killed, status=9/KILL
Oct 15 10:07:05 node-01 systemd[1]: loginsight-parser.service: Scheduled restart job, restart counter is at 3.
Oct 15 10:08:12 node-01 kernel: TCP: request_sock_TCP: Possible SYN flooding on port 8000. Sending cookies.
Oct 15 10:09:30 node-01 loginsight-worker[7011]: INFO  retry shard=2 attempt=2 backoff=2s
Oct 15 10:10:44 node-01 loginsight-worker[7011]: ERROR shard=2 failed: timeout after 30s (rule=E-TIMEOUT-504)
Oct 15 10:12:00 node-01 loginsight-api[6912]: INFO  analyse done task=20261015_1011 status=partial rules_hit=17
`,
  },
  'f-secure': {
    lang: 'log',
    content: `Oct 15 09:58:02 node-01 sshd[6120]: Accepted publickey for ops from 10.20.3.18 port 51422 ssh2
Oct 15 09:58:02 node-01 sshd[6120]: pam_unix(sshd:session): session opened for user ops by (uid=0)
Oct 15 10:01:44 node-01 su[6203]: (to root) ops on pts/1
Oct 15 10:04:19 node-01 sshd[6411]: Failed password for invalid user admin from 203.0.113.77 port 39211 ssh2
Oct 15 10:04:21 node-01 sshd[6411]: Failed password for invalid user admin from 203.0.113.77 port 39214 ssh2
Oct 15 10:04:25 node-01 sshd[6412]: pam_unix(sshd:auth): authentication failure; rhost=203.0.113.77
Oct 15 10:05:00 node-01 sshd[6413]: error: maximum authentication attempts exceeded for invalid user admin
Oct 15 10:12:37 node-01 sudo[6888]: ops : TTY=pts/1 ; PWD=/opt/tasks ; USER=root ; COMMAND=/usr/bin/top -b -n 3
`,
  },
  'f-access': {
    lang: 'log',
    content: `10.20.3.18 - - [15/Oct/2026:10:04:58 +0800] "GET /api/v1/tasks?page=1 HTTP/1.1" 200 18432 0.412
10.20.3.41 - - [15/Oct/2026:10:05:02 +0800] "POST /api/v1/analyse HTTP/1.1" 202 118 0.058
10.20.4.7 - - [15/Oct/2026:10:05:39 +0800] "GET /api/v1/tasks/20261015_1011/export HTTP/1.1" 200 1052841 12.884
10.20.4.7 - - [15/Oct/2026:10:06:18 +0800] "GET /api/v1/perf/summary HTTP/1.1" 504 0 30.001
10.20.3.18 - - [15/Oct/2026:10:06:33 +0800] "GET /api/v1/tasks/20261015_1011/events?page=3 HTTP/1.1" 200 22518 2.841
10.20.3.41 - - [15/Oct/2026:10:06:51 +0800] "GET /api/v1/rules HTTP/1.1" 200 8317 0.096
10.20.3.18 - - [15/Oct/2026:10:12:04 +0800] "GET /api/v1/tasks/20261015_1011 HTTP/1.1" 200 4096 0.133
`,
  },
  'f-errorlog': {
    lang: 'log',
    content: `2026/10/15 10:05:33 [error] 1180#1180: *4123 upstream timed out (110: Connection timed out) while reading response header from upstream, client: 10.20.4.7, request: "GET /api/v1/perf/summary HTTP/1.1", upstream: "http://127.0.0.1:8000/api/v1/perf/summary"
2026/10/15 10:06:12 [warn] 1180#1180: *4128 an upstream response is buffered to a temporary file /var/cache/nginx/proxy_temp/9/01/0000000019
2026/10/15 10:06:18 [error] 1180#1180: *4128 upstream timed out (110: Connection timed out) while reading response header from upstream, client: 10.20.4.7, request: "GET /api/v1/perf/summary HTTP/1.1"
2026/10/15 10:08:12 [error] 1185#1185: *4201 connect() failed (113: No route to host) while connecting to upstream, upstream: "http://127.0.0.1:8000/api/v1/health"
2026/10/15 10:12:00 [notice] 1180#1180: signal process started
`,
  },
  'f-parsedmsg': {
    lang: 'log',
    content: `# 解析产物: task=20261015_1011 · parser=linux-syslog · shard=2/3 · 生成于 10:12:00
# 字段: ts | level | source | rule | message
2026-10-15T10:05:33+08:00 | ERROR | kernel | - | Out of memory: Killed process 6821 (loginsight-parser)
2026-10-15T10:05:47+08:00 | WARN  | loginsight-worker | E-TIMEOUT-504 | rule timeout elapsed=6412ms shard=2
2026-10-15T10:06:02+08:00 | ERROR | loginsight-worker | E-REGEX-BACKTRACK | line_len=71204 pattern=^(\S+\s+){2,}\S+$
2026-10-15T10:06:18+08:00 | ERROR | nginx | E-UPSTREAM-504 | upstream timed out reading response header
2026-10-15T10:07:05+08:00 | ERROR | systemd | - | loginsight-parser.service killed, restart counter 3
2026-10-15T10:08:12+08:00 | WARN  | kernel | E-SYN-FLOOD | Possible SYN flooding on port 8000
2026-10-15T10:10:44+08:00 | ERROR | loginsight-worker | E-TIMEOUT-504 | shard=2 failed after 30s

# 汇总: rules_hit=17 · error=5 · warn=3 · 最长单行=71204 bytes
`,
  },
  'f-parse': {
    lang: 'json',
    content: `{
  "task_id": "20261015_1011",
  "status": "partial",
  "parser": "linux-syslog",
  "rules_hit": 17,
  "shards": [
    { "id": 1, "events": 1284, "elapsed_ms": 842, "status": "done" },
    { "id": 2, "events": 392, "elapsed_ms": 30041, "status": "timeout" },
    { "id": 3, "events": 1201, "elapsed_ms": 908, "status": "done" }
  ],
  "hot_rules": [
    { "code": "E-TIMEOUT-504", "hit": 6, "avg_ms": 6412 },
    { "code": "E-REGEX-BACKTRACK", "hit": 3, "max_line_len": 71204 },
    { "code": "E-SYN-FLOOD", "hit": 1 }
  ],
  "duration_ms": 622884,
  "generated_at": "2026-10-15T10:12:00+08:00"
}
`,
  },
  'f-sar': {
    lang: 'log',
    content: `Linux 5.10.0-153.12.0.92.oe2203.aarch64 (node-01) 	10/15/2026 	_aarch64_	(64 CPU)

10:00:01        CPU     %user     %nice   %system   %iowait    %steal     %idle
10:10:01        all     41.28      0.00     12.44      1.24      0.00     45.04
10:20:01        all     62.71      0.00     15.62      1.18      0.00     20.49

10:00:01    runq-sz  plist-sz   ldavg-1   ldavg-5  ldavg-15   blocked
10:10:01         12       842      6.28      4.11      3.02         2
10:20:01         21       851      9.84      7.32      5.16         4

10:00:01     kbmemfree   kbavail kbmemused  %memused kbbuffers  kbcached
10:10:01       2841204   5933112   7442188     68.42    128440   3281504
10:20:01        912048   3328512   9371344     86.15    112008   2684200

10:00:01          DEV       tps  rd_sec/s  wr_sec/s  avgrq-sz  avgqu-sz     await     svctm     %util
10:10:01      nvme0n1      8.42     12.80    284.16     70.52      0.04      4.82      0.41      0.35
`,
  },
  'f-eth0': {
    lang: 'ini',
    content: `TYPE=Ethernet
PROXY_METHOD=none
BROWSER_ONLY=no
BOOTPROTO=static
DEFROUTE=yes
IPV4_FAILURE_FATAL=no
NAME=eth0
DEVICE=eth0
ONBOOT=yes
IPADDR=10.20.3.18
PREFIX=24
GATEWAY=10.20.3.1
DNS1=10.20.0.2
DNS2=114.114.114.114
`,
  },
  'f-selinux': {
    lang: 'text',
    content: `# This file controls the state of SELinux on the system.
# SELINUX= can take one of these three values:
#     enforcing - SELinux security policy is enforced.
#     permissive - SELinux prints warnings instead of enforcing.
#     disabled - No SELinux policy is loaded.
SELINUX=permissive
# SELINUXTYPE= can take one of these three values:
#     targeted - Targeted processes are protected,
#     minimum - Modification of targeted policy.
#     mls - Multi Level Security protection.
SELINUXTYPE=targeted
`,
  },
}

const GENERIC_TEXT = (f: Attach) => ({
  kind: 'text' as const,
  lang: 'text',
  content: `# ${f.path} (${f.size ?? '—'})
# 演示数据: 该文件暂无内置内容, 对接后端文件读取接口后展示真实内容。
`,
})

const PLACEHOLDER_IMAGE = svgDataUri(
  '图片预览',
  `<rect x="30" y="70" width="660" height="270" rx="12" fill="#f2f6fd" stroke="#c9d8ee" stroke-dasharray="6 4"/>` +
    `<text x="360" y="205" font-size="14" fill="#6b7c96" text-anchor="middle">演示图片占位</text>`,
)

/* 读取文件内容 (原型: 本地 mock) */
export function readFile(f: Attach): FilePayload {
  const image = IMAGES[f.id]
  if (image) return { kind: 'image', content: image }
  if (IMG_EXT.test(f.name)) return { kind: 'image', content: PLACEHOLDER_IMAGE }
  const text = TEXT_FILES[f.id]
  if (text) return { kind: 'text', content: text.content, lang: text.lang }
  return GENERIC_TEXT(f)
}
