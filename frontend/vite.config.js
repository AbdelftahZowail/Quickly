import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // During development proxy API requests to the backend.  We only want
      // *JSON* API traffic forwarded; any navigation request for the SPA
      // (Accept: text/html) must fall back to index.html so that the React
      // router can handle routes such as "/unibox".
      '/api': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
      '/oauth': 'http://localhost:8000',

      // unibox endpoints live at the root of the backend.  The simple string
      // proxy would also catch browser reloads, which is why navigating
      // directly to /unibox previously returned the raw JSON response.  Add a
      // rule with a bypass function to serve index.html for HTML requests.
      '/unibox': {
        target: 'http://localhost:8000',
        bypass: (req) => {
          const accept = req.headers.accept || '';
          if (accept.includes('text/html')) {
            // return a path to bypass proxy and serve the SPA entrypoint
            return '/index.html';
          }
          // otherwise, proxy to backend (return undefined)
        },
      },
    },
  }
});
