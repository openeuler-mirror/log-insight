<script setup lang="ts">
/* 中断计数变化分析: 上传 /proc/interrupts 多次快照 或 sar -I 文本输出, 后端自动识别并解析
   - /proc/interrupts 多快照 → 相邻快照各中断源 / 各 CPU 的计数变化量
   - sar -I → 中断速率(中断/秒)时序 */
import { computed, ref } from 'vue'
import type { EChartsOption } from 'echarts'
import { post, apiError } from '@/api/http'
import ChartBase from '@/components/ChartBase.vue'
import Icon from '@/components/Icon.vue'

/* 响应经 http.ts 深度驼峰化, 此处字段均为 camelCase */
interface IrqItem {
  name: string; desc: string; value: number; share: number; cpuCount: number; series: number[]
}
interface IrqCpu {
  cpu: string; value: number; share: number; lastTotal: number
}
interface IrqResult {
  format: string
  unit: 'count' | 'per_s'
  metricLabel: string
  cpuCount: number
  sampleCount: number
  intervalCount: number
  intervalS: number | null
  total: number
  avg: number
  peak: { value: number; at: string }
  perCpu: IrqCpu[]
  top: IrqItem[]
  series: { x: string[]; items: { name: string; data: number[] }[] }
  samples: { label: string; total: number }[]
  warnings: string[]
}

const PALETTE = ['#1e6fff', '#00b365', '#e5533d', '#f5a623', '#9b59b6', '#00c2c7', '#ff8f1f', '#8e44ad']
const FORMAT_LABEL: Record<string, string> = {
  'proc-interrupts': '/proc/interrupts 快照',
  'sar-irq': 'sar -I 输出',
}

const fileInput = ref<HTMLInputElement>()
const fileName = ref('')
const loading = ref(false)
const err = ref('')
const result = ref<IrqResult | null>(null)

function fmt(v: number | undefined): string {
  if (v === undefined || v === null) return '—'
  return v.toLocaleString('en-US', { maximumFractionDigits: 2 })
}
function fmtInt(v: number): string {
  return v.toLocaleString('en-US')
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
    result.value = await post<IrqResult>('/perf/interrupts/analyze', fd)
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

const isRate = computed(() => result.value?.unit === 'per_s')

const kpis = computed(() => {
  const r = result.value
  if (!r) return []
  if (r.unit === 'per_s') {
    return [
      { label: '平均总速率', value: fmt(r.avg), unit: '中断/秒' },
      { label: '峰值速率', value: fmt(r.peak.value), unit: r.peak.at },
      { label: '采样点', value: fmtInt(r.sampleCount), unit: '' },
      { label: '采样间隔', value: r.intervalS ? `${r.intervalS}s` : '—', unit: '' },
      { label: 'CPU 数', value: fmtInt(r.cpuCount), unit: '' },
    ]
  }
  return [
    { label: '中断变化总量', value: fmt(r.total), unit: '次' },
    { label: '平均每区间', value: fmt(r.avg), unit: '次' },
    { label: '峰值区间', value: fmt(r.peak.value), unit: r.peak.at },
    { label: '快照数', value: fmtInt(r.sampleCount), unit: '' },
    { label: 'CPU 数', value: fmtInt(r.cpuCount), unit: '' },
  ]
})

/* 中断变化/速率时序: 变化量为堆叠柱, 速率为堆叠面积 */
const trendOption = computed<EChartsOption>(() => {
  const r = result.value
  if (!r) return {}
  const isBar = r.unit === 'count'
  return {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 0 },
    grid: { left: 68, right: 24, top: 36, bottom: 44 },
    xAxis: { type: 'category', data: r.series.x },
    yAxis: { type: 'value', name: r.metricLabel },
    dataZoom: [{ type: 'inside' }],
    series: r.series.items.map((it, i) => ({
      name: it.name,
      type: isBar ? ('bar' as const) : ('line' as const),
      stack: 'irq',
      showSymbol: false,
      areaStyle: isBar ? undefined : { opacity: 0.25 },
      lineStyle: { width: 1.6 },
      itemStyle: { color: PALETTE[i % PALETTE.length] },
      data: it.data,
    })),
  }
})

const cpuOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'item', valueFormatter: (v: unknown) => fmt(Number(v)) },
  legend: { type: 'scroll', bottom: 0 },
  series: [
    {
      type: 'pie',
      radius: ['46%', '70%'],
      center: ['50%', '46%'],
      itemStyle: { borderColor: '#fff', borderWidth: 1 },
      label: { formatter: '{b} {d}%', fontSize: 11 },
      data: (result.value?.perCpu ?? []).map((c, i) => ({
        name: c.cpu,
        value: c.value,
        itemStyle: { color: PALETTE[i % PALETTE.length] },
      })),
    },
  ],
}))
</script>

