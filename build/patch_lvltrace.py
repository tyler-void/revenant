#!/usr/bin/env python3
"""
MOD-LOADER recon (round 2) — capture the LEVEL filename format.

-[... loadLevelInfo:FileName:]@0x6e25dc takes (Info @r2, FileName @r3). r3 = the level
filename NSString. Stub logs [r3 UTF8String] to logcat tag RVLN, runs the displaced
prologue, continues. Cave region 2 (0xaf7500) so it coexists with the gravity hook.

Drive: load any level; `adb logcat | grep RVLN` shows the exact filename string.
Usage: python3 patch_lvltrace.py [build/work/lib/armeabi-v7a/libgame.so]
"""
import struct, sys

SO   = sys.argv[1] if len(sys.argv) > 1 else "build/work/lib/armeabi-v7a/libgame.so"
S        = 0xaf7500     # cave region 2 (gravity uses region 1 @0xaf745c)
HOOK     = 0x6e25dc     # loadLevelInfo:FileName: first insn (push {...,lr})
CONT     = 0x6e25e0
ORIG     = 0xe92d4bf0   # push {r4,r5,r6,r7,r8,sb,fp,lr}
SEL_REG  = 0x3775e0
MSGSEND  = 0x3783d4
PLT_LOG  = 0x36b55c

def br(at, target, link=False):
    return struct.pack('<I', (0xeb000000 if link else 0xea000000) | (((target - (at + 8)) >> 2) & 0xffffff))

UTF8_OFF, TAG_OFF, FMT_OFF = 0x3c, 0x48, 0x50
ins = [
    0xe92d5fff,                                   # +00 push {r0-r12, lr}
    0xe1a05003,                                   # +04 mov  r5, r3          (FileName)
    0xe28f0000 | ((UTF8_OFF-(0x08+8)) & 0xfff),   # +08 add  r0, pc, -> "UTF8String"
    ('bl', 0x0c, SEL_REG),                        # +0c bl   sel_registerName
    0xe1a01000,                                   # +10 mov  r1, r0
    0xe1a00005,                                   # +14 mov  r0, r5          (FileName)
    ('bl', 0x18, MSGSEND),                        # +18 bl   objc_msgSend -> char*
    0xe1a03000,                                   # +1c mov  r3, r0
    0xe3a00003,                                   # +20 mov  r0, #3
    0xe28f1000 | ((TAG_OFF-(0x24+8)) & 0xfff),    # +24 add  r1, pc, -> "RVLN"
    0xe28f2000 | ((FMT_OFF-(0x28+8)) & 0xfff),    # +28 add  r2, pc, -> "%s"
    ('bl', 0x2c, PLT_LOG),                        # +2c bl   __android_log_print
    0xe8bd5fff,                                   # +30 pop  {r0-r12, lr}
    ORIG,                                         # +34 push {...,lr}        (displaced)
    ('b', 0x38, CONT),                            # +38 b
]
stub = bytearray()
for w in ins:
    if isinstance(w, int):
        stub += struct.pack('<I', w)
    else:
        kind, rel, tgt = w
        stub += br(S + rel, tgt, link=(kind == 'bl'))
assert len(stub) == UTF8_OFF, hex(len(stub))
stub += b'UTF8String\x00'
stub += b'\x00' * (TAG_OFF - len(stub))
stub += b'RVLN\x00\x00\x00\x00'
stub += b'%s\x00\x00'

data = bytearray(open(SO, 'rb').read())
assert all(b == 0 for b in data[S:S+len(stub)]), "cave @%#x not all-zero" % S
assert struct.unpack_from('<I', data, HOOK)[0] == ORIG, \
    "hook site mismatch: %s" % data[HOOK:HOOK+4].hex()
data[S:S+len(stub)] = stub
data[HOOK:HOOK+4]   = br(HOOK, S)
open(SO, 'wb').write(data)
print("[lvltrace] hook loadLevelInfo:FileName:@%#x -> cave %#x -> RVLN (level filename)" % (HOOK, S))
