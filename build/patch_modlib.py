#!/usr/bin/env python3
"""
Inject System.loadLibrary("mod") into GameActivity.<clinit> (right after the fmod/
fmodstudio loads) — a guaranteed-every-launch, very-early point. Wrapped in a
try/catch(Throwable) so a broken libmod can never crash the game at class-init.

libmod loads BEFORE libgame here, so libmod defers its hooks until libgame is mapped
(it polls dl_iterate_phdr) — see mod/mod.c.

Usage: python3 patch_modlib.py <decode_dir>
"""
import sys, os

GAMEACT = "smali/com/miniclip/bikerivals/GameActivity.smali"

def patch(root):
    p = os.path.join(root, GAMEACT)
    s = open(p).read()
    anchor = ('    const-string v0, "fmodstudio"\n\n'
              '    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n')
    assert s.count(anchor) == 1, "modlib anchor count=%d" % s.count(anchor)
    inject = anchor + (
        '\n'
        '    :try_start_rvmod\n'
        '    const-string v0, "mod"\n\n'
        '    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n'
        '    :try_end_rvmod\n'
        '    .catch Ljava/lang/Throwable; {:try_start_rvmod .. :try_end_rvmod} :catch_rvmod\n\n'
        '    goto :goto_rvmod\n\n'
        '    :catch_rvmod\n'
        '    move-exception v0\n\n'
        '    :goto_rvmod\n')
    s = s.replace(anchor, inject)
    open(p, "w").write(s)
    print('[modlib] System.loadLibrary("mod") injected into GameActivity.<clinit> (try/catch)')

if __name__ == "__main__":
    patch(sys.argv[1] if len(sys.argv) > 1 else "build/work")
