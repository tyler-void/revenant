import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// GitHub Pages serves a project site under /<repo>/ — set base accordingly for prod.
// Override with VITE_BASE when deploying elsewhere.
export default defineConfig({
  base: process.env.VITE_BASE ?? '/revenant/',
  plugins: [react(), tailwindcss()],
});
