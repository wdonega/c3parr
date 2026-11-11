import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { defineConfig } from 'vitest/config'

// O build vai para dentro do pacote Python, que serve a interface (src/c3parr/web/dist).
// Em desenvolvimento, `npm run dev` conversa com o c3parr-web rodando na 8484.
export default defineConfig({
  plugins: [
    react(),
    // PWA: dá para instalar o c3parr como app. O service worker guarda a casca (página, código,
    // fontes latinas, ícones) e os pôsteres; os dados vêm sempre do servidor, nunca do cache.
    VitePWA({
      registerType: 'prompt',
      includeAssets: ['favicon.png', 'logo.png', 'apple-touch-icon.png'],
      manifest: {
        name: 'c3parr',
        short_name: 'c3parr',
        description: 'Subtitles for the *arr stack: sync, transcribe and translate with context',
        lang: 'en',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        background_color: '#14111b',
        theme_color: '#14111b',
        icons: [
          { src: 'pwa-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,png}', 'assets/*-latin-[0-9]*.woff2'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/static\//, /^\/health$/],
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith('/api/v1/poster/'),
            handler: 'CacheFirst',
            options: {
              cacheName: 'posters',
              expiration: { maxEntries: 600, maxAgeSeconds: 30 * 24 * 3600 },
            },
          },
          {
            urlPattern: ({ url }) => url.pathname.startsWith('/assets/'),
            handler: 'CacheFirst',
            options: { cacheName: 'assets' },
          },
        ],
      },
    }),
  ],
  build: { outDir: '../src/c3parr/web/dist', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8484' } },
  test: { environment: 'jsdom' },
})
