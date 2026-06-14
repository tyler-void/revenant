//! Revenant WASM core. Raw C-ABI (no wasm-bindgen) so it loads in any browser/Vite without
//! extra tooling: JS calls `alloc`, writes bytes into wasm memory, calls a function, reads back.
//!
//!  * `dex_fixup`        — recompute a DEX's Adler32 + SHA-1 after in-place byte-patching.
//!  * `dex_tilt_rewrite` — apply the full MCAccelerometer tilt fix to classes.dex IN ONE ATOMIC
//!                         step: register()/unregister() same-size byte patches + a code-item
//!                         REWRITE of onSensorChanged(SensorEvent)V (modern landscape mapping +
//!                         try/catch around the native call), with all DEX offsets fixed up and
//!                         checksums recomputed. Returns the new length, or 0 on any failure
//!                         (caller then keeps the original dex untouched — never half-patched).

use sha1::{Digest, Sha1};
use std::alloc::{alloc as ralloc, dealloc as rdealloc, Layout};

#[no_mangle]
pub extern "C" fn alloc(size: usize) -> *mut u8 {
    unsafe { ralloc(Layout::from_size_align(size.max(1), 1).unwrap()) }
}

#[no_mangle]
pub extern "C" fn dealloc(ptr: *mut u8, size: usize) {
    if ptr.is_null() {
        return;
    }
    unsafe { rdealloc(ptr, Layout::from_size_align(size.max(1), 1).unwrap()) }
}

/// Recompute the DEX integrity fields IN PLACE: SHA-1 over [32..] -> [12..32], Adler32 over
/// [12..] -> [8..12]. `len` is the live dex length (buffer may be larger).
#[no_mangle]
pub extern "C" fn dex_fixup(ptr: *mut u8, len: usize) {
    if len < 32 {
        return;
    }
    let d = unsafe { core::slice::from_raw_parts_mut(ptr, len) };
    checksum(d);
}

