import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { viteSingleFile } from 'vite-plugin-singlefile'

// One self-contained HTML file served by FastAPI: no CDN, no asset routes,
// compatible with the dashboard CSP (inline script/style only).
export default defineConfig({
  plugins: [react(), viteSingleFile({ removeViteModuleLoader: true })],
  publicDir: false,
  build: {
    outDir: '../src/manager',
    emptyOutDir: false,
    reportCompressedSize: false,
    rollupOptions: { input: 'dashboard.html' },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['src/test/setup.ts'],
  },
})
