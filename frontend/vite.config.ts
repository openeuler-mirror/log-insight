import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 33480, // 本机 8000/5180 易冲突或外网不通，开发用高端口
    open: false,
    allowedHosts: true,
    proxy: {
      // 后端 API (uvicorn :8001); 本机 8000 已被占用
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        // SSE: 响应直通, 不缓冲 (EventSource / llm 打字机依赖)
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache'
              proxyRes.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
    },
  },
})
