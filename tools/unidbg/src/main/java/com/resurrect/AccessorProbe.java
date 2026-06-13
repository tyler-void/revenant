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
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * AccessorProbe — map BikeInfo setters/getters to instance byte offsets on a
 * SYNTHETIC instance (malloc 72, set ivars). No real save state, no store/loader.
 *
 * BikeInfo instance_size = 72. Confirmed BOOL ivars:
 *   loading_@0x8  purchased_@0x9  loaded@0xa  unlocked@0xb  _purchased@0xc
 *
 * Part 1 (setter->ivar): zero the instance, call setX:(1), diff every byte to see
 *   which offset the setter wrote.  _cmd is passed in r1, the BOOL arg in r2.
 * Part 2 (getter->ivar): for each candidate byte offset, set ONLY that byte to 1,
 *   call the getter, and report which offset the return value tracks.
 */
public class AccessorProbe {

    static AndroidEmulator emulator;
    static Module module;
    static long base;

    static final int INST = 72;            // BikeInfo instance_size
    // bytes we care about; print writes anywhere in 0..INST
    static final int[] WATCH = {0x8, 0x9, 0xa, 0xb, 0xc};

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

        // ---- Part 1: setters ----
        // BikeInfo-cluster setters (file offsets from /tmp/sel2imp.pkl)
        Map<String, Long> setters = new LinkedHashMap<>();
        setters.put("setPurchased:",   0x5eed38L);
        setters.put("setUnlocked:",    0x5eeab8L);
        setters.put("setLoaded:",      0x5ee888L);
        setters.put("setRevealed:",    0x50d960L);
        setters.put("setRevealCount:", 0x5eed80L);

        System.out.println("RESULT === SETTER -> IVAR (arg=1) ===");
        for (Map.Entry<String, Long> e : setters.entrySet()) {
            probeSetter(e.getKey(), e.getValue());
        }

        // ---- Part 2: getters ----
        Map<String, Long> getters = new LinkedHashMap<>();
        getters.put("purchased",   0x5eed14L); // known reads 0xc
        getters.put("unlocked",    0x5eea94L);
        getters.put("loaded",      0x5ee864L);
        getters.put("isRevealed",  0x5edeb0L);
        getters.put("revealed",    0x50d93cL);

        System.out.println("RESULT === GETTER -> IVAR (set single byte) ===");
        for (Map.Entry<String, Long> e : getters.entrySet()) {
            probeGetter(e.getKey(), e.getValue());
        }

        emulator.close();
        System.out.println("RESULT done.");
    }

    /** Zero instance, call setter with BOOL arg=1, diff bytes to find write offset. */
    static void probeSetter(String name, long impFileOff) {
        MemoryBlock blk = emulator.getMemory().malloc(INST, true);
        UnidbgPointer self = blk.getPointer();
        try {
            byte[] before = new byte[INST];
            for (int i = 0; i < INST; i++) { self.setByte(i, (byte) 0); before[i] = 0; }

            // unidbg 0.9.8 callFunction maps: r0=self, r1 forced to _cmd slot,
            // FIRST vararg lands in r2 (the strb value). Verified via SetterTrace:
            // passing (self, 0xAA) put 0xAA at the ivar offset. So value = 1st vararg.
            module.callFunction(emulator, impFileOff, self.peer, 1L /*value -> r2*/);

            StringBuilder writes = new StringBuilder();
            for (int i = 0; i < INST; i++) {
                byte now = self.getByte(i);
                if (now != before[i]) {
                    writes.append(String.format(" 0x%x=%d", i, now & 0xff));
                }
            }
            System.out.printf("RESULT setter %-16s IMP 0x%-7x writes:%s%n",
                    name, impFileOff, writes.length() == 0 ? " (none)" : writes.toString());
        } catch (Throwable t) {
            System.out.printf("RESULT setter %-16s IMP 0x%-7x CALL FAILED: %s%n", name, impFileOff, t);
        } finally {
            blk.free();
        }
    }

    /** For each watched byte: set ONLY it to 1, call getter, record return. */
    static void probeGetter(String name, long impFileOff) {
        StringBuilder line = new StringBuilder();
        MemoryBlock blk = emulator.getMemory().malloc(INST, true);
        UnidbgPointer self = blk.getPointer();
        try {
            // baseline: all-zero -> return should be 0
            for (int i = 0; i < INST; i++) self.setByte(i, (byte) 0);
            long base0 = ret(impFileOff, self);
            line.append("zero->").append(base0);

            for (int off : WATCH) {
                for (int i = 0; i < INST; i++) self.setByte(i, (byte) 0);
                self.setByte(off, (byte) 1);
                long r = ret(impFileOff, self);
                line.append(String.format("  0x%x->%d%s", off, r, (r == 1 && base0 == 0) ? "*" : ""));
            }
            System.out.printf("RESULT getter %-12s IMP 0x%-7x : %s%n", name, impFileOff, line);
        } catch (Throwable t) {
            System.out.printf("RESULT getter %-12s IMP 0x%-7x CALL FAILED: %s%n", name, impFileOff, t);
        } finally {
            blk.free();
        }
    }

    static long ret(long impFileOff, UnidbgPointer self) {
        Number n = module.callFunction(emulator, impFileOff, self.peer, 0L);
        return n == null ? -1 : (n.longValue() & 0xff);
    }
}
