/* APK v1 (JAR) signing, in-browser. WebCrypto for SHA-256 digests; node-forge for the
 * X.509 cert + PKCS#7 (CMS) signature block. A self-signed debug key is generated once and
 * cached in localStorage so re-patches keep the same signer (so `adb install -r` updates work).
 *
 * Layout written into META-INF/:
 *   MANIFEST.MF  — per-entry SHA-256-Digest of each file's (uncompressed) content
 *   CERT.SF      — SHA-256-Digest of each MANIFEST.MF section + of the whole manifest
 *   CERT.RSA     — PKCS#7 detached SignedData over CERT.SF (with the cert)
 */
import forge from 'node-forge';
import type JSZip from 'jszip';

export type LogFn = (msg: string, cls?: 'ok' | 'err' | 'dim') => void;

const LS_KEY = 'revenant.signer.v1';
const enc = new TextEncoder();

const u8ToBin = (u: Uint8Array): string => { let s = ''; for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); return s; };
const binToU8 = (b: string): Uint8Array => { const u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i) & 0xff; return u; };

async function sha256b64(data: Uint8Array): Promise<string> {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', data as BufferSource));
  return btoa(u8ToBin(h));
}

/** "Name: value\r\n" wrapped to <=70 bytes/line, continuation lines start with a space (JAR spec). */
function attr(name: string, value: string): string {
  let line = `${name}: ${value}`;
  if (line.length <= 70) return line + '\r\n';
  let out = line.slice(0, 70);
  let rest = line.slice(70);
  while (rest.length) { out += '\r\n ' + rest.slice(0, 69); rest = rest.slice(69); }
  return out + '\r\n';
}

type Signer = { privateKeyPem: string; certPem: string };

async function getSigner(log: LogFn): Promise<Signer> {
  const cached = localStorage.getItem(LS_KEY);
  if (cached) return JSON.parse(cached);
  log('Generating a signing key (one-time)…', 'dim');
  const keys = await new Promise<forge.pki.rsa.KeyPair>((res, rej) =>
    forge.pki.rsa.generateKeyPair({ bits: 2048, e: 0x10001 }, (err, kp) => (err ? rej(err) : res(kp))));
  const cert = forge.pki.createCertificate();
  cert.publicKey = keys.publicKey;
  cert.serialNumber = '01';
  cert.validity.notBefore = new Date(2020, 0, 1);
  cert.validity.notAfter = new Date(2099, 0, 1);
  const attrs = [{ name: 'commonName', value: 'Revenant Debug' }, { name: 'organizationName', value: 'Revenant' }];
  cert.setSubject(attrs);
  cert.setIssuer(attrs);
  cert.sign(keys.privateKey, forge.md.sha256.create());
  const signer: Signer = {
    privateKeyPem: forge.pki.privateKeyToPem(keys.privateKey),
    certPem: forge.pki.certificateToPem(cert),
  };
  localStorage.setItem(LS_KEY, JSON.stringify(signer));
  return signer;
}

const isSigFile = (p: string) => /^META-INF\/(MANIFEST\.MF|.*\.(SF|RSA|DSA|EC))$/i.test(p);

/** v1-sign the zip IN PLACE (adds META-INF/{MANIFEST.MF,CERT.SF,CERT.RSA}, strips any old sig). */
export async function signApkV1(zip: JSZip, log: LogFn): Promise<void> {
  // 1. strip any existing signature
  for (const p of Object.keys(zip.files)) if (isSigFile(p)) zip.remove(p);

  // 2. collect entries (files, not dirs, not the sig we'll add) in stable order
  const entries = Object.values(zip.files).filter((f) => !f.dir && !isSigFile(f.name));
  log(`Signing ${entries.length} entries (SHA-256, v1)…`);

  // 3. MANIFEST.MF
  const mainSection = 'Manifest-Version: 1.0\r\nCreated-By: Revenant\r\n\r\n';
  let manifest = mainSection;
  const sectionForEntry: Record<string, string> = {};
  for (const e of entries) {
    const content = new Uint8Array(await e.async('arraybuffer'));
    const digest = await sha256b64(content);
    const section = attr('Name', e.name) + attr('SHA-256-Digest', digest) + '\r\n';
    sectionForEntry[e.name] = section;
    manifest += section;
  }
  const manifestBytes = enc.encode(manifest);

  // 4. CERT.SF — digest the whole manifest, its main section, and each entry section
  let sf = 'Signature-Version: 1.0\r\n';
  sf += attr('SHA-256-Digest-Manifest', await sha256b64(manifestBytes));
  sf += attr('SHA-256-Digest-Manifest-Main-Attributes', await sha256b64(enc.encode(mainSection)));
  sf += 'Created-By: Revenant\r\n\r\n';
  for (const e of entries) {
    const sdig = await sha256b64(enc.encode(sectionForEntry[e.name]));
    sf += attr('Name', e.name) + attr('SHA-256-Digest', sdig) + '\r\n';
  }
  const sfBytes = enc.encode(sf);

  // 5. CERT.RSA — PKCS#7 detached signature over CERT.SF
  const signer = await getSigner(log);
  const cert = forge.pki.certificateFromPem(signer.certPem);
  const key = forge.pki.privateKeyFromPem(signer.privateKeyPem);
  const p7 = forge.pkcs7.createSignedData();
  p7.content = forge.util.createBuffer(u8ToBin(sfBytes));
  p7.addCertificate(cert);
  p7.addSigner({
    key,
    certificate: cert,
    digestAlgorithm: forge.pki.oids.sha256,
    authenticatedAttributes: [
      { type: forge.pki.oids.contentType, value: forge.pki.oids.data },
      { type: forge.pki.oids.messageDigest },
      { type: forge.pki.oids.signingTime, value: (new Date()).toString() },
    ],
  });
  p7.sign({ detached: true });
  const rsaBytes = binToU8(forge.asn1.toDer(p7.toAsn1()).getBytes());

  // 6. write META-INF (MANIFEST.MF first, by convention)
  zip.file('META-INF/MANIFEST.MF', manifestBytes);
  zip.file('META-INF/CERT.SF', sfBytes);
  zip.file('META-INF/CERT.RSA', rsaBytes);
  log('v1 signature written (META-INF/CERT.RSA).', 'ok');
}
