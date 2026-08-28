import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: 4175,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:18765',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        entryFileNames: 'app.js',
        assetFileNames: (assetInfo) => assetInfo.names?.some((name) => name.endsWith('.css')) ? 'styles.css' : 'assets/[name][extname]',
      },
    },
  },
});
