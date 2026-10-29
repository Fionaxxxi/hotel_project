import { defineConfig } from 'vite';
export default defineConfig({ base: '/admin/', server: { proxy: { '/v1': 'http://127.0.0.1:8000', '/dev': 'http://127.0.0.1:8000' } } });
