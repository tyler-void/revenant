package com.resurrect;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.Module;
import com.github.unidbg.arm.backend.Backend;
import com.github.unidbg.arm.backend.CodeHook;
import com.github.unidbg.arm.backend.UnHook;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.linux.android.dvm.DalvikModule;
import com.github.unidbg.linux.android.dvm.VM;
import com.github.unidbg.memory.Memory;
import com.github.unidbg.memory.MemoryBlock;
import com.github.unidbg.pointer.UnidbgPointer;
import unicorn.ArmConst;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

/**
 * Deeper probe: instruction-trace each getter to confirm the ivar-offset variable
 * is class-specific (not a fallback default). Trace the whole IMP and dump
 * r1 at each step + the offset-variable address it dereferences.
 */
public class IvarProbe {
    static AndroidEmulator emulator;
    static Module module;
    static long base;

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
        System.out.println("[*] base=0x" + Long.toHexString(base));

        // getter file offsets and their selector name
        long[][] gs = {
            {0x5eed14L, 0}, // purchased
            {0x50d93cL, 1}, // revealed
        };
        String[] nm = {"purchased", "revealed"};

        for (long[] g : gs) {
            traceGetter(nm[(int) g[1]], g[0]);
        }
        emulator.close();
    }

    static void traceGetter(String name, long impFileOff) {
        System.out.println("\n[*] === trace getter '" + name + "' IMP 0x" + Long.toHexString(impFileOff) + " ===");
        final Backend backend = emulator.getBackend();
        MemoryBlock blk = emulator.getMemory().malloc(512, true);
        final UnidbgPointer self = blk.getPointer();
        for (int i = 0; i < 512; i++) self.setByte(i, (byte) 0x77);

        final long impAddr = (base + impFileOff) & 0xffffffffL;
        final List<String> trace = new ArrayList<>();
        UnHook[] uh = new UnHook[1];
        CodeHook hook = new CodeHook() {
            @Override public void hook(Backend b, long address, int size, Object user) {
                long r0 = b.reg_read(ArmConst.UC_ARM_REG_R0).intValue() & 0xffffffffL;
                long r1 = b.reg_read(ArmConst.UC_ARM_REG_R1).intValue() & 0xffffffffL;
                long r2 = b.reg_read(ArmConst.UC_ARM_REG_R2).intValue() & 0xffffffffL;
                trace.add(String.format("    0x%x  r0=0x%x r1=0x%x r2=0x%x", address, r0, r1, r2));
            }
            @Override public void onAttach(UnHook unHook) { uh[0] = unHook; }
            @Override public void detach() { if (uh[0] != null) uh[0].unhook(); }
        };
        backend.hook_add_new(hook, impAddr, impAddr + 0x40, null);
        try {
            Number ret = module.callFunction(emulator, impFileOff, self.peer, 0L);
            for (String t : trace) System.out.println(t);
            System.out.println("    -> returned 0x" + Long.toHexString(ret == null ? -1 : ret.longValue() & 0xff));
        } catch (Throwable t) {
            System.out.println("    CALL FAILED: " + t);
        } finally {
            hook.detach();
            blk.free();
        }
    }
}
