import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
const backend = {target: 'http://127.0.0.1:8097', changeOrigin: false};
export default defineConfig({cacheDir: '.vite', plugins: [react()], server: {proxy: {'/api': backend, '/v1': backend}}});
