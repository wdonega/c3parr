import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// O build vai para dentro do pacote Python, que serve a interface (src/c3parr/web/dist).
// Em desenvolvimento, `npm run dev` conversa com o c3parr-web rodando na 8484.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../src/c3parr/web/dist', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8484' } },
  test: { environment: 'jsdom' },
})
