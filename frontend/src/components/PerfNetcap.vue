<script setup lang="ts">
/* 网络抓包结果分析: 上传 pcap 原始文件或 tshark 字段导出, 后端解析后展示统计与图表 */
import { computed, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { post, apiError } from '@/api/http'
import ChartBase from '@/components/ChartBase.vue'
import Icon from '@/components/Icon.vue'

interface Proto { name: string; packets: number; bytes: number }
interface Host { host: string; packets: number; bytes: number }
interface Conv { src: string; sport: number; dst: string; dport: number; proto: string; packets: number; bytes: number }
interface NetcapResult {
  format: string
  packetCount: number
  byteCount: number
  durationS: number
  pps: number
  bps: number
  protocols: Proto[]
  topSrc: Host[]
  topDst: Host[]
  conversations: Conv[]
  sizeHistogram: { bucket: string; count: number }[]
  timeline: { t: number; packets: number; bytes: number }[]
  warnings: string[]
}

const fileInput = ref<HTMLInputElement>()
const fileName = ref('')
const loading = ref(false)
const err = ref('')
const result = ref<NetcapResult | null>(null)

function fmtBytes(n: number): string {
  if (!n) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`
}
function fmtRate(bps: number): string {
  const u = ['bps', 'Kbps', 'Mbps', 'Gbps']
  let i = 0
  let v = bps
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++ }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 2)} ${u[i]}`
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
    result.value = await post<NetcapResult>('/perf/netcap/analyze', fd)
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

const protoOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'item', formatter: '{b}<br/>占比 {d}%' },
  legend: { type: 'scroll', bottom: 0 },
  series: [{
    type: 'pie',
    radius: ['42%', '68%'],
    avoidLabelOverlap: true,
    label: { formatter: '{b} {d}%' },
    data: (result.value?.protocols ?? []).map((p) => ({ name: p.name, value: p.bytes })),
  }],
}))

const topSrcOption = computed<EChartsOption>(() => {
  const rows = [...(result.value?.topSrc ?? [])].slice(0, 10).reverse()
  return {
    tooltip: { trigger: 'axis', axis: 'y' },
    grid: { left: 120, right: 24, top: 12, bottom: 24 },
    xAxis: { type: 'value', name: '字节' },
    yAxis: { type: 'category', data: rows.map((r) => r.host) },
    series: [{
      type: 'bar', data: rows.map((r) => r.bytes), itemStyle: { color: '#1e6fff' },
      label: { show: true, position: 'right', formatter: (p: any) => fmtBytes(p.value) },
    }],
  }
})

const timelineOption = computed<EChartsOption>(() => {
  const tl = result.value?.timeline ?? []
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['包速率', '字节速率'], top: 0 },
    grid: { left: 64, right: 64, top: 36, bottom: 40 },
    xAxis: { type: 'time' },
    yAxis: [
      { type: 'value', name: 'pps' },
      { type: 'value', name: 'B/s', splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside' }],
    series: [
      { name: '包速率', type: 'line', showSymbol: false, data: tl.map((d) => [d.t * 1000, d.packets]) },
      { name: '字节速率', type: 'line', showSymbol: false, yAxisIndex: 1, areaStyle: { opacity: 0.08 },
        data: tl.map((d) => [d.t * 1000, d.bytes]) },
    ],
  }
})

const sizeOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 48, right: 24, top: 24, bottom: 32 },
  xAxis: { type: 'category', data: (result.value?.sizeHistogram ?? []).map((s) => s.bucket) },
  yAxis: { type: 'value', name: '包数' },
  series: [{ type: 'bar', data: (result.value?.sizeHistogram ?? []).map((s) => s.count), itemStyle: { color: '#00b365' } }],
}))
</script>