<template>
  <div class="o-operate-bar">
    <div class="o-operate-left">
      <h2>中断计数变化</h2>
      <span class="o-mono o-text-3">
        支持 /proc/interrupts 多次快照（`for i in 1 2 3; do cat /proc/interrupts; sleep 5; done &gt; irq.txt`）或
        sar -I 输出（`LC_ALL=C sar -I SUM -P ALL 1 10 &gt; sar_irq.txt`）
      </span>
    </div>
    <div class="o-operate-right">
      <input ref="fileInput" type="file" accept=".txt,.log,.out,.csv" style="display: none" @change="onFile" />
      <button class="o-btn o-btn--primary" :disabled="loading" @click="fileInput?.click()">
        {{ loading ? '解析中…' : '选择中断文件并分析' }}
      </button>
      <button v-if="result" class="o-btn" @click="reset">清空</button>
    </div>
  </div>

  <div v-if="err" class="o-card" style="border-color: var(--danger)">
    <span class="o-mono err-line"><Icon name="alert" :size="13" /> {{ err }}</span>
  </div>

  <template v-if="result">
    <div class="kpis">
      <div v-for="k in kpis" :key="k.label" class="o-card kpi">
        <div class="kpi-label">{{ k.label }}</div>
        <div class="kpi-value">{{ k.value }}</div>
        <div v-if="k.unit" class="kpi-unit o-mono o-text-3">{{ k.unit }}</div>
      </div>
    </div>

    <div class="o-card">
      <div class="o-mono o-text-3">
        格式 {{ FORMAT_LABEL[result.format] || result.format }} · 指标 {{ result.metricLabel }} ·
        采样 {{ result.sampleCount }} 次 / 变化区间 {{ result.intervalCount }} 段 ·
        CPU {{ result.cpuCount }}
        <span v-if="result.intervalS">· 间隔 {{ result.intervalS }}s</span>
        <span v-if="fileName"> · {{ fileName }}</span>
      </div>
    </div>

    <div v-for="w in result.warnings" :key="w" class="o-mono o-text-3" style="margin: 8px 0">ℹ {{ w }}</div>

    <div v-if="result.series.x.length" class="o-card">
      <div class="o-card__title">
        {{ isRate ? '中断速率时序' : '中断计数变化时序' }}
        <span class="o-mono o-text-3">堆叠展示 Top {{ Math.min(result.top.length, 15) }} 中断源</span>
      </div>
      <ChartBase :option="trendOption" height="340px" />
    </div>

    <div class="grid2">
      <div v-if="result.perCpu.length" class="o-card">
        <div class="o-card__title">{{ isRate ? '各 CPU 平均速率占比' : '各 CPU 中断变化占比' }}</div>
        <ChartBase :option="cpuOption" height="300px" />
      </div>

      <div v-if="result.perCpu.length" class="o-card">
        <div class="o-card__title">per-CPU 明细</div>
        <table class="o-table">
          <thead>
            <tr>
              <th>CPU</th>
              <th>{{ result.metricLabel }}</th>
              <th>占比</th>
              <th>末次累计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in result.perCpu" :key="c.cpu">
              <td class="o-mono">{{ c.cpu }}</td>
              <td>{{ fmt(c.value) }}</td>
              <td>{{ c.share }}%</td>
              <td class="o-mono">{{ fmtInt(c.lastTotal) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="result.top.length" class="o-card">
      <div class="o-card__title">
        Top 中断源
        <span class="o-mono o-text-3">{{ isRate ? '按平均速率排序' : '按变化量排序' }}</span>
      </div>
      <table class="o-table">
        <thead>
          <tr>
            <th>中断源</th>
            <th>说明</th>
            <th>{{ result.metricLabel }}</th>
            <th>占比</th>
            <th>列数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in result.top" :key="t.name">
            <td class="o-mono">{{ t.name }}</td>
            <td class="o-text-3">{{ t.desc || '—' }}</td>
            <td>{{ fmt(t.value) }}</td>
            <td>{{ t.share }}%</td>
            <td>{{ t.cpuCount || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="result.samples.length" class="o-card">
      <div class="o-card__title">{{ isRate ? '采样点明细（总速率）' : '快照明细（累计计数）' }}</div>
      <table class="o-table">
        <thead><tr><th>采样点</th><th>{{ isRate ? '总速率（中断/秒）' : '累计计数（次）' }}</th></tr></thead>
        <tbody>
          <tr v-for="s in result.samples" :key="s.label">
            <td class="o-mono">{{ s.label }}</td>
            <td>{{ fmt(s.total) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>

  <div v-else-if="!loading && !err" class="o-card">
    <div class="o-empty" style="padding: 80px 0">
      <div class="icon"><Icon name="bolt" :size="30" /></div>
      <div>选择中断文件开始分析 — 支持 /proc/interrupts 多次快照与 sar -I 输出；解析结果不落库，仅本次展示</div>
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
.kpi-unit {
  font-size: 11px;
  margin-top: 4px;
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
