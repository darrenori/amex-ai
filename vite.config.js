import { defineConfig } from 'vite';

// The frontend lives in web/ and builds to dist/, which is what Vercel serves.
// In development /api is proxied to the FastAPI app (npm run dev:api).
//
// API_PORT overrides the backend port, so a second checkout — or a stale server
// someone left on 8000 — does not block the dev loop.
const API_PORT = process.env.API_PORT || '8000';

export default defineConfig({
  root: 'web',
  publicDir: '../public',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    target: 'es2020',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${API_PORT}`,
        changeOrigin: true,
      },
    },
  },
});
