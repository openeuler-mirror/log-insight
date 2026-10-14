<script setup lang="ts">
/* blktrace 结果分析: 上传 blkparse 文本输出, 展示 IOPS/吞吐/延迟分布与慢 IO */
import { computed, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { post, apiError } from '@/api/http'
import ChartBase from '@/components/ChartBase.vue'

interface BlkResult {
  format: string
  recordCount: number
  opCount: number
  readCount: number
  writeCount: number
  totalBytes: number
  durationS: number
  iops: number
  throughputMbps: number
  latency: { count: number; avgMs: number; p50Ms: number; p90Ms: number; p99Ms: number; maxMs: number }
  serviceMs: { count: number; avgMs: number; p99Ms: number }
  devices: { dev: string; r: number; w: number; ops: number; bytes: number }[]
  latencyHistogram: { bucket: string; count: number }[]
  sizeHistogram: { bucket: string; count: number }[]
  timeline: { t: number; ops: number; bytes: number; avgLatencyMs: number }[]
  slowIos: { dev: string; sector: number; bytes: number; rw: string; latencyMs: number; time: number }[]
  warnings: string[]
}

const fileInput = ref<HTMLInputElement>()
const fileName = ref('')
const loading = ref(false)
const err = ref('')
const result = ref<BlkResult | null>(null)

function fmtBytes(n: number): string {
  if (!n) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`
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
    result.value = await post<BlkResult>('/perf/blktrace/analyze', fd)
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

const latencyHistOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 48, right: 24, top: 24, bottom: 36 },
  xAxis: { type: 'category', data: (result.value?.latencyHistogram ?? []).map((h) => h.bucket), axisLabel: { rotate: 30 } },
  yAxis: { type: 'value', name: '请求数' },
  series: [{
    type: 'bar',
    data: (result.value?.latencyHistogram ?? []).map((h) => h.count),
    itemStyle: { color: (p: any) => (p.dataIndex >= 5 ? '#e5533d' : '#1e6fff') },
  }],
}))

const sizeOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 48, right: 24, top: 24, bottom: 32 },
  xAxis: { type: 'category', data: (result.value?.sizeHistogram ?? []).map((s) => s.bucket) },
  yAxis: { type: 'value', name: '请求数' },
  series: [{ type: 'bar', data: (result.value?.sizeHistogram ?? []).map((s) => s.count), itemStyle: { color: '#00b365' } }],
}))

const timelineOption = computed<EChartsOption>(() => {
  const tl = result.value?.timeline ?? []
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['IOPS', '平均延迟'], top: 0 },
    grid: { left: 56, right: 64, top: 36, bottom: 40 },
    xAxis: { type: 'value', name: '秒' },
    yAxis: [
      { type: 'value', name: 'IOPS' },
      { type: 'value', name: 'ms', splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside' }],
    series: [
      { name: 'IOPS', type: 'bar', data: tl.map((d) => [d.t, d.ops]), itemStyle: { color: '#1e6fff' } },
      { name: '平均延迟', type: 'line', showSymbol: false, yAxisIndex: 1, lineStyle: { color: '#e5533d', width: 2 },
        data: tl.map((d) => [d.t, d.avgLatencyMs]) },
    ],
  }
})
</script>

<template>
  <div class="o-operate-bar">
    <div class="o-operate-left">
      <h2>blktrace 分析</h2>
      <span class="o-mono o-text-3">输入 blkparse 文本输出（如 blkparse -i sdb.blktrace），按 Q→C 统计请求延迟、D→C 统计设备服务时间</span>
    </div>
    <div class="o-operate-right">
      <input ref="fileInput" type="file" accept=".txt,.log,.blktrace,.csv,.out" style="display: none" @change="onFile" />
      <button class="o-btn o-btn--primary" :disabled="loading" @click="fileInput?.click()">
        {{ loading ? '解析中…' : '选择 blktrace 文件并分析' }}
      </button>
      <button v-if="result" class="o-btn" @click="reset">清空</button>
    </div>
  </div>

  <div v-if="err" class="o-card" style="border-color: var(--danger)">
    <span class="o-mono" style="color: var(--danger)">⚠ {{ err }}</span>
  </div>

  <template v-if="result">
    <div class="kpis">
      <div class="o-card kpi"><div class="kpi-label">IO 请求</div><div class="kpi-value">{{ result.opCount.toLocaleString() }}</div></div>
      <div class="o-card kpi"><div class="kpi-label">读 / 写</div><div class="kpi-value">{{ result.readCount }} / {{ result.writeCount }}</div></div>
      <div class="o-card kpi"><div class="kpi-label">吞吐</div><div class="kpi-value">{{ result.throughputMbps }} MB/s</div></div>
      <div class="o-card kpi"><div class="kpi-label">IOPS</div><div class="kpi-value">{{ result.iops.toLocaleString() }}</div></div>
      <div class="o-card kpi"><div class="kpi-label">平均延迟</div><div class="kpi-value">{{ result.latency.avgMs }} ms</div></div>
      <div class="o-card kpi"><div class="kpi-label">P99 延迟</div><div class="kpi-value" :style="{ color: result.latency.p99Ms > 50 ? 'var(--danger)' : undefined }">{{ result.latency.p99Ms }} ms</div></div>
    </div>

    <div class="o-card" style="padding: 12px 16px">
      <span class="o-mono o-text-3">
        总数据量 {{ fmtBytes(result.totalBytes) }} · 时长 {{ result.durationS.toFixed(3) }}s ·
        延迟 P50 {{ result.latency.p50Ms }}ms / P90 {{ result.latency.p90Ms }}ms / Max {{ result.latency.maxMs }}ms ·
        配对请求 {{ result.latency.count }} · 设备 {{ result.devices.length }}
      </span>
    </div>

    <div v-for="w in result.warnings" :key="w" class="o-mono o-text-3" style="margin: 8px 0">ℹ {{ w }}</div>

    <div class="o-card">
      <div class="o-card__title">IO 时序 <span class="o-mono o-text-3">{{ fileName }}</span></div>
      <ChartBase :option="timelineOption" height="320px" />
    </div>

    <div class="grid2">
      <div class="o-card">
        <div class="o-card__title">延迟分布 <span class="o-mono o-text-3">≥5ms 标红</span></div>
        <ChartBase :option="latencyHistOption" height="300px" />
      </div>
      <div class="o-card">
        <div class="o-card__title">IO 大小分布</div>
        <ChartBase :option="sizeOption" height="300px" />
      </div>
    </div>

    <div class="grid2">
      <div class="o-card">
        <div class="o-card__title">设备维度</div>
        <table class="o-table">
          <thead><tr><th>设备</th><th>读</th><th>写</th><th>总量</th></tr></thead>
          <tbody>
            <tr v-for="d in result.devices" :key="d.dev">
              <td class="o-mono">{{ d.dev }}</td><td>{{ d.r }}</td><td>{{ d.w }}</td><td>{{ fmtBytes(d.bytes) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="o-card">
        <div class="o-card__title">Top 慢 IO <span class="o-mono o-text-3">按延迟排序</span></div>
        <table class="o-table">
          <thead><tr><th>时间(s)</th><th>设备</th><th>扇区</th><th>大小</th><th>R/W</th><th>延迟(ms)</th></tr></thead>
          <tbody>
            <tr v-for="(s, i) in result.slowIos" :key="i">
              <td class="o-mono">{{ s.time }}</td>
              <td class="o-mono">{{ s.dev }}</td>
              <td class="o-mono">{{ s.sector }}</td>
              <td>{{ fmtBytes(s.bytes) }}</td>
              <td>{{ s.rw }}</td>
              <td :style="{ color: s.latencyMs > 50 ? 'var(--danger)' : undefined }">{{ s.latencyMs }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </template>

  <div v-else-if="!loading && !err" class="o-card">
    <div class="o-empty" style="padding: 80px 0">
      <div class="icon">💽</div>
      <div>选择 blktrace 文件开始分析 — 解析结果不落库，仅本次展示</div>
    </div>
  </div>
</template>

<style scoped>
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