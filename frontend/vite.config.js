import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // File watching inside Docker on Windows can miss changes because
    // the underlying filesystem notifications don’t propagate across
    // the bind mount.  Enabling polling ensures hot‑reload works.
    watch: {
      usePolling: true,
      interval: 100,
    },

    proxy: {
      // During development proxy API requests to the backend.  The target
      // can be overridden by the VITE_API_URL environment variable which
      // is helpful when running inside Docker compose (the backend lives on a
      // different container and is reachable by its service name).  If no
      // variable is set we fall back to localhost so ordinary `npm run dev`
      // on the host continues to work.
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:8000',
        changeOrigin: true,
      },
      '/oauth': {
        target: process.env.VITE_API_URL || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  }
});
