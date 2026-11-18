/**
 * Build Script for VibeCore Chrome Extension.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 */

import esbuild from 'esbuild';
import fs from 'fs';
import path from 'path';

const distDir = path.resolve('dist');
if (!fs.existsSync(distDir)) {
  fs.mkdirSync(distDir, { recursive: true });
}

// 1. Build Background Service Worker (ESM format)
await esbuild.build({
  entryPoints: ['src/background/service_worker.ts'],
  bundle: true,
  outfile: 'dist/service_worker.js',
  target: 'es2022',
  format: 'esm',
  sourcemap: false,
  logLevel: 'info',
});

// 2. Build Content Script (IIFE format for in-page execution)
await esbuild.build({
  entryPoints: ['src/content/content_script.ts'],
  bundle: true,
  outfile: 'dist/content_script.js',
  target: 'es2022',
  format: 'iife',
  sourcemap: false,
  logLevel: 'info',
});

// 3. Build Popup Script (IIFE format)
await esbuild.build({
  entryPoints: ['src/popup/popup.ts'],
  bundle: true,
  outfile: 'dist/popup.js',
  target: 'es2022',
  format: 'iife',
  sourcemap: false,
  logLevel: 'info',
});

// 4. Copy HTML & CSS to dist
fs.copyFileSync('src/popup/popup.html', 'dist/popup.html');
fs.copyFileSync('src/popup/popup.css', 'dist/popup.css');

// 5. Generate self-contained dist/manifest.json pointing to built files
const rawManifest = JSON.parse(fs.readFileSync('manifest.json', 'utf8'));
const distManifest = {
  ...rawManifest,
  background: {
    service_worker: 'service_worker.js',
    type: 'module'
  },
  content_scripts: [
    {
      matches: rawManifest.content_scripts[0].matches,
      js: ['content_script.js'],
      run_at: 'document_idle'
    }
  ],
  action: {
    default_popup: 'popup.html',
    default_title: 'VibeCore Bridge'
  }
};

fs.writeFileSync('dist/manifest.json', JSON.stringify(distManifest, null, 2));

console.log(' Successfully built VibeCore Chrome Extension to chrome-extension/dist/');
