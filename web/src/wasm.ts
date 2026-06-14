/* Loads the Rust→WASM core (raw C-ABI: alloc/dealloc + functions over linear memory). */
interface WasmExports {
  memory: WebAssembly.Memory;
  alloc(size: number): number;
  dealloc(ptr: number, size: number): void;
  dex_fixup(ptr: number, len: number): void;
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