fn checksum(d: &mut [u8]) {
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

// ---------------------------------------------------------------------------------------------
// DEX tilt rewrite
// ---------------------------------------------------------------------------------------------

const TYPE_CODE_ITEM: u16 = 0x2001;
const TYPE_ANNOTATION_SET_REF_LIST: u16 = 0x1002;
const TYPE_ANNOTATION_SET_ITEM: u16 = 0x1003;
const TYPE_ANNOTATIONS_DIRECTORY: u16 = 0x2006;

#[inline]
fn rd_u16(b: &[u8], o: usize) -> usize {
    (b[o] as usize) | ((b[o + 1] as usize) << 8)
}
#[inline]
fn rd_u32(b: &[u8], o: usize) -> usize {
    (b[o] as usize) | ((b[o + 1] as usize) << 8) | ((b[o + 2] as usize) << 16) | ((b[o + 3] as usize) << 24)
}
#[inline]
fn wr_u32(b: &mut [u8], o: usize, v: usize) {
    b[o..o + 4].copy_from_slice(&(v as u32).to_le_bytes());
}

/// Read unsigned LEB128; returns (value, new_offset).
fn read_uleb(b: &[u8], mut o: usize) -> (usize, usize) {
    let mut res = 0usize;
    let mut shift = 0u32;
    loop {
        let x = b[o];
        o += 1;
        res |= ((x & 0x7f) as usize) << shift;
        if x & 0x80 == 0 {
            break;
        }
        shift += 7;
    }
    (res, o)
}

fn read_sleb(b: &[u8], mut o: usize) -> (i64, usize) {
    let mut res: i64 = 0;
    let mut shift = 0u32;
    loop {
        let x = b[o];
        o += 1;
        res |= ((x & 0x7f) as i64) << shift;
        shift += 7;
        if x & 0x80 == 0 {
            if x & 0x40 != 0 {
                res |= -1i64 << shift;
            }
            break;
        }
    }
    (res, o)
}

fn uleb_encode(mut v: usize, out: &mut Vec<u8>) {
    loop {
        let mut x = (v & 0x7f) as u8;
        v >>= 7;
        if v != 0 {
            x |= 0x80;
            out.push(x);
        } else {
            out.push(x);
            break;
        }
    }
}

#[inline]
fn align4(o: usize) -> usize {
    (o + 3) & !3
}

/// Byte length of a code_item starting at `off` (NOT including trailing 4-align padding).
fn code_item_size(b: &[u8], off: usize) -> usize {
    let insns_size = rd_u32(b, off + 12);
    let tries = rd_u16(b, off + 6);
    let mut pos = off + 16 + insns_size * 2;
    if tries == 0 {
        return pos - off;
    }
    if insns_size % 2 == 1 {
        pos += 2; // padding to 4-align tries
    }
    pos += tries * 8; // try_items
    // encoded_catch_handler_list
    let (n, mut p) = read_uleb(b, pos);
    for _ in 0..n {
        let (size, np) = read_sleb(b, p);
        p = np;
        let cnt = size.unsigned_abs() as usize;
        for _ in 0..cnt {
            let (_, np2) = read_uleb(b, p);
            let (_, np3) = read_uleb(b, np2);
            p = np3;
        }
        if size <= 0 {
            let (_, np4) = read_uleb(b, p);
            p = np4;
        }
    }
    p - off
}

struct Hdr {
    string_ids_size: usize,
    string_ids_off: usize,
    type_ids_off: usize,
    proto_ids_size: usize,
    proto_ids_off: usize,
    field_ids_size: usize,
    field_ids_off: usize,
    method_ids_size: usize,
    method_ids_off: usize,
    class_defs_size: usize,
    class_defs_off: usize,
    map_off: usize,
}

fn parse_hdr(b: &[u8]) -> Hdr {
    Hdr {
        map_off: rd_u32(b, 0x34),
        string_ids_size: rd_u32(b, 0x38),
        string_ids_off: rd_u32(b, 0x3c),
        type_ids_off: rd_u32(b, 0x44),
        proto_ids_size: rd_u32(b, 0x48),
        proto_ids_off: rd_u32(b, 0x4c),
        field_ids_size: rd_u32(b, 0x50),
        field_ids_off: rd_u32(b, 0x54),
        method_ids_size: rd_u32(b, 0x58),
        method_ids_off: rd_u32(b, 0x5c),
        class_defs_size: rd_u32(b, 0x60),
        class_defs_off: rd_u32(b, 0x64),
    }
}

/// MUTF-8 bytes of string #idx (between the uleb length and the NUL terminator).
fn str_bytes<'a>(b: &'a [u8], h: &Hdr, idx: usize) -> &'a [u8] {
    let off = rd_u32(b, h.string_ids_off + 4 * idx);
    let (_, o) = read_uleb(b, off); // utf16 length, skip
    let mut e = o;
    while b[e] != 0 {
        e += 1;
    }
    &b[o..e]
}

fn str_eq(b: &[u8], h: &Hdr, idx: usize, target: &[u8]) -> bool {
    str_bytes(b, h, idx) == target
}

fn type_eq(b: &[u8], h: &Hdr, tidx: usize, target: &[u8]) -> bool {
    let sidx = rd_u32(b, h.type_ids_off + 4 * tidx);
    str_eq(b, h, sidx, target)
}

fn find_type(b: &[u8], h: &Hdr, desc: &[u8]) -> Option<usize> {
    // type_ids_size not stored; derive from proto/field scans not needed — scan via string match.
    // We don't have type_ids_size in Hdr; read it from header here.
    let type_ids_size = rd_u32(b, 0x40);
    for i in 0..type_ids_size {
        if type_eq(b, h, i, desc) {
            return Some(i);
        }
    }
    None
}

fn find_field(b: &[u8], h: &Hdr, cls: &[u8], name: &[u8], typ: &[u8]) -> Option<usize> {
    for i in 0..h.field_ids_size {
        let o = h.field_ids_off + 8 * i;
        let cidx = rd_u16(b, o);
        let tidx = rd_u16(b, o + 2);
        let nidx = rd_u32(b, o + 4);
        if type_eq(b, h, cidx, cls) && str_eq(b, h, nidx, name) && type_eq(b, h, tidx, typ) {
            return Some(i);
        }
    }
    None
}

