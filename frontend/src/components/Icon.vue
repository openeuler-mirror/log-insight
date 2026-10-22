<script setup lang="ts">
/* 统一线性图标: 只使用 SVG, 禁止用 emoji 当图标 */
export type IconName =
  | 'history' | 'file' | 'folder' | 'folder-open' | 'image' | 'archive'
  | 'bell' | 'pencil' | 'trash' | 'alert' | 'chart' | 'search' | 'clipboard'
  | 'activity' | 'refresh' | 'chevron-left' | 'chevron-right' | 'plus'
  | 'close' | 'lock' | 'users' | 'play' | 'check' | 'x' | 'disk' | 'bolt'
  | 'hourglass' | 'star' | 'arrow-left' | 'arrow-right'
  | 'caret-down' | 'caret-right' | 'circle'

const props = withDefaults(defineProps<{ name: IconName; size?: number }>(), { size: 14 })

const PATHS: Record<IconName, string[]> = {
  history: [
    'M3 12a9 9 0 1 0 2.6-6.4L3 8',
    'M3 3v5h5',
    'M12 7v5l3 2',
  ],
  file: [
    'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z',
    'M14 2v6h6',
  ],
  folder: ['M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'],
  'folder-open': [
    'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H6.5L3 19z',
    'M6.5 10H21l-2.2 8.1a2 2 0 0 1-1.9 1.4H4.4a2 2 0 0 1-1.9-2.6z',
  ],
  image: [
    'M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z',
    'M8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z',
    'm21 16-5-5-9 9',
  ],
  archive: [
    'M3 4h18v4H3z',
    'M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8',
    'M10 12h4',
  ],
  bell: [
    'M18 8a6 6 0 1 0-12 0c0 7-3 8-3 8h18s-3-1-3-8',
    'M10.3 21a1.9 1.9 0 0 0 3.4 0',
  ],
  pencil: [
    'M12 20h9',
    'M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
  ],
  trash: [
    'M3 6h18',
    'M8 6V4h8v2',
    'M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6',
    'M10 11v6M14 11v6',
  ],
  alert: [
    'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
    'M12 9v4',
    'M12 17h.01',
  ],
  chart: [
    'M3 3v18h18',
    'M7 15v-4M12 17V8M17 13V6',
  ],
  search: [
    'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z',
    'm20 20-3.5-3.5',
  ],
  clipboard: [
    'M9 2h6v4H9z',
    'M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2',
  ],
  activity: ['M22 12h-4l-3 9L9 3l-3 9H2'],
  refresh: [
    'M21 12a9 9 0 1 1-2.6-6.4',
    'M21 3v6h-6',
  ],
  'chevron-left': ['M15 5 8 12l7 7'],
  'chevron-right': ['M9 5l7 7-7 7'],
  plus: ['M12 5v14M5 12h14'],
  close: ['M18 6 6 18M6 6l12 12'],
  lock: [
    'M5 11h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z',
    'M8 11V7a4 4 0 0 1 8 0v4',
  ],
  users: [
    'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2',
    'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
    'M22 21v-2a4 4 0 0 0-3-3.9',
    'M16 3.1a4 4 0 0 1 0 7.8',
  ],
  play: ['M7 4l13 8-13 8z'],
  check: ['M20 6 9 17l-5-5'],
  x: ['M18 6 6 18M6 6l12 12'],
  disk: [
    'M4 4h12l4 4v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z',
    'M7 4v6h9V4',
    'M7 21v-7h10v7',
  ],
  bolt: ['M13 2 4 14h7l-1 8 9-12h-7z'],
  hourglass: [
    'M6 2h12',
    'M6 22h12',
    'M7 2v4l5 6 5-6V2',
    'M7 22v-4l5-6 5 6v4',
  ],
  star: ['M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9z'],
  'arrow-left': ['M19 12H5', 'M12 19l-7-7 7-7'],
  'arrow-right': ['M5 12h14', 'M12 5l7 7-7 7'],
  'caret-down': ['M6 9l6 6 6-6'],
  'caret-right': ['M9 6l6 6-6 6'],
  circle: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z'],
}
</script>

<template>
  <svg
    :width="props.size"
    :height="props.size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(d, i) in PATHS[props.name]" :key="i" :d="d" />
  </svg>
</template>

<style scoped>
svg {
  flex: none;
  display: block;
}
</style>
