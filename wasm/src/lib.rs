//! Revenant WASM core. Raw C-ABI (no wasm-bindgen) so it loads in any browser/Vite without
//! extra tooling: JS calls `alloc`, writes bytes into wasm memory, calls a function, reads back.
//!
//! Today: `dex_fixup` (recompute a DEX's Adler32 + SHA-1 after in-place byte-patching).
//! Next: APK v1/v2/v3 signing in here (where Rust crates genuinely beat hand-rolled TS).

use sha1::{Digest, Sha1};
use std::alloc::{alloc as ralloc, dealloc as rdealloc, Layout};

/// Allocate `size` bytes in wasm linear memory; JS writes the input here. Returns the pointer.
#[no_mangle]
pub extern "C" fn alloc(size: usize) -> *mut u8 {
    unsafe { ralloc(Layout::from_size_align(size.max(1), 1).unwrap()) }
}

/// Free a buffer previously returned by `alloc`.
#[no_mangle]
pub extern "C" fn dealloc(ptr: *mut u8, size: usize) {
    if ptr.is_null() {
        return;
    }
    unsafe { rdealloc(ptr, Layout::from_size_align(size.max(1), 1).unwrap()) }
}

/// Recompute the DEX integrity fields IN PLACE after byte-patching:
///   SHA-1 over bytes[32..]  -> header[12..32]
///   Adler32 over bytes[12..] -> header[8..12]
/// `ptr`/`len` is the whole classes.dex buffer in wasm memory.
#[no_mangle]
pub extern "C" fn dex_fixup(ptr: *mut u8, len: usize) {
    if len < 32 {
        return;
    }
    let d = unsafe { core::slice::from_raw_parts_mut(ptr, len) };
    let sig = Sha1::digest(&d[32..]);
    d[12..32].copy_from_slice(&sig);
    let a = adler32(&d[12..]);
    d[8..12].copy_from_slice(&a.to_le_bytes());
}

fn adler32(data: &[u8]) -> u32 {
    const MOD: u32 = 65521;
    const NMAX: usize = 5552;
    let (mut a, mut b) = (1u32, 0u32);
    for chunk in data.chunks(NMAX) {
        for &x in chunk {
            a += x as u32;
            b += a;
        }
        a %= MOD;
        b %= MOD;
    }
    (b << 16) | a
}
