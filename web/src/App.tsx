import { useEffect, useRef, useState } from 'react';
import { loadManifest, patchApk, type Manifest } from './patcher';

type Line = { text: string; cls?: 'ok' | 'err' | 'dim' };

const GROUPS = [
  { id: 'unlock', label: 'Unlock all worlds & bikes', default: true },
  { id: 'fuel', label: 'Unlimited fuel', default: true },
  { id: 'nitro', label: 'Unlimited nitro & helmets', default: true },
];

export default function App() {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [apkBuf, setApkBuf] = useState<ArrayBuffer | null>(null);
  const [apkName, setApkName] = useState('');
  const [groups, setGroups] = useState<Record<string, boolean>>(
    Object.fromEntries(GROUPS.map((g) => [g.id, g.default])),
  );
  const [lines, setLines] = useState<Line[]>([{ text: 'Waiting for an APK…', cls: 'dim' }]);
  const [busy, setBusy] = useState(false);
  const [dlUrl, setDlUrl] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const base = import.meta.env.BASE_URL;
  const log = (text: string, cls?: Line['cls']) => setLines((p) => [...p, { text, cls }]);

  useEffect(() => {
    loadManifest(base)
      .then((m) => { setManifest(m); log(`Manifest: ${m.game.label} ${m.game.version} — ${m.native.patches.length} native patches.`, 'dim'); })
      .catch((e) => log(`Could not load manifest.json: ${e}`, 'err'));
  }, [base]);

  useEffect(() => { logRef.current?.scrollTo(0, logRef.current.scrollHeight); }, [lines]);

  const onFile = (f: File | undefined) => {
    if (!f) return;
    const fr = new FileReader();
    fr.onload = () => { setApkBuf(fr.result as ArrayBuffer); setApkName(f.name); log(`Loaded ${f.name} (${((fr.result as ArrayBuffer).byteLength / 1048576).toFixed(1)} MB).`); };
    fr.readAsArrayBuffer(f);
  };

  const run = async () => {
    if (!apkBuf || !manifest) return;
    setBusy(true); setDlUrl(null); setLines([]);
    try {
      const enabled = new Set(Object.entries(groups).filter(([, v]) => v).map(([k]) => k));
      const { blob } = await patchApk(apkBuf, manifest, enabled, log);
      setDlUrl(URL.createObjectURL(blob));
      log('Done — install with: adb install -r <file>  (uninstall the original first).', 'ok');
    } catch (e) {
      log(`ERROR: ${e instanceof Error ? e.message : String(e)}`, 'err');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wrap">
      <header className="hero">
        <img src={`${base}logo.png`} alt="Revenant" className="logo" />
        <div>
          <h1>Revenant <span className="dim">— Bike Rivals patcher</span></h1>
          <p className="sub">Revive Bike Rivals 1.5.2: all-unlocked, fuel-fixed, offline. Patched entirely in your browser.</p>
        </div>
      </header>

      <div className="panel legal">
        <strong>Bring your own APK.</strong> This patches an original Bike Rivals 1.5.2 APK that <em>you</em> own,
        locally in your browser — nothing is uploaded and no game files are distributed.
      </div>

      <div className="panel">
        <h2>1. Your original APK</h2>
        <p className="dim sm">com.miniclip.bikerivals 1.5.2</p>
        <input type="file" accept=".apk" onChange={(e) => onFile(e.target.files?.[0])} />
      </div>

      <div className="panel">
        <h2>2. Patches</h2>
        {GROUPS.map((g) => (
          <label key={g.id} className="opt">
            <input type="checkbox" checked={groups[g.id]} onChange={(e) => setGroups((p) => ({ ...p, [g.id]: e.target.checked }))} />
            {g.label}
          </label>
        ))}
      </div>

      <div className="panel">
        <h2>3. Patch</h2>
        <button onClick={run} disabled={busy || !apkBuf || !manifest}>{busy ? 'Patching…' : 'Patch APK'}</button>
        {dlUrl && (
          <a className="dl" href={dlUrl} download="BikeRivals-revenant.apk">Download patched APK</a>
        )}
        <div className="log" ref={logRef}>
          {lines.map((l, i) => (<div key={i} className={l.cls}>{l.text}</div>))}
        </div>
      </div>

      <footer className="dim sm">
        {apkName && <>Loaded: {apkName} · </>}Revenant project · BYO-original · methods/offsets only
      </footer>
    </div>
  );
}
