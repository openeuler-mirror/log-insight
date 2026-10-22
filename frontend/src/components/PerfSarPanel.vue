<script setup lang="ts">
/* sar 性能分析: 上传 sar 文本输出 (-u/-r/-d)，后端解析后展示 CPU/内存/磁盘图表 */
import { computed, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { post, apiError } from '@/api/http'
import ChartBase from '@/components/ChartBase.vue'
import Icon from '@/components/Icon.vue'

interface SarCpuPoint {
  t: number; clock: string
  user: number; nice: number; system: number; iowait: number; steal: number; idle: number; irq: number
}
interface SarMemPoint {
  t: number; clock: string
  memFreeKb: number; memAvailKb: number | null; memUsedKb: number; memUsedPct: number
  buffersKb: number; cachedKb: number
}
interface SarDiskPoint {
  t: number; clock: string; dev: string
  tps: number; readKbps: number; writeKbps: number
  avgrqSz: number; avgquSz: number; awaitMs: number; svctmMs: number; utilPct: number
}
interface SarCpu {
  points: SarCpuPoint[]
  avg: Record<string, number>
  peak: { busy: number; busyClock: string; iowait: number }
  perCpu: { cpu: string; avgBusy: number; avgIdle: number; pointCount: number }[]
  pointsTotal: number; pointsReturned: number; thinned: boolean
}
interface SarMem {
  points: SarMemPoint[]
  avg: Record<string, number>
  peak: { memUsedPct: number; memUsedPctClock: string; memUsedKb: number }
  memTotalKb: number; pointsTotal: number; pointsReturned: number; thinned: boolean
}
interface SarDiskDevice {
  dev: string; pointCount: number
  avgTps: number; avgReadKbps: number; avgWriteKbps: number
  avgAwaitMs: number; maxAwaitMs: number; avgUtilPct: number; maxUtilPct: number
  totalReadKb: number; totalWriteKb: number
}
interface SarDisk {
  devices: SarDiskDevice[]
  series: { dev: string; points: SarDiskPoint[] }[]
  totals: Record<string, any>
  throughputUnit: string; deviceTotal: number; seriesDevices: number
}
interface SarResult {
  format: string; host: string; kernel: string; date: string; cpuCount: number
  intervalS: number; durationS: number; startClock: string; endClock: string; views: string[]
  cpu?: SarCpu
  mem?: SarMem
  disk?: SarDisk
  warnings: string[]
}

const fileInput = ref<HTMLInputElement>()
const fileName = ref('')
const loading = ref(false)
const err = ref('')
const result = ref<SarResult | null>(null)

function fmtKb(kb: number): string {
  if (!kb && kb !== 0) return '—'
  const u = ['KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = kb
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`
}
function num(v: number | undefined, d = 2): string {
  return v === undefined || v === null ? '—' : v.toFixed(d)
}

async function onFile(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (!f) return
  fileName.value = f.name
  err.value = ''
  loading.value = true
  result.value = null
  try {
    const fd = new FormData()
    fd.append('file', f)
    result.value = await post<SarResult>('/perf/sar/analyze', fd)
  } catch (e2) {
    err.value = apiError(e2)
  } finally {
    loading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

function reset() {
  result.value = null
  fileName.value = ''
  err.value = ''
}

/* ---- CPU: 堆叠面积 ---- */
const cpuOption = computed<EChartsOption>(() => {
  const pts = result.value?.cpu?.points ?? []
  const mk = (name: string, key: keyof SarCpuPoint, color: string) => ({
    name,
    type: 'line' as const,
    stack: 'cpu',
    areaStyle: { opacity: 0.75 },
    showSymbol: false,
    smooth: false,
    lineStyle: { width: 0.5, color },
    itemStyle: { color },
    data: pts.map((p) => p[key] as number),
  })
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => `${Number(v).toFixed(2)}%` },
    legend: { type: 'scroll', top: 0 },
    grid: { left: 56, right: 24, top: 36, bottom: 40 },
    xAxis: { type: 'category', data: pts.map((p) => p.clock) },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
    dataZoom: [{ type: 'inside' }],
    series: [
      mk('用户', 'user', '#1e6fff'),
      mk('系统', 'system', '#00b365'),
      mk('iowait', 'iowait', '#e5533d'),
      mk('steal', 'steal', '#f5a623'),
      mk('irq', 'irq', '#9b59b6'),
    ],
  }
})

/* ---- 内存: 双轴 (左 % / 右 GB) ---- */
const memOption = computed<EChartsOption>(() => {
  const pts = result.value?.mem?.points ?? []
  const toGb = (kb: number) => kb / 1024 / 1024
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['使用率', '已用', '空闲'], top: 0 },
    grid: { left: 56, right: 64, top: 36, bottom: 40 },
    xAxis: { type: 'category', data: pts.map((p) => p.clock) },
    yAxis: [
      { type: 'value', name: '%', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
      { type: 'value', name: 'GB', min: 0, splitLine: { show: false }, axisLabel: { formatter: (v: number) => v.toFixed(1) } },
    ],
    dataZoom: [{ type: 'inside' }],
    series: [
      { name: '使用率', type: 'line', showSymbol: false, lineStyle: { width: 2, color: '#e5533d' }, itemStyle: { color: '#e5533d' }, data: pts.map((p) => p.memUsedPct) },
      { name: '已用', type: 'line', yAxisIndex: 1, showSymbol: false, areaStyle: { opacity: 0.08 }, itemStyle: { color: '#1e6fff' }, data: pts.map((p) => toGb(p.memUsedKb)) },
      { name: '空闲', type: 'line', yAxisIndex: 1, showSymbol: false, itemStyle: { color: '#00b365' }, data: pts.map((p) => toGb(p.memFreeKb)) },
    ],
  }
})

/* ---- 磁盘: 多设备折线 + 指标切换 ---- */
type DiskMetric = 'utilPct' | 'tps' | 'awaitMs' | 'throughput'
const METRIC_LABEL: Record<DiskMetric, string> = {
  utilPct: '%util', tps: 'tps', awaitMs: 'await(ms)', throughput: '吞吐(MB/s)',
}
const DISK_PALETTE = ['#1e6fff', '#00b365', '#e5533d', '#f5a623', '#9b59b6', '#00c2c7', '#ff8f1f', '#8e44ad']
const diskMetric = ref<DiskMetric>('utilPct')

function diskVal(p: SarDiskPoint, m: DiskMetric): number {
  if (m === 'throughput') return (p.readKbps + p.writeKbps) / 1024
  return p[m] as number
}

const diskOption = computed<EChartsOption>(() => {
  const seriesData = result.value?.disk?.series ?? []
  const m = diskMetric.value
  return {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 0, data: seriesData.map((s) => s.dev) },
    grid: { left: 56, right: 24, top: 36, bottom: 40 },
    xAxis: { type: 'category', data: (seriesData[0]?.points ?? []).map((p) => p.clock) },
    yAxis: { type: 'value', name: METRIC_LABEL[m] },
    dataZoom: [{ type: 'inside' }],
    series: seriesData.map((s, i) => ({
      name: s.dev,
      type: 'line' as const,
      showSymbol: false,
      itemStyle: { color: DISK_PALETTE[i % DISK_PALETTE.length] },
      data: s.points.map((p) => diskVal(p, m)),
    })),
  }
})
</script>

<template>
  <div class="o-operate-bar">
    <div class="o-operate-left">
      <h2>sar 性能分析</h2>
      <span class="o-mono o-text-3">支持 sar -u / -r / -d 文本输出（推荐 `LC_ALL=C sar -u -r -d 1 10 &gt; sar.txt`）</span>
    </div>
    <div class="o-operate-right">
      <input ref="fileInput" type="file" accept=".sar,.txt,.log,.out,.csv" style="display: none" @change="onFile" />
      <button class="o-btn o-btn--primary" :disabled="loading" @click="fileInput?.click()">
        {{ loading ? '解析中…' : '选择 sar 文件并分析' }}
      </button>
      <button v-if="result" class="o-btn" @click="reset">清空</button>
    </div>
  </div>

  <div v-if="err" class="o-card" style="border-color: var(--danger)">
    <span class="o-mono err-line"><Icon name="alert" :size="13" /> {{ err }}</span>
  </div>

  <template v-if="result">
    <div class="kpis">
      <div class="o-card kpi">
        <div class="kpi-label">CPU 平均使用率</div>
        <div class="kpi-value">{{ num(result.cpu?.avg.busy) }}%</div>
      </div>
      <div class="o-card kpi">
        <div class="kpi-label">CPU 峰值使用率</div>
        <div class="kpi-value">{{ num(result.cpu?.peak.busy) }}%</div>
      </div>
      <div class="o-card kpi">
        <div class="kpi-label">内存平均使用率</div>
        <div class="kpi-value">{{ num(result.mem?.avg.memUsedPct) }}%</div>
      </div>
      <div class="o-card kpi">
        <div class="kpi-label">内存峰值使用率</div>
        <div class="kpi-value">{{ num(result.mem?.peak.memUsedPct) }}%</div>
      </div>
      <div class="o-card kpi">
        <div class="kpi-label">磁盘设备数</div>
        <div class="kpi-value">{{ result.disk?.deviceTotal ?? 0 }}</div>
      </div>
      <div class="o-card kpi">
        <div class="kpi-label">最高 %util</div>
        <div class="kpi-value">{{ num(result.disk?.totals.maxUtilPct) }}%</div>
      </div>
    </div>

    <div class="o-card">
      <div class="o-mono o-text-3">
        {{ result.host || '-' }} · {{ result.kernel || '-' }} · {{ result.date || '-' }} ·
        {{ result.cpuCount || '-' }} CPU · 间隔 {{ result.intervalS }}s · 时长 {{ result.durationS }}s ·
        视图 {{ result.views.join(' / ') }} <span v-if="fileName"> · {{ fileName }}</span>
      </div>
    </div>

    <div v-for="w in result.warnings" :key="w" class="o-mono o-text-3" style="margin: 8px 0">ℹ {{ w }}</div>

    <div v-if="result.cpu?.points.length" class="o-card">
      <div class="o-card__title">
        CPU 使用率（堆叠）
        <span v-if="result.cpu.thinned" class="o-mono o-text-3">已抽稀至 {{ result.cpu.pointsReturned }}/{{ result.cpu.pointsTotal }} 点</span>
      </div>
      <ChartBase :option="cpuOption" height="340px" />
    </div>

    <div class="grid2">
      <div v-if="result.mem?.points.length" class="o-card">
        <div class="o-card__title">
          内存使用率与容量
          <span class="o-mono o-text-3">总量 {{ fmtKb(result.mem.memTotalKb) }}</span>
        </div>
        <ChartBase :option="memOption" height="320px" />
      </div>
      <div v-if="result.disk?.series.length" class="o-card">
        <div class="o-card__title">
          磁盘（{{ METRIC_LABEL[diskMetric] }}）
          <select v-model="diskMetric" class="o-select" style="max-width: 140px">
            <option value="utilPct">%util</option>
            <option value="tps">tps</option>
            <option value="awaitMs">await(ms)</option>
            <option value="throughput">吞吐(MB/s)</option>
          </select>
        </div>
        <ChartBase :option="diskOption" height="320px" />
      </div>
    </div>

    <div v-if="result.disk?.devices.length" class="o-card">
      <div class="o-card__title">
        磁盘设备汇总
        <span class="o-mono o-text-3">吞吐口径 {{ result.disk.throughputUnit }}</span>
      </div>
      <table class="o-table">
        <thead>
          <tr>
            <th>设备</th><th>平均 tps</th><th>读 KB/s</th><th>写 KB/s</th>
            <th>平均 await(ms)</th><th>最大 await(ms)</th><th>平均 %util</th><th>最大 %util</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in result.disk.devices" :key="d.dev">
            <td class="o-mono">{{ d.dev }}</td>
            <td>{{ num(d.avgTps) }}</td>
            <td>{{ num(d.avgReadKbps) }}</td>
            <td>{{ num(d.avgWriteKbps) }}</td>
            <td :style="d.avgAwaitMs > 50 ? 'color: var(--danger)' : ''">{{ num(d.avgAwaitMs) }}</td>
            <td :style="d.maxAwaitMs > 50 ? 'color: var(--danger)' : ''">{{ num(d.maxAwaitMs) }}</td>
            <td :style="d.avgUtilPct > 80 ? 'color: var(--danger)' : ''">{{ num(d.avgUtilPct) }}</td>
            <td :style="d.maxUtilPct > 80 ? 'color: var(--danger)' : ''">{{ num(d.maxUtilPct) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="result.cpu?.perCpu.length" class="o-card">
      <div class="o-card__title">per-CPU 摘要</div>
      <table class="o-table">
        <thead><tr><th>CPU</th><th>平均使用率</th><th>平均 idle</th><th>采样点</th></tr></thead>
        <tbody>
          <tr v-for="c in result.cpu.perCpu" :key="c.cpu">
            <td class="o-mono">{{ c.cpu }}</td>
            <td>{{ num(c.avgBusy) }}%</td>
            <td>{{ num(c.avgIdle) }}%</td>
            <td>{{ c.pointCount }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>

  <div v-else-if="!loading && !err" class="o-card">
    <div class="o-empty" style="padding: 80px 0">
      <div class="icon"><Icon name="chart" :size="30" /></div>
      <div>选择 sar 文件开始分析 — 支持 sar -u / -r / -d 文本输出；解析结果不落库，仅本次展示</div>
    </div>
  </div>
</template>

<style scoped>
.err-line {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--danger);
}
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.kpi {
  padding: 14px 16px;
}
.kpi-label {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 6px;
}
.kpi-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--primary);
  font-family: var(--mono);
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
@media (max-width: 900px) {
  .grid2 {
    grid-template-columns: 1fr;
  }
}
</style>