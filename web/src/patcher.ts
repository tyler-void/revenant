/* Core patching logic — applies the shared patches/manifest.json to the user's OWN APK
 * in-browser. Native .so byte-patches work today; v1 signing + DEX tilt patch are the
 * next milestones (scaffolded in sign.ts / dex.ts). */
import JSZip from 'jszip';
import { signApkV1 } from './sign';
import { dexFixup } from './wasm';

export type BytePatch = {
  name: string; off: string; expect: string; patch: string; group: string; desc: string;
};
export type NativePatch = BytePatch;
export type Manifest = {
  game: { package: string; label: string; version: string };
  native: { file: string; patches: NativePatch[] };
  dex?: { file: string; recomputeChecksum: boolean; patches: BytePatch[] };
};
export type LogFn = (msg: string, cls?: 'ok' | 'err' | 'dim') => void;

const hexToBytes = (h: string): Uint8Array => {
  const a = new Uint8Array(h.length / 2);
  for (let i = 0; i < a.length; i++) a[i] = parseInt(h.substr(i * 2, 2), 16);
  return a;
};
const bytesToHex = (b: Uint8Array, n: number): string => {
  let s = '';
  for (let i = 0; i < n; i++) s += b[i].toString(16).padStart(2, '0');
  return s;
};

export async function loadManifest(base: string): Promise<Manifest> {
  const res = await fetch(`${base}manifest.json`);
  if (!res.ok) throw new Error(`manifest.json HTTP ${res.status}`);
  return res.json();
}

export type PatchResult = { blob: Blob; applied: number; skipped: number; failed: number };

/** Apply the native byte-patches for the enabled groups; returns the repackaged (UNSIGNED) APK. */
export async function patchApk(
  apkBuf: ArrayBuffer,
  manifest: Manifest,
  enabledGroups: Set<string>,
  log: LogFn,
): Promise<PatchResult> {
  log('Opening APK…');
  const zip = await JSZip.loadAsync(apkBuf);

  const soPath = manifest.native.file;
  const soEntry = zip.file(soPath);
  if (!soEntry) throw new Error(`APK missing ${soPath} — is this the Bike Rivals 1.5.2 APK?`);
  const so = new Uint8Array(await soEntry.async('arraybuffer'));
  log(`Patching ${soPath} (${(so.length / 1048576).toFixed(1)} MB)…`);

  let applied = 0, skipped = 0, failed = 0;
  for (const p of manifest.native.patches) {
    if (!enabledGroups.has(p.group)) { skipped++; continue; }
    const off = parseInt(p.off, 16);
    const n = p.expect.length / 2;
    const cur = bytesToHex(so.subarray(off, off + n), n);
    if (cur !== p.expect) {
      log(`  ✗ ${p.name} @ ${p.off}: ${cur} ≠ ${p.expect} (wrong/non-1.5.2 libgame) — skipped`, 'err');
      failed++;
      continue;
    }
    so.set(hexToBytes(p.patch), off);
    applied++;
    log(`  ✓ ${p.name} — ${p.desc}`, 'ok');
  }
  zip.file(soPath, so, { compression: 'DEFLATE' });
  log(`Native patches: ${applied} applied, ${skipped} off, ${failed} mismatched.`);

  // --- DEX byte-patches (tilt fix) — must run BEFORE signing (signing digests file content) ---
  if (manifest.dex && manifest.dex.patches.some((p) => enabledGroups.has(p.group))) {
    const dexFile = zip.file(manifest.dex.file);
    if (!dexFile) {
      log(`  ✗ ${manifest.dex.file} not found — tilt patch skipped`, 'err');
    } else {
      const dex = new Uint8Array(await dexFile.async('arraybuffer'));
      let dexApplied = 0;
      for (const p of manifest.dex.patches) {
        if (!enabledGroups.has(p.group)) continue;
        const off = parseInt(p.off, 16);
        const n = p.expect.length / 2;
        const cur = bytesToHex(dex.subarray(off, off + n), n);
        if (cur !== p.expect) {
          log(`  ✗ ${p.name} @ ${p.off}: ${cur} ≠ ${p.expect} (non-1.5.2 dex) — skipped`, 'err');
          continue;
        }
        dex.set(hexToBytes(p.patch), off);
        dexApplied++;
        log(`  ✓ ${p.name} — ${p.desc}`, 'ok');
      }
      if (dexApplied && manifest.dex.recomputeChecksum) {
        await dexFixup(dex);   // Rust→WASM: recompute Adler32 + SHA-1
        log('  dex checksum + signature recomputed (Rust→WASM).', 'dim');
      }
      if (dexApplied) zip.file(manifest.dex.file, dex, { compression: 'DEFLATE' });
    }
  }

  await signApkV1(zip, log);   // v1-sign so the result actually installs

  log('Repackaging APK…');
  const blob = await zip.generateAsync({
    type: 'blob',
    compression: 'DEFLATE',
    mimeType: 'application/vnd.android.package-archive',
  });
  return { blob, applied, skipped, failed };
}
