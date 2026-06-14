// Dev verification: sign a minimal zip with the real signApkV1 and confirm jarsigner accepts it.
// Run: bun run sign-test.ts  then  jarsigner -verify /tmp/revenant-sign-test.apk
const store: Record<string, string> = {};
(globalThis as any).localStorage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; } };

import JSZip from 'jszip';
import { signApkV1 } from './src/sign';

const zip = new JSZip();
zip.file('AndroidManifest.xml', new Uint8Array([1, 2, 3, 4, 5]));
zip.file('classes.dex', 'dummy dex content for digest test');
zip.file('lib/armeabi-v7a/libgame.so', new Uint8Array(2048));
zip.file('res/values/strings.xml', 'x'.repeat(100));
zip.file('a/very/deeply/nested/path/that/exceeds/seventy/bytes/to/exercise/line/wrapping/file.bin', 'wraptest');

await signApkV1(zip, (m) => console.log('  ' + m));
const out = await zip.generateAsync({ type: 'uint8array', compression: 'DEFLATE' });
await Bun.write('/tmp/revenant-sign-test.apk', out);
console.log('wrote /tmp/revenant-sign-test.apk (' + out.length + ' bytes)');