/// proto params match `params` (slice of descriptors) and return type matches `ret`.
fn proto_matches(b: &[u8], h: &Hdr, pidx: usize, params: &[&[u8]], ret: &[u8]) -> bool {
    let o = h.proto_ids_off + 12 * pidx;
    let ret_tidx = rd_u32(b, o + 4);
    if !type_eq(b, h, ret_tidx, ret) {
        return false;
    }
    let params_off = rd_u32(b, o + 8);
    if params_off == 0 {
        return params.is_empty();
    }
    let n = rd_u32(b, params_off);
    if n != params.len() {
        return false;
    }
    for i in 0..n {
        let tidx = rd_u16(b, params_off + 4 + 2 * i);
        if !type_eq(b, h, tidx, params[i]) {
            return false;
        }
    }
    true
}

fn find_method(b: &[u8], h: &Hdr, cls: &[u8], name: &[u8], params: &[&[u8]], ret: &[u8]) -> Option<usize> {
    for i in 0..h.method_ids_size {
        let o = h.method_ids_off + 8 * i;
        let cidx = rd_u16(b, o);
        let pidx = rd_u16(b, o + 2);
        let nidx = rd_u32(b, o + 4);
        if type_eq(b, h, cidx, cls) && str_eq(b, h, nidx, name) && proto_matches(b, h, pidx, params, ret) {
            return Some(i);
        }
    }
    None
}

fn find_class_def(b: &[u8], h: &Hdr, desc: &[u8]) -> Option<usize> {
    let tidx = find_type(b, h, desc)?;
    for i in 0..h.class_defs_size {
        if rd_u32(b, h.class_defs_off + 32 * i) == tidx {
            return Some(i);
        }
    }
    None
}

/// Returns (code_off, code_off_uleb_pos, code_off_uleb_len) for the encoded_method matching
/// `target_midx` in class `class_desc`.
fn find_encoded_method(
    b: &[u8],
    h: &Hdr,
    class_desc: &[u8],
    target_midx: usize,
) -> Option<(usize, usize, usize)> {
    let cd = find_class_def(b, h, class_desc)?;
    let class_data_off = rd_u32(b, h.class_defs_off + 32 * cd + 24);
    if class_data_off == 0 {
        return None;
    }
    let mut o = class_data_off;
    let (sf, no) = read_uleb(b, o);
    o = no;
    let (inf, no) = read_uleb(b, o);
    o = no;
    let (dm, no) = read_uleb(b, o);
    o = no;
    let (vm, no) = read_uleb(b, o);
    o = no;
    // skip static + instance fields (field_idx_diff uleb, access uleb)
    for _ in 0..(sf + inf) {
        let (_, n1) = read_uleb(b, o);
        let (_, n2) = read_uleb(b, n1);
        o = n2;
    }
    // walk direct then virtual methods, accumulating method idx
    for group in [dm, vm] {
        let mut cur = 0usize;
        for _ in 0..group {
            let (diff, n1) = read_uleb(b, o);
            cur += diff;
            let (_access, n2) = read_uleb(b, n1); // access_flags
            let coff_pos = n2;
            let (code_off, n3) = read_uleb(b, n2);
            let coff_len = n3 - coff_pos;
            o = n3;
            if cur == target_midx {
                return Some((code_off, coff_pos, coff_len));
            }
        }
    }
    None
}

