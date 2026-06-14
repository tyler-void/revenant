// Build the full distributed APK headlessly (native + tilt DEX + v1 sign), same as the browser.
const store: Record<string, string> = {};
(globalThis as any).localStorage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; } };

import JSZip from 'jszip';
import { signApkV1 } from './src/sign';

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

// dex tilt + wasm checksum
const dex = new Uint8Array(await zip.file(manifest.dex.file)!.async('arraybuffer'));
let dn = 0;
for (const p of manifest.dex.patches) {
  const off = parseInt(p.off, 16), len = p.expect.length / 2;
  if (hex(dex.subarray(off, off + len), len) !== p.expect) { console.log('dex MISMATCH', p.name); continue; }
  dex.set(hexToBytes(p.patch), off); dn++;
}
const w = (await WebAssembly.instantiate(await Bun.file('public/revenant_wasm.wasm').arrayBuffer(), {})).instance.exports as any;
const ptr = w.alloc(dex.length); new Uint8Array(w.memory.buffer, ptr, dex.length).set(dex);
w.dex_fixup(ptr, dex.length); dex.set(new Uint8Array(w.memory.buffer, ptr, dex.length)); w.dealloc(ptr, dex.length);
zip.file(manifest.dex.file, dex, { compression: 'DEFLATE' });
log(`dex tilt: ${dn} patches + wasm checksum`);

await signApkV1(zip, log);
const out = new Uint8Array(await (await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' })).arrayBuffer());
await Bun.write('/tmp/revenant-dist.apk', out);
console.log('wrote /tmp/revenant-dist.apk', (out.length / 1048576).toFixed(1), 'MB');
