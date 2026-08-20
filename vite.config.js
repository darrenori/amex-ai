import { defineConfig } from 'vite';

// The frontend lives in web/ and builds to dist/, which is what Vercel serves.
// In development /api is proxied to the FastAPI app (npm run dev:api).
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
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
