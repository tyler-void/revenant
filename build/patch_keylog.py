#!/usr/bin/env python3
"""
DIAGNOSTIC patch (Route 2 key-capture) — NOT for release builds.

Injects a tiny ARM stub into a code cave of libgame.so that logs the asset cipher
key to logcat (tag RVKEY) every time cipher_setkey runs. Drive the game (load a
level); the real per-file key appears in `adb logcat -s RVKEY`. Then feed it to the
unidbg decryption oracle.

Hook: at cipher_setkey+0x24 (0x650594) r0 = the key bytes (result of the key-bytes
accessor). We divert the original `b 0x6505f0` to a stub that logs r0 as "%s",
restores, and continues.
"""
import struct, sys, os

SO = sys.argv[1] if len(sys.argv) > 1 else "build/work/lib/armeabi-v7a/libgame.so"

S       = 0xaf745c   # code cave (8 KB of zeros at 0xaf7459), 4-aligned
HOOK    = 0x650594   # original: b 0x6505f0  (key NSString is in r5 here)
CONT    = 0x6505f0   # original branch target (continue cipher_setkey)
PLT_LOG = 0x36b55c   # __android_log_print PLT stub (0x36b550 is modff!)
SEL_REG = 0x3775e0   # sel_registerName(char*)
MSGSEND = 0x3783d4   # objc_msgSend(recv, sel, ...)

def br(at, target, link=False):
    off = (target - (at + 8)) >> 2
    op = 0xeb000000 if link else 0xea000000
    return struct.pack('<I', op | (off & 0xffffff))

# ---- build the stub: HEX-dump `length` (r0) bytes of the key (r5) to logcat ----
# null-safe (binary keys); hex buffer on the STACK (cave is read-only .rodata).
HEXTAB_OFF, TAG_OFF, FMT_OFF = 0x78, 0x88, 0x90
def cb(at, target, cond=0xa):  # conditional/uncond branch
    off = (target - (at + 8)) >> 2
    return struct.pack('<I', (cond<<28) | 0x0a000000 | (off & 0xffffff))
ins = [
    0xe92d41ff,                                   # i0  +00 push {r0-r8, lr}
    0xe1a04005,                                   # i1  +04 mov  r4, r5   (key ptr)
    0xe1a05000,                                   # i2  +08 mov  r5, r0   (length)
    0xe24ddc02,                                   # i3  +0c sub  sp, sp, #0x200
    0xe1a0600d,                                   # i4  +10 mov  r6, sp   (hexbuf, writable)
    0xe28f7000 | ((HEXTAB_OFF-(0x14+8))&0xfff),   # i5  +14 add  r7, pc, # -> hextab
    0xe3a03000,                                   # i6  +18 mov  r3, #0   (i)
    0xe1530005,                                   # i7  +1c cmp  r3, r5     [LOOP]
    ('bge', 0x20, 0x4c),                          # i8  +20 bge  DONE
    0xe7d40003,                                   # i9  +24 ldrb r0, [r4, r3]
    0xe1a01220,                                   # i10 +28 lsr  r1, r0, #4
    0xe200200f,                                   # i11 +2c and  r2, r0, #0xf
    0xe7d71001,                                   # i12 +30 ldrb r1, [r7, r1]
    0xe7d72002,                                   # i13 +34 ldrb r2, [r7, r2]
    0xe0868083,                                   # i14 +38 add  r8, r6, r3, lsl #1
    0xe5c81000,                                   # i15 +3c strb r1, [r8]
    0xe5c82001,                                   # i16 +40 strb r2, [r8, #1]
    0xe2833001,                                   # i17 +44 add  r3, r3, #1
    ('b',   0x48, 0x1c),                          # i18 +48 b    LOOP
    0xe0868085,                                   # i19 +4c add  r8, r6, r5, lsl #1   [DONE]
    0xe3a00000,                                   # i20 +50 mov  r0, #0
    0xe5c80000,                                   # i21 +54 strb r0, [r8]   (null-terminate)
    0xe1a03006,                                   # i22 +58 mov  r3, r6     (%s arg)
    0xe3a00003,                                   # i23 +5c mov  r0, #3
    0xe28f1000 | ((TAG_OFF-(0x60+8))&0xfff),      # i24 +60 add  r1, pc, # -> tag
    0xe28f2000 | ((FMT_OFF-(0x64+8))&0xfff),      # i25 +64 add  r2, pc, # -> fmt
    ('bl',  0x68, PLT_LOG),                       # i26 +68 bl   __android_log_print
    0xe28ddc02,                                   # i27 +6c add  sp, sp, #0x200
    0xe8bd41ff,                                   # i28 +70 pop  {r0-r8, lr}
    ('b',   0x74, CONT),                          # i29 +74 b    0x6505f0
]
stub = bytearray()
for idx, w in enumerate(ins):
    if isinstance(w, int):
        stub += struct.pack('<I', w)
    else:
        kind, rel, tgt = w
        at = S + rel
        if kind == 'bge': stub += cb(at, S + tgt, cond=0xa)
        elif kind == 'b': stub += cb(at, (S+tgt) if tgt < 0x1000 else tgt, cond=0xe)
        elif kind == 'bl': stub += br(at, tgt, link=True)
assert len(stub) == HEXTAB_OFF, hex(len(stub))
stub += b'0123456789abcdef'                                    # +78 hextab (16B)
stub += b'RVKEY\x00\x00\x00'                                   # +88 tag (8B)
stub += b'%s\x00\x00'                                          # +90 fmt

data = bytearray(open(SO, 'rb').read())
# safety asserts
cave = data[S:S+len(stub)]
assert all(b == 0 for b in cave), "code cave at %#x is not all-zero (size %d)" % (S, len(stub))
orig = bytes(data[HOOK:HOOK+4])
want = br(HOOK, CONT)
assert orig == want, "hook site mismatch: have %s want %s (b 0x6505f0)" % (orig.hex(), want.hex())

# apply
data[S:S+len(stub)] = stub
data[HOOK:HOOK+4] = br(HOOK, S)
open(SO, 'wb').write(data)
print("[keylog] stub @ %#x (%d B), hook %#x: b 0x6505f0 -> b stub; logs tag RVKEY" % (S, len(stub), HOOK))
