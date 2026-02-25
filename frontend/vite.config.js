import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // During development proxy API requests to the backend
      '/api': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
      // OAuth endpoints need to hit backend as well (otherwise Vite returns index.html)
      '/oauth': 'http://localhost:8000'
    }
  }
});