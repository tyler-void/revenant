#!/usr/bin/env python3
"""
PHASE-6 SPIKE — prove we can change live physics. NOT for release.

setGravity:@0x64d3a4 stores the gravity b2Vec2 into b2World.m_gravity
(world+0x19240 = x, +0x19244 = y). We hook just before the stores and quarter the
VERTICAL gravity magnitude (r3 = gravity.y) by decrementing its IEEE-754 exponent by 2
(sub 0x01000000 off the float bits — VFP-free, sign-preserving). Result on device: the
bike floats / jumps far higher → proof we can read+write the live Box2D world, which
de-risks the whole live mod menu (gravity slider, etc.).

Hook site 0x64d3bc (`mov r1,#0x244`, not PC-relative → safe to relocate). Cave does:
  sub r3, r3, #0x01000000   ; gravity.y /= 4   (x at r2 left alone — it's 0)
  mov r1, #0x244            ; displaced original
  b   0x64d3c0              ; continue into the two stores

Usage: python3 patch_gravity_spike.py [build/work/lib/armeabi-v7a/libgame.so]
"""
import struct, sys

SO   = sys.argv[1] if len(sys.argv) > 1 else "build/work/lib/armeabi-v7a/libgame.so"
S        = 0xaf745c     # code cave
HOOK     = 0x64d3bc     # mov r1, #0x244   (bytes 911fa0e3)
CONT     = 0x64d3c0
ORIG     = 0xe3a01f91   # mov r1, #0x244

def br(at, target):
    return struct.pack('<I', 0xea000000 | (((target - (at + 8)) >> 2) & 0xffffff))

ins = [
    0xe2433740,            # +00 sub r3, r3, #0x01000000   (gravity.y /= 4)
    ORIG,                  # +04 mov r1, #0x244            (displaced)
    ('b', 0x08, CONT),     # +08 b 0x64d3c0
]
stub = bytearray()
for w in ins:
    if isinstance(w, int):
        stub += struct.pack('<I', w)
    else:
        _, rel, tgt = w
        stub += br(S + rel, tgt)

data = bytearray(open(SO, 'rb').read())
assert all(b == 0 for b in data[S:S+len(stub)]), "cave @%#x not all-zero" % S
assert struct.unpack_from('<I', data, HOOK)[0] == ORIG, \
    "hook site mismatch: %s != mov r1,#0x244" % data[HOOK:HOOK+4].hex()
data[S:S+len(stub)] = stub
data[HOOK:HOOK+4]   = br(HOOK, S)
open(SO, 'wb').write(data)
print("[gravity-spike] hook setGravity:@%#x -> cave %#x  (gravity.y /= 4)" % (HOOK, S))
