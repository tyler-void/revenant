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
 * PatchVerify — prove the proposed SINGLE-INSTRUCTION patch forces the gate getters
 * true regardless of ivar contents, on a synthetic BikeInfo instance.
 *
 * Patch: at the getter's `ldrb r0,[r0,r1]` instruction (file offset = IMP+0x14),
 * overwrite the 4 bytes with ARM `mov r0,#1` = 0xE3A00001 (LE: 01 00 A0 E3).
 * The trailing `bx lr` then returns 1. One instruction, 4 bytes, no shape change.
 *
 * We verify: 'unlocked' getter (reads unlocked@0xb) returns 1 even when 0xb==0.
 */
public class PatchVerify {
    static AndroidEmulator emulator;
    static Module module;
    static long base;
    static final int INST = 72;

    // ARM `mov r0,#1`  (E3A00001) little-endian bytes
    static final byte[] MOV_R0_1 = { 0x01, 0x00, (byte) 0xA0, (byte) 0xE3 };

    public static void main(String[] args) throws Exception {
        File so = new File("src/main/resources/libgame.so");
        if (!so.exists()) so = new File("tools/unidbg/src/main/resources/libgame.so");
        emulator = AndroidEmulatorBuilder.for32Bit().setProcessName("br").build();
        Memory memory = emulator.getMemory();
        memory.setLibraryResolver(new AndroidResolver(23));
        VM vm = emulator.createDalvikVM();
        vm.setVerbose(false);
        DalvikModule dm = vm.loadLibrary(so, false);
        module = dm.getModule();
        base = module.base;
        System.out.println("RESULT base=0x" + Long.toHexString(base));

        // {name, IMP fileoff, ivar offset it reads}
        Object[][] targets = {
            {"unlocked",  0x5eea94L, 0xb},
            {"purchased", 0x5eed14L, 0xc},
        };

        for (Object[] t : targets) {
            String name = (String) t[0];
            long imp = (Long) t[1];
            int ivar = (Integer) t[2];
            long ldrb = imp + 0x14;

            System.out.println("RESULT --- " + name + " getter IMP 0x" + Long.toHexString(imp)
                    + " (reads 0x" + Integer.toHexString(ivar) + ") ---");

            // before patch: ivar=0 => getter returns 0
            System.out.println("RESULT   pre-patch  ivar=0 -> " + callWith(imp, ivar, 0));

            // apply single-instruction patch at IMP+0x14
            long patchAddr = (base + ldrb) & 0xffffffffL;
            UnidbgPointer pp = UnidbgPointer.pointer(emulator, patchAddr);
            byte[] orig = new byte[4];
            for (int i = 0; i < 4; i++) orig[i] = pp.getByte(i);
            for (int i = 0; i < 4; i++) pp.setByte(i, MOV_R0_1[i]);
            System.out.printf("RESULT   patched 4 bytes @file 0x%x (0x%x): %02x%02x%02x%02x -> 01 00 A0 E3%n",
                    ldrb, patchAddr, orig[0]&0xff, orig[1]&0xff, orig[2]&0xff, orig[3]&0xff);

            // after patch: ivar=0 must now return 1; ivar=1 also 1
            System.out.println("RESULT   post-patch ivar=0 -> " + callWith(imp, ivar, 0) + "  (want 1)");
            System.out.println("RESULT   post-patch ivar=1 -> " + callWith(imp, ivar, 1) + "  (want 1)");

            // restore so targets don't interfere
            for (int i = 0; i < 4; i++) pp.setByte(i, orig[i]);
        }

        emulator.close();
        System.out.println("RESULT done.");
    }

    static long callWith(long imp, int ivarOff, int v) {
        MemoryBlock blk = emulator.getMemory().malloc(INST, true);
        UnidbgPointer self = blk.getPointer();
        try {
            for (int i = 0; i < INST; i++) self.setByte(i, (byte) 0);
            self.setByte(ivarOff, (byte) v);
            Number n = module.callFunction(emulator, imp, self.peer, 0L);
            return n == null ? -1 : (n.longValue() & 0xff);
        } finally {
            blk.free();
        }
    }
}