/// Build the new onSensorChanged code_item (88 bytes for 1.5.2), 4-aligned. Returns the body.
fn build_code_item(f_sensor: usize, f_values: usize, f_timestamp: usize, m_gettype: usize, m_native: usize) -> Vec<u8> {
    let i16 = |v: usize| [(v & 0xff) as u8, ((v >> 8) & 0xff) as u8];
    let mut insns: Vec<u8> = Vec::new();
    // iget-object v0, p1(v8), sensor
    insns.extend_from_slice(&[0x54, 0x80]);
    insns.extend_from_slice(&i16(f_sensor));
    // invoke-virtual {v0}, getType()I
    insns.extend_from_slice(&[0x6e, 0x10]);
    insns.extend_from_slice(&i16(m_gettype));
    insns.extend_from_slice(&i16(0x0000));
    // move-result v0
    insns.extend_from_slice(&[0x0a, 0x00]);
    // const/4 v1, 1
    insns.extend_from_slice(&[0x12, 0x11]);
    // if-eq v0, v1, :cond_process  (offset 3 code units -> :cond at addr 10)
    insns.extend_from_slice(&[0x32, 0x10, 0x03, 0x00]);
    // return-void
    insns.extend_from_slice(&[0x0e, 0x00]);
    // :cond_process  iget-object v6, p1(v8), values
    insns.extend_from_slice(&[0x54, 0x86]);
    insns.extend_from_slice(&i16(f_values));
    // const/4 v0,1 ; aget v0,v6,v0
    insns.extend_from_slice(&[0x12, 0x10, 0x44, 0x00, 0x06, 0x00]);
    // const/4 v1,0 ; aget v1,v6,v1 ; neg-float v1,v1
    insns.extend_from_slice(&[0x12, 0x01, 0x44, 0x01, 0x06, 0x01, 0x7f, 0x11]);
    // const/4 v2,2 ; aget v2,v6,v2
    insns.extend_from_slice(&[0x12, 0x22, 0x44, 0x02, 0x06, 0x02]);
    // iget-wide v3, p1(v8), timestamp  (0x53 = iget-wide)
    insns.extend_from_slice(&[0x53, 0x83]);
    insns.extend_from_slice(&i16(f_timestamp));
    // :try_start invoke-static {v0,v1,v2,v3,v4}, native onSensorChanged(FFFJ)V
    insns.extend_from_slice(&[0x71, 0x54]);
    insns.extend_from_slice(&i16(m_native));
    insns.extend_from_slice(&i16(0x3210));
    // :try_end return-void
    insns.extend_from_slice(&[0x0e, 0x00]);
    // :catch_all move-exception v0 ; return-void
    insns.extend_from_slice(&[0x0d, 0x00, 0x0e, 0x00]);

    let insns_size = insns.len() / 2; // 30 code units
    let try_start = 24usize; // invoke-static addr
    let try_count = 3usize;
    let catch_addr = 28usize;

    let mut body: Vec<u8> = Vec::new();
    body.extend_from_slice(&i16(9)); // registers_size
    body.extend_from_slice(&i16(2)); // ins_size
    body.extend_from_slice(&i16(5)); // outs_size
    body.extend_from_slice(&i16(1)); // tries_size
    body.extend_from_slice(&[0, 0, 0, 0]); // debug_info_off = 0
    body.extend_from_slice(&(insns_size as u32).to_le_bytes());
    body.extend_from_slice(&insns);
    if insns_size % 2 == 1 {
        body.extend_from_slice(&[0, 0]); // pad to 4-align tries
    }
    // try_item: start_addr u32, insn_count u16, handler_off u16
    body.extend_from_slice(&(try_start as u32).to_le_bytes());
    body.extend_from_slice(&i16(try_count));
    body.extend_from_slice(&i16(1)); // handler_off = 1
    // encoded_catch_handler_list: size=1, handler{ sleb 0 (catch-all), uleb catch_all_addr }
    uleb_encode(1, &mut body);
    body.push(0x00); // sleb 0
    uleb_encode(catch_addr, &mut body);
    while body.len() % 4 != 0 {
        body.push(0);
    }
    body
}

/// Apply the full tilt fix. Buffer `ptr` has capacity `cap`, holds a dex of length `len`.
/// Returns new length, or 0 on failure (no usable output).
#[no_mangle]
pub extern "C" fn dex_tilt_rewrite(ptr: *mut u8, len: usize, cap: usize) -> usize {
    let buf = unsafe { core::slice::from_raw_parts_mut(ptr, cap) };
    match tilt_rewrite(buf, len) {
        Some(n) => n,
        None => 0,
    }
}

