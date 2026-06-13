#!/usr/bin/env python3
"""
DIAGNOSTIC World-5 trace patch — NOT for release. Finds the level-select logic that
decides per-world availability (_comingSoon).

Hooks `getWorldPanelForIndex:` IMP = 0x56bacc — called on the world/level-select
screen to fetch each world's panel. objc_msgSend tail-calls the IMP, so at entry
r2 = the world index arg and LR = the caller's return address (the LevelSelectionMenu
display logic). We log "i<index> L<callerLR>" to logcat tag RVWP, run the displaced
prologue, and continue (behavior-preserving).

Drive the game → open the level-select. `adb logcat | grep RVWP` shows which world
indices are queried + the caller address; subtract 4 → the `bl objc_msgSend`, then
disassemble that function to find where _comingSoon (+0x1c) is read/written and patch
the world-index condition.

Usage: python3 patch_worldtrace.py [build/work/lib/armeabi-v7a/libgame.so]
"""
import struct, sys

SO   = sys.argv[1] if len(sys.argv) > 1 else "build/work/lib/armeabi-v7a/libgame.so"
S        = 0xaf745c     # code cave (8 KB of zeros), 4-aligned
HOOK     = 0x56bacc     # getWorldPanelForIndex: first insn (push {...,lr})
CONT     = 0x56bad0     # after the displaced push
ORIG     = 0xe92d4ff0   # push {r4,r5,r6,r7,r8,sb,sl,fp,lr}  (bytes f04f2de9)
PLT_LOG  = 0x36b55c     # __android_log_print PLT stub

def br(at, target, link=False):
    off = (target - (at + 8)) >> 2
    return struct.pack('<I', (0xeb000000 if link else 0xea000000) | (off & 0xffffff))

# log __android_log_print(3, "RVWP", "i%d L%x", index, callerLR)
#   r0=prio r1=tag r2=fmt r3=arg1(index)  [sp]=arg2(LR)
TAG_OFF, FMT_OFF = 0x38, 0x40
ins = [
    0xe92d5fff,                                   # +00 push {r0-r12, lr}
    0xe1a0c00e,                                   # +04 mov  r12, lr        (caller LR -> arg2)
    0xe1a03002,                                   # +08 mov  r3,  r2        (index   -> arg1)
    0xe24dd008,                                   # +0c sub  sp, sp, #8     (room for stack vararg, 8-align)
    0xe58dc000,                                   # +10 str  r12, [sp]      (arg2 = LR)
    0xe3a00003,                                   # +14 mov  r0, #3         (log priority)
    0xe28f1000 | ((TAG_OFF-(0x18+8)) & 0xfff),    # +18 add  r1, pc, -> "RVWP"
    0xe28f2000 | ((FMT_OFF-(0x1c+8)) & 0xfff),    # +1c add  r2, pc, -> "i%d L%x"
    ('bl', 0x20, PLT_LOG),                        # +20 bl   __android_log_print
    0xe28dd008,                                   # +24 add  sp, sp, #8
    0xe8bd5fff,                                   # +28 pop  {r0-r12, lr}
    ORIG,                                         # +2c push {r4-r8,sb,sl,fp,lr}  (displaced)
    ('b', 0x30, CONT),                            # +30 b    0x56bad0
    0x00000000,                                   # +34 pad to TAG_OFF
]
stub = bytearray()
for w in ins:
    if isinstance(w, int):
        stub += struct.pack('<I', w)
    else:
        kind, rel, tgt = w
        stub += br(S + rel, tgt, link=(kind == 'bl'))
assert len(stub) == TAG_OFF, hex(len(stub))
stub += b'RVWP\x00\x00\x00\x00'                   # +38 tag (8B)
stub += b'i%d L%x\x00'                            # +40 fmt (8B)

data = bytearray(open(SO, 'rb').read())
assert all(b == 0 for b in data[S:S+len(stub)]), "cave @%#x not all-zero" % S
assert struct.unpack_from('<I', data, HOOK)[0] == ORIG, \
    "hook site mismatch: %s != push" % data[HOOK:HOOK+4].hex()
data[S:S+len(stub)] = stub
data[HOOK:HOOK+4]   = br(HOOK, S)
open(SO, 'wb').write(data)
print("[worldtrace] hook getWorldPanelForIndex:@%#x -> cave %#x -> RVWP (index + caller LR)" % (HOOK, S))
