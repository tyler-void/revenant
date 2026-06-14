// Build the full distributed APK headlessly (native + tilt DEX + v1 sign), same as the browser.
const store: Record<string, string> = {};
(globalThis as any).localStorage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; } };

import JSZip from 'jszip';
import { signApkV1 } from './src/sign';
import { stripManifest } from './src/axml';

const hexToBytes = (h: string) => { const a = new Uint8Array(h.length / 2); for (let i = 0; i < a.length; i++) a[i] = parseInt(h.substr(i * 2, 2), 16); return a; };
const hex = (b: Uint8Array, n: number) => { let s = ''; for (let i = 0; i < n; i++) s += b[i].toString(16).padStart(2, '0'); return s; };
const log = (m: string) => console.log('  ' + m);

const manifest = JSON.parse(await Bun.file('../patches/manifest.json').text());
const zip = await JSZip.loadAsync(await Bun.file('../base/Bike+Rivals_1.5.2_APKPure.apk').arrayBuffer());

// native
const so = new Uint8Array(await zip.file(manifest.native.file)!.async('arraybuffer'));
let n = 0;
for (const p of manifest.native.patches) {
  const off = parseInt(p.off, 16), len = p.expect.length / 2;
  if (hex(so.subarray(off, off + len), len) !== p.expect) { console.log('native MISMATCH', p.name); continue; }
  so.set(hexToBytes(p.patch), off); n++;
}
zip.file(manifest.native.file, so, { compression: 'DEFLATE' });
log(`native: ${n} patches`);

// dex tilt — atomic Rust→WASM rewrite (register/unregister byte-patches + onSensorChanged
// code-item rewrite + offset fixup + checksums)
const dex = new Uint8Array(await zip.file(manifest.dex.file)!.async('arraybuffer'));
const w = (await WebAssembly.instantiate(await Bun.file('public/revenant_wasm.wasm').arrayBuffer(), {})).instance.exports as any;
const cap = dex.length + 1024;
const ptr = w.alloc(cap); new Uint8Array(w.memory.buffer, ptr, dex.length).set(dex);
const newLen = w.dex_tilt_rewrite(ptr, dex.length, cap);
if (newLen === 0) throw new Error('dex_tilt_rewrite failed (dex not 1.5.2?)');
const outDex = new Uint8Array(w.memory.buffer, ptr, newLen).slice(); w.dealloc(ptr, cap);
zip.file(manifest.dex.file, outDex, { compression: 'DEFLATE' });
log(`dex tilt: ${dex.length} -> ${newLen} B (code-item rewrite + checksums)`);

// privacy: strip permissions + tracking/push/ad components from binary AndroidManifest.xml
if (manifest.androidManifest) {
  const am = manifest.androidManifest;
  const axml = new Uint8Array(await zip.file(am.file)!.async('arraybuffer'));
  const { out, removed, kept } = stripManifest(axml, {
    dropPermissions: am.dropPermissions, dropComponents: am.dropComponents,
  });
  zip.file(am.file, out, { compression: 'DEFLATE' });
  log(`privacy: removed ${removed.length} (perms+components), ${kept.length} perms kept: ${removed.join(', ')}`);
}

await signApkV1(zip, log);
const out = new Uint8Array(await (await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' })).arrayBuffer());
await Bun.write('/tmp/revenant-dist.apk', out);
console.log('wrote /tmp/revenant-dist.apk', (out.length / 1048576).toFixed(1), 'MB');
