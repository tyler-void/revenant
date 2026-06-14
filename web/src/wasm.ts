/* Loads the Rust→WASM core (raw C-ABI: alloc/dealloc + functions over linear memory). */
interface WasmExports {
  memory: WebAssembly.Memory;
  alloc(size: number): number;
  dealloc(ptr: number, size: number): void;
  dex_fixup(ptr: number, len: number): void;
  dex_tilt_rewrite(ptr: number, len: number, cap: number): number;
}

let cached: WasmExports | null = null;

async function load(): Promise<WasmExports> {
  if (cached) return cached;
  const url = `${import.meta.env.BASE_URL}revenant_wasm.wasm`;
  const bytes = await (await fetch(url)).arrayBuffer();
  const { instance } = await WebAssembly.instantiate(bytes, {});
  cached = instance.exports as unknown as WasmExports;
  return cached;
}

/** Recompute a DEX's Adler32 + SHA-1 in place (after byte-patching) via Rust→WASM. */
export async function dexFixup(dex: Uint8Array): Promise<void> {
  const w = await load();
  const ptr = w.alloc(dex.length);
  new Uint8Array(w.memory.buffer, ptr, dex.length).set(dex);
  w.dex_fixup(ptr, dex.length);
  dex.set(new Uint8Array(w.memory.buffer, ptr, dex.length));
  w.dealloc(ptr, dex.length);
}

/**
 * Apply the full MCAccelerometer tilt fix to classes.dex via Rust→WASM (register/unregister
 * byte-patches + onSensorChanged code-item rewrite + offset fixup + checksums), all atomically.
 * Returns the NEW (grown) dex bytes, or null if the dex isn't the patchable 1.5.2 build
 * (in which case the caller leaves classes.dex untouched — never half-patched).
 */
export async function dexTiltRewrite(dex: Uint8Array): Promise<Uint8Array | null> {
  const w = await load();
  const cap = dex.length + 1024; // headroom for the appended code_item (~88 B)
  const ptr = w.alloc(cap);
  new Uint8Array(w.memory.buffer, ptr, dex.length).set(dex);
  const newLen = w.dex_tilt_rewrite(ptr, dex.length, cap);
  let out: Uint8Array | null = null;
  if (newLen > 0) out = new Uint8Array(w.memory.buffer, ptr, newLen).slice();
  w.dealloc(ptr, cap);
  return out;
}