const CLS: &[u8] = b"Lcom/miniclip/input/MCAccelerometer;";
const SE: &[u8] = b"Landroid/hardware/SensorEvent;";

fn tilt_rewrite(buf: &mut [u8], len: usize) -> Option<usize> {
    if len < 0x70 || &buf[0..4] != b"dex\n" {
        return None;
    }
    let h = parse_hdr(&buf[..len]);

    // --- resolve refs (all must already exist in the pool) ---
    let f_sensor = find_field(&buf[..len], &h, SE, b"sensor", b"Landroid/hardware/Sensor;")?;
    let f_values = find_field(&buf[..len], &h, SE, b"values", b"[F")?;
    let f_timestamp = find_field(&buf[..len], &h, SE, b"timestamp", b"J")?;
    let m_gettype = find_method(&buf[..len], &h, b"Landroid/hardware/Sensor;", b"getType", &[], b"I")?;
    let m_native = find_method(&buf[..len], &h, CLS, b"onSensorChanged", &[b"F", b"F", b"F", b"J"], b"V")?;
    // 16-bit operand fields must fit
    if f_sensor > 0xffff || f_values > 0xffff || f_timestamp > 0xffff || m_gettype > 0xffff || m_native > 0xffff {
        return None;
    }

    // --- target method + its code_off uleb ---
    let target_midx = find_method(&buf[..len], &h, CLS, b"onSensorChanged", &[SE], b"V")?;
    let (_old_off, coff_pos, coff_len) = find_encoded_method(&buf[..len], &h, CLS, target_midx)?;

    // --- parse map: locate code section + P (next section) ---
    let new_map_base = h.map_off; // (map is < P here; verified below it doesn't move)
    let map_n = rd_u32(&buf[..len], h.map_off);
    let mut code_start = 0usize;
    let mut code_count = 0usize;
    let mut code_map_item_pos = 0usize;
    let mut p_off = usize::MAX;
    // first pass to get code_start
    for i in 0..map_n {
        let mp = h.map_off + 4 + 12 * i;
        let t = rd_u16(&buf[..len], mp) as u16;
        let sz = rd_u32(&buf[..len], mp + 4);
        let off = rd_u32(&buf[..len], mp + 8);
        if t == TYPE_CODE_ITEM {
            code_start = off;
            code_count = sz;
            code_map_item_pos = mp;
        }
    }
    if code_start == 0 {
        return None;
    }
    // P = min section offset > code_start  (offset field is at map_item +8)
    for i in 0..map_n {
        let off = rd_u32(&buf[..len], h.map_off + 4 + 12 * i + 8);
        if off > code_start && off < p_off {
            p_off = off;
        }
    }
    if p_off == usize::MAX {
        return None;
    }
    let p = p_off;

    // --- build new code_item ---
    let body = build_code_item(f_sensor, f_values, f_timestamp, m_gettype, m_native);
    let k = body.len();
    if len + k > buf.len() {
        return None; // not enough capacity
    }

    // --- repoint code_off must keep same uleb width ---
    let mut new_coff = Vec::new();
    uleb_encode(p, &mut new_coff);
    if new_coff.len() != coff_len {
        return None;
    }
    if coff_pos >= p {
        return None; // class_data after P unsupported (it isn't, in 1.5.2)
    }

    // --- same-size byte patches (atomic verify first) ---
    // register(): NOP if-eqz isEnabled gate @0x43fc46; unregister(): return-void @0x43fca4
    const BP: [(usize, [u8; 4], [u8; 4]); 2] = [
        (0x43fc46, [0x38, 0x00, 0x0b, 0x00], [0x00, 0x00, 0x00, 0x00]),
        (0x43fca4, [0x63, 0x00, 0xb9, 0x43], [0x0e, 0x00, 0x00, 0x00]),
    ];
    for (off, expect, _patch) in BP.iter() {
        if &buf[*off..*off + 4] != expect {
            return None; // wrong/non-1.5.2 dex
        }
    }

    // ===== MUTATE (everything verified above) =====
    // shift [p..len) -> [p+k..len+k)
    buf.copy_within(p..len, p + k);
    // write new code_item at [p..p+k)
    buf[p..p + k].copy_from_slice(&body);
    let new_len = len + k;

    // byte patches
    for (off, _expect, patch) in BP.iter() {
        buf[*off..*off + 4].copy_from_slice(patch);
    }

    // --- offset fixups: every u32 file-offset >= p gets +k ---
    let fix = |b: &mut [u8], pos: usize| {
        let v = rd_u32(b, pos);
        if v >= p && v != 0 {
            wr_u32(b, pos, v + k);
        }
    };
    // header: file_size(+k), data_size(+k), map_off(maybe)
    let fs = rd_u32(buf, 0x20);
    wr_u32(buf, 0x20, fs + k);
    let ds = rd_u32(buf, 0x68);
    wr_u32(buf, 0x68, ds + k);
    fix(buf, 0x34);
    // string_ids -> string_data_off
    for i in 0..h.string_ids_size {
        fix(buf, h.string_ids_off + 4 * i);
    }
    // proto_ids -> parameters_off (+8)
    for i in 0..h.proto_ids_size {
        fix(buf, h.proto_ids_off + 12 * i + 8);
    }
    // class_defs -> interfaces/annotations/class_data/static_values
    for i in 0..h.class_defs_size {
        let base = h.class_defs_off + 32 * i;
        for d in [12usize, 20, 24, 28] {
            fix(buf, base + d);
        }
    }
    // code_items (original count): debug_info_off (+8)
    let mut o = code_start;
    for _ in 0..code_count {
        o = align4(o);
        fix(buf, o + 8);
        o += code_item_size(buf, o);
    }
    // annotation sections (positions shift by k if section >= p)
    let sec_base = |off: usize| if off >= p { off + k } else { off };
    for i in 0..map_n {
        // read map item from its (unchanged) position; map is < p here
        let mp = new_map_base + 4 + 12 * i;
        let t = rd_u16(buf, mp) as u16;
        let sz = rd_u32(buf, mp + 4);
        let off = rd_u32(buf, mp + 8);
        // note: off may already be >= p (a moved section) but we use ORIGINAL off; the map
        // entries we read here still hold pre-fixup values (we fix them in the final pass).
        let mut base = sec_base(off);
        match t {
            TYPE_ANNOTATION_SET_ITEM | TYPE_ANNOTATION_SET_REF_LIST => {
                for _ in 0..sz {
                    base = align4(base);
                    let n = rd_u32(buf, base);
                    for j in 0..n {
                        fix(buf, base + 4 + 4 * j);
                    }
                    base += 4 + 4 * n;
                }
            }
            TYPE_ANNOTATIONS_DIRECTORY => {
                for _ in 0..sz {
                    base = align4(base);
                    fix(buf, base); // class_annotations_off
                    let fsz = rd_u32(buf, base + 4);
                    let msz = rd_u32(buf, base + 8);
                    let psz = rd_u32(buf, base + 12);
                    let mut pp = base + 16;
                    for _ in 0..(fsz + msz + psz) {
                        fix(buf, pp + 4); // annotations_off
                        pp += 8;
                    }
                    base = pp;
                }
            }
            _ => {}
        }
    }
    // map item offsets + bump code count
    for i in 0..map_n {
        let mp = new_map_base + 4 + 12 * i;
        let t = rd_u16(buf, mp) as u16;
        fix(buf, mp + 8);
        if t == TYPE_CODE_ITEM {
            let c = rd_u32(buf, mp + 4);
            wr_u32(buf, mp + 4, c + 1);
        }
    }
    let _ = code_map_item_pos;

    // repoint onSensorChanged code_off (uleb, same width, position < p so unmoved)
    buf[coff_pos..coff_pos + coff_len].copy_from_slice(&new_coff);

    // recompute checksums over the live dex
    checksum(&mut buf[..new_len]);
    Some(new_len)
}
