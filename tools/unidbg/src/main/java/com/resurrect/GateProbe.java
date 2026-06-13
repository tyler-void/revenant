package com.resurrect;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.Module;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.linux.android.dvm.DalvikModule;
import com.github.unidbg.linux.android.dvm.VM;
import com.github.unidbg.memory.Memory;
import com.github.unidbg.memory.MemoryBlock;
import com.github.unidbg.pointer.UnidbgPointer;

import java.io.File;

/**
 * Milestone 4 (gate behaviour): build a fake BikeInfo instance, flip each candidate
 * BOOL ivar (purchased_@0x9, unlocked@0xb, _purchased@0xc), and call the
 * `purchased` getter (IMP 0x5eed14) + `revealed` getter to confirm which ivar each
 * reads, and that the getter return tracks the ivar value. This pins the gate ivar.
 */
public class GateProbe {
    static final long PURCHASED_GETTER = 0x5eed14L;

    public static void main(String[] args) throws Exception {
        File so = new File("src/main/resources/libgame.so");
        if (!so.exists()) so = new File("tools/unidbg/src/main/resources/libgame.so");

        AndroidEmulator emulator = AndroidEmulatorBuilder.for32Bit().setProcessName("br").build();
        Memory memory = emulator.getMemory();
        memory.setLibraryResolver(new AndroidResolver(23));
        VM vm = emulator.createDalvikVM();
        vm.setVerbose(false);
        DalvikModule dm = vm.loadLibrary(so, false);
        Module module = dm.getModule();
        long base = module.base;
        System.out.println("[*] base=0x" + Long.toHexString(base));

        MemoryBlock blk = memory.malloc(128, true);
        UnidbgPointer self = blk.getPointer();

        System.out.println("[*] Calling 'purchased' getter (IMP 0x5eed14) with _purchased(@0xc) toggled:");
        for (int v : new int[]{0, 1}) {
            for (int i = 0; i < 128; i++) self.setByte(i, (byte) 0); // clear all ivars
            self.setByte(0xc, (byte) v);                              // _purchased = v
            Number ret = module.callFunction(emulator, PURCHASED_GETTER, self.peer, 0L);
            long r0 = ret == null ? -1 : ret.longValue() & 0xff;
            System.out.println("[*]   _purchased(@0xc)=" + v + "  => getter returned " + r0
                    + (r0 == v ? "  (tracks)" : "  (MISMATCH)"));
        }

        System.out.println("[*] Cross-check: set OTHER BOOLs, leave _purchased=0, getter must stay 0:");
        for (int off : new int[]{0x9, 0xb}) {
            for (int i = 0; i < 128; i++) self.setByte(i, (byte) 0);
            self.setByte(off, (byte) 1); // purchased_ or unlocked = 1, _purchased stays 0
            Number ret = module.callFunction(emulator, PURCHASED_GETTER, self.peer, 0L);
            long r0 = ret == null ? -1 : ret.longValue() & 0xff;
            System.out.println("[*]   ivar@0x" + Integer.toHexString(off) + "=1, _purchased=0 => getter returned " + r0
                    + (r0 == 0 ? "  (confirms getter reads ONLY _purchased@0xc)" : "  (unexpected)"));
        }

        blk.free();
        emulator.close();
        System.out.println("[*] done.");
    }
}