<template>
  <div class="o-operate-bar">
    <div class="o-operate-left">
      <h2>网络抓包分析</h2>
      <span class="o-mono o-text-3">支持 .pcap 原始文件，或 tshark 字段导出文本（TSV/CSV，含表头或按 时间/源/目的/协议/长度/源端口/目的端口 顺序）</span>
    </div>
    <div class="o-operate-right">
      <input ref="fileInput" type="file" accept=".pcap,.pcapng,.cap,.txt,.csv,.tsv,.log" style="display: none" @change="onFile" />
      <button class="o-btn o-btn--primary" :disabled="loading" @click="fileInput?.click()">
        {{ loading ? '解析中…' : '选择抓包文件并分析' }}
      </button>
      <button v-if="result" class="o-btn" @click="reset">清空</button>
    </div>
  </div>

  <div v-if="err" class="o-card" style="border-color: var(--danger)">
    <span class="o-mono err-line"><Icon name="alert" :size="13" /> {{ err }}</span>
  </div>

  <template v-if="result">
    <div class="kpis">
      <div class="o-card kpi"><div class="kpi-label">数据包</div><div class="kpi-value">{{ result.packetCount.toLocaleString() }}</div></div>
      <div class="o-card kpi"><div class="kpi-label">总流量</div><div class="kpi-value">{{ fmtBytes(result.byteCount) }}</div></div>
      <div class="o-card kpi"><div class="kpi-label">时长</div><div class="kpi-value">{{ result.durationS.toFixed(2) }} s</div></div>
      <div class="o-card kpi"><div class="kpi-label">平均包速率</div><div class="kpi-value">{{ result.pps.toLocaleString() }} pps</div></div>
      <div class="o-card kpi"><div class="kpi-label">平均带宽</div><div class="kpi-value">{{ fmtRate(result.bps) }}</div></div>
      <div class="o-card kpi"><div class="kpi-label">协议数</div><div class="kpi-value">{{ result.protocols.length }}</div></div>
    </div>

    <div v-for="w in result.warnings" :key="w" class="o-mono o-text-3" style="margin: 8px 0">ℹ {{ w }}</div>

    <div class="grid2">
      <div class="o-card">
        <div class="o-card__title">协议分布（按字节） <span class="o-mono o-text-3">{{ result.format }}</span></div>
        <ChartBase :option="protoOption" height="300px" />
      </div>
      <div class="o-card">
        <div class="o-card__title">包长分布</div>
        <ChartBase :option="sizeOption" height="300px" />
      </div>
    </div>

    <div class="o-card">
      <div class="o-card__title">流量时序 <span class="o-mono o-text-3">{{ fileName }}</span></div>
      <ChartBase :option="timelineOption" height="320px" />
    </div>

    <div class="grid2">
      <div class="o-card">
        <div class="o-card__title">Top 源地址（按字节）</div>
        <ChartBase :option="topSrcOption" height="320px" />
      </div>
      <div class="o-card">
        <div class="o-card__title">Top 目的地址</div>
        <table class="o-table">
          <thead><tr><th>地址</th><th>包数</th><th>字节</th></tr></thead>
          <tbody>
            <tr v-for="r in result.topDst" :key="r.host">
              <td class="o-mono">{{ r.host }}</td><td>{{ r.packets.toLocaleString() }}</td><td>{{ fmtBytes(r.bytes) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="o-card">
      <div class="o-card__title">会话 Top（按字节）</div>
      <table class="o-table">
        <thead><tr><th>源 → 目的</th><th>协议</th><th>包数</th><th>字节</th></tr></thead>
        <tbody>
          <tr v-for="(c, i) in result.conversations" :key="i">
            <td class="o-mono">{{ c.src }}:{{ c.sport }} → {{ c.dst }}:{{ c.dport }}</td>
            <td><span class="o-badge o-badge--outline">{{ c.proto }}</span></td>
            <td>{{ c.packets.toLocaleString() }}</td>
            <td>{{ fmtBytes(c.bytes) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>

  <div v-else-if="!loading && !err" class="o-card">
    <div class="o-empty" style="padding: 80px 0">
      <div class="icon"><Icon name="search" :size="30" /></div>
      <div>选择抓包文件开始分析 — 解析结果不落库，仅本次展示</div>
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