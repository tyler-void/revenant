// Dev verification: full pipeline (native patches + v1 sign) on the REAL local APK.
const store: Record<string, string> = {};
(globalThis as any).localStorage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; } };

import { patchApk, type Manifest } from './src/patcher';

const manifest: Manifest = JSON.parse(await Bun.file('../patches/manifest.json').text());
const apkBuf = await Bun.file('../base/Bike+Rivals_1.5.2_APKPure.apk').arrayBuffer();
const groups = new Set(['unlock', 'fuel', 'nitro']);
const { blob, applied, skipped, failed } = await patchApk(apkBuf, manifest, groups, (m) => console.log('  ' + m));
const u8 = new Uint8Array(await blob.arrayBuffer());
await Bun.write('/tmp/revenant-full-test.apk', u8);
console.log(`OUT ${(u8.length / 1048576).toFixed(1)} MB · applied=${applied} skipped=${skipped} failed=${failed}`);
