import { useEffect, useRef, useState } from 'react';
import { loadManifest, patchApk, type Manifest } from './patcher';

type Line = { text: string; cls?: 'ok' | 'err' | 'dim' };

const GROUPS = [
  { id: 'tilt', label: 'Fix tilt steering (modern Android)', default: true },
  { id: 'unlock', label: 'Unlock all worlds & bikes', default: true },
  { id: 'fuel', label: 'Unlimited fuel', default: true },
  { id: 'nitro', label: 'Unlimited nitro & helmets', default: true },
  { id: 'privacy', label: 'Remove tracking permissions (location, accounts, ads, push)', default: true },
];

const LINE_CLS: Record<string, string> = { ok: 'text-ok', err: 'text-err', dim: 'text-dim' };

const PANEL = 'bg-panel border border-edge rounded-xl p-4 sm:p-5';
const H2 = 'text-base font-semibold mb-2.5';

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
    <div className="mx-auto w-full max-w-3xl px-4 py-7 pb-16 sm:px-5">
      <header className="mb-5 flex flex-col items-center gap-4 text-center sm:flex-row sm:text-left">
        <img
          src={`${base}logo.png`}
          alt="Revenant"
          className="h-20 w-20 shrink-0 rounded-2xl shadow-[0_6px_24px_rgba(255,122,24,0.25)] sm:h-24 sm:w-24"
        />
        <div>
          <h1 className="text-2xl font-bold">
            Revenant <span className="text-dim font-normal">— Bike Rivals patcher</span>
          </h1>
          <p className="mt-1 text-dim">
            Revive Bike Rivals 1.5.2: all-unlocked, fuel-fixed, tilt-fixed, offline. Patched entirely in your browser.
          </p>
        </div>
      </header>

      <div className={`${PANEL} my-3.5 border-l-[3px] border-l-brand text-sm text-dim`}>
        <strong className="text-ink">Bring your own APK.</strong> This patches an original Bike Rivals 1.5.2 APK that{' '}
        <em>you</em> own, locally in your browser — nothing is uploaded and no game files are distributed.
      </div>

      <div className={`${PANEL} my-3.5`}>
        <h2 className={H2}>1. Your original APK</h2>
        <p className="mb-2 text-[13px] text-dim">com.miniclip.bikerivals 1.5.2</p>
        <input
          type="file"
          accept=".apk"
          onChange={(e) => onFile(e.target.files?.[0])}
          className="w-full cursor-pointer rounded-lg border border-dashed border-[#3a3f49] bg-[#14161a] p-3 text-sm
                     file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-edge file:px-3 file:py-1.5 file:text-ink"
        />
      </div>

      <div className={`${PANEL} my-3.5`}>
        <h2 className={H2}>2. Patches</h2>
        <div className="grid gap-1 sm:grid-cols-2">
          {GROUPS.map((g) => (
            <label key={g.id} className="flex cursor-pointer items-start gap-2 rounded-md py-1.5 hover:bg-white/5">
              <input
                type="checkbox"
                checked={groups[g.id]}
                onChange={(e) => setGroups((p) => ({ ...p, [g.id]: e.target.checked }))}
                className="mt-1 size-4 accent-brand"
              />
              <span className="text-sm">{g.label}</span>
            </label>
          ))}
        </div>
      </div>

      <div className={`${PANEL} my-3.5`}>
        <h2 className={H2}>3. Patch</h2>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <button
            onClick={run}
            disabled={busy || !apkBuf || !manifest}
            className="w-full rounded-lg bg-brand px-5 py-3 text-base font-bold text-[#1a1207] transition
                       hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          >
            {busy ? 'Patching…' : 'Patch APK'}
          </button>
          {dlUrl && (
            <a
              href={dlUrl}
              download="BikeRivals-revenant.apk"
              className="w-full rounded-lg bg-ok px-5 py-3 text-center text-base font-bold text-[#06210d] no-underline
                         transition hover:brightness-110 sm:w-auto"
            >
              ↓ Download patched APK
            </a>
          )}
        </div>
        <div
          ref={logRef}
          className="mt-3.5 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-[#0e1013] p-3 font-mono text-[13px] leading-relaxed"
        >
          {lines.map((l, i) => (
            <div key={i} className={l.cls ? LINE_CLS[l.cls] : ''}>{l.text}</div>
          ))}
        </div>
      </div>

      {dlUrl && (
        <div className={`${PANEL} my-3.5 border-l-[3px] border-l-ok`}>
          <h2 className={H2}>4. Install &amp; first launch</h2>
          <ol className="list-decimal space-y-1.5 pl-5 text-sm marker:text-dim">
            <li>Uninstall any existing Bike Rivals first (different signer), then install the downloaded APK.</li>
            <li>
              On first launch your phone shows a <strong className="text-brand">permissions review</strong>.{' '}
              <strong className="text-brand">Turn ON only “Motion” / sensor access</strong> — that’s all tilt steering
              needs. <strong className="text-brand">Leave every other toggle OFF.</strong> The game is fully offline and
              needs nothing else.
            </li>
            <li>If a “built for an older version of Android” notice appears, tap <strong className="text-brand">OK</strong> — that’s normal.</li>
          </ol>
          <p className="mt-2.5 text-[13px] text-dim">
            All tracking permissions (location, accounts, ads, billing, push) are already stripped from the APK. The
            remaining toggles your phone may list (clipboard, installed-apps, etc.) are your phone’s own generic
            controls, not requests from the game — keeping them off is safe.
          </p>
        </div>
      )}

      <footer className="mt-5 text-center text-[13px] text-dim">
        {apkName && <>Loaded: {apkName} · </>}Revenant project · BYO-original · methods/offsets only
      </footer>
    </div>
  );
}
