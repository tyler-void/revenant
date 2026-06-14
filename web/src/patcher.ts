/* Core patching logic — applies the shared patches/manifest.json to the user's OWN APK
 * in-browser. Native .so byte-patches work today; v1 signing + DEX tilt patch are the
 * next milestones (scaffolded in sign.ts / dex.ts). */
import JSZip from 'jszip';
import { signApkV1 } from './sign';
import { dexTiltRewrite } from './wasm';
import { stripManifest, type ElementMatch } from './axml';

export type BytePatch = {
  name: string; off: string; expect: string; patch: string; group: string; desc: string;
};
export type NativePatch = BytePatch;
export type TiltFix = {
  file: string; group: string; desc: string; method: string;
  bytePatches: { name: string; off: string; expect: string; patch: string }[];
};
export type AndroidManifestEdit = {
  file: string; group: string; dropPermissions: string[]; dropComponents?: ElementMatch[];
};
export type Manifest = {
  game: { package: string; label: string; version: string };
  native: { file: string; patches: NativePatch[] };
  dex?: { file: string; tilt?: TiltFix };
  androidManifest?: AndroidManifestEdit;
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

  // --- DEX tilt fix — must run BEFORE signing (signing digests file content) ---
  // The whole fix (register/unregister byte-patches + onSensorChanged code-item rewrite +
  // offset fixup + checksums) is applied atomically in Rust→WASM. If the dex isn't the
  // patchable 1.5.2 build the wasm returns null and classes.dex is left untouched.
  const tilt = manifest.dex?.tilt;
  if (tilt && enabledGroups.has(tilt.group)) {
    const dexFile = zip.file(tilt.file);
    if (!dexFile) {
      log(`  ✗ ${tilt.file} not found — tilt fix skipped`, 'err');
    } else {
      const dex = new Uint8Array(await dexFile.async('arraybuffer'));
      const out = await dexTiltRewrite(dex);
      if (out) {
        zip.file(tilt.file, out, { compression: 'DEFLATE' });
        log(`  ✓ tilt fix — ${tilt.desc}`, 'ok');
        log(`    ${tilt.file}: ${dex.length} → ${out.length} B (code-item rewrite + checksums, Rust→WASM).`, 'dim');
      } else {
        log(`  ✗ tilt fix: ${tilt.file} did not match the 1.5.2 layout — left untouched`, 'err');
      }
    }
  }

  // --- privacy: strip tracking permissions + push/ad/tracking components from AndroidManifest.xml ---
  const am = manifest.androidManifest;
  if (am && enabledGroups.has(am.group)) {
    const amFile = zip.file(am.file);
    if (!amFile) {
      log(`  ✗ ${am.file} not found — permission cull skipped`, 'err');
    } else {
      try {
        const axml = new Uint8Array(await amFile.async('arraybuffer'));
        const { out, removed, kept } = stripManifest(axml, {
          dropPermissions: am.dropPermissions,
          dropComponents: am.dropComponents,
        });
        if (removed.length) {
          zip.file(am.file, out, { compression: 'DEFLATE' });
          log(`  ✓ privacy: removed ${removed.length} permissions/components (${kept.length} permissions kept)`, 'ok');
          for (const p of removed) log(`      − ${p.replace('android.permission.', '')}`, 'dim');
        } else {
          log('  privacy: nothing to remove (already clean?).', 'dim');
        }
      } catch (e) {
        log(`  ✗ permission cull failed: ${e instanceof Error ? e.message : String(e)} — manifest left as-is`, 'err');
      }
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
