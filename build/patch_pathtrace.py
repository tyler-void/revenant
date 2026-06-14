#!/usr/bin/env python3
"""
MOD-LOADER recon — log every filename CCFileUtils resolves, so we know the exact
string format the mods/ folder must mirror. NOT for release.

Hook -[CCFileUtils fullPathForFilename:] @0x4d78b0 (first insn push {fp,lr}). r1 = the
requested NSString filename. Stub logs [filename UTF8String] to logcat tag RVPT, then
runs the displaced prologue and continues. (Messaging nil is safe in ObjC, so no nil
guard needed.)

Drive the game; `adb logcat | grep RVPT` lists the real requested names.
Usage: python3 patch_pathtrace.py [build/work/lib/armeabi-v7a/libgame.so]
"""
import struct, sys

SO   = sys.argv[1] if len(sys.argv) > 1 else "build/work/lib/armeabi-v7a/libgame.so"
S        = 0xaf745c     # code cave
HOOK     = 0x4d78b0     # fullPathForFilename: first insn (push {fp,lr})
CONT     = 0x4d78b4
ORIG     = 0xe92d4800   # push {fp, lr}
SEL_REG  = 0x3775e0     # sel_registerName(char*)
MSGSEND  = 0x3783d4     # objc_msgSend(recv, sel, ...)
PLT_LOG  = 0x36b55c     # __android_log_print

def br(at, target, link=False):
    return struct.pack('<I', (0xeb000000 if link else 0xea000000) | (((target - (at + 8)) >> 2) & 0xffffff))

UTF8_OFF, TAG_OFF, FMT_OFF = 0x3c, 0x48, 0x50
ins = [
    0xe92d5fff,                                   # +00 push {r0-r12, lr}
    0xe1a05001,                                   # +04 mov  r5, r1          (preserve filename)
    0xe28f0000 | ((UTF8_OFF-(0x08+8)) & 0xfff),   # +08 add  r0, pc, -> "UTF8String"
    ('bl', 0x0c, SEL_REG),                        # +0c bl   sel_registerName
    0xe1a01000,                                   # +10 mov  r1, r0          (SEL)
    0xe1a00005,                                   # +14 mov  r0, r5          (filename)
    ('bl', 0x18, MSGSEND),                        # +18 bl   objc_msgSend    -> char* utf8
    0xe1a03000,                                   # +1c mov  r3, r0          (%s arg)
    0xe3a00003,                                   # +20 mov  r0, #3          (log priority)
    0xe28f1000 | ((TAG_OFF-(0x24+8)) & 0xfff),    # +24 add  r1, pc, -> "RVPT"
    0xe28f2000 | ((FMT_OFF-(0x28+8)) & 0xfff),    # +28 add  r2, pc, -> "%s"
    ('bl', 0x2c, PLT_LOG),                        # +2c bl   __android_log_print
    0xe8bd5fff,                                   # +30 pop  {r0-r12, lr}
    ORIG,                                         # +34 push {fp, lr}        (displaced)
    ('b', 0x38, CONT),                            # +38 b    0x4d78b4
]
stub = bytearray()
for w in ins:
    if isinstance(w, int):
        stub += struct.pack('<I', w)
    else:
        kind, rel, tgt = w
        stub += br(S + rel, tgt, link=(kind == 'bl'))
assert len(stub) == UTF8_OFF, hex(len(stub))
stub += b'UTF8String\x00'                          # +3c (11B)
stub += b'\x00' * (TAG_OFF - len(stub))            # pad to TAG_OFF
stub += b'RVPT\x00\x00\x00\x00'                    # +48 (8B)
stub += b'%s\x00\x00'                              # +50 (4B)

data = bytearray(open(SO, 'rb').read())
assert all(b == 0 for b in data[S:S+len(stub)]), "cave @%#x not all-zero" % S
assert struct.unpack_from('<I', data, HOOK)[0] == ORIG, \
    "hook site mismatch: %s != push{fp,lr}" % data[HOOK:HOOK+4].hex()
data[S:S+len(stub)] = stub
data[HOOK:HOOK+4]   = br(HOOK, S)
open(SO, 'wb').write(data)
print("[pathtrace] hook fullPathForFilename:@%#x -> cave %#x -> RVPT (requested filename)" % (HOOK, S))
