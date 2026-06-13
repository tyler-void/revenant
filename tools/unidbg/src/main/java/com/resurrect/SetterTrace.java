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

/**
 * Trace setPurchased: (0x5eed38) at the strb r2,[r0,r1] instruction (+0x14)
 * to see exactly what r0/r1/r2 hold under module.callFunction, so we know how
 * the BOOL arg must be passed.
 */
public class SetterTrace {
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
        System.out.println("RESULT base=0x" + Long.toHexString(base));

        long imp = 0x5eed38L;        // setPurchased:
        long strb = imp + 0x14;      // strb r2,[r0,r1]
        trace("setPurchased:", imp, strb);

        emulator.close();
        System.out.println("RESULT done.");
    }

    static void trace(String name, long impFileOff, long strbFileOff) {
        Backend backend = emulator.getBackend();
        MemoryBlock blk = emulator.getMemory().malloc(72, true);
        UnidbgPointer self = blk.getPointer();
        for (int i = 0; i < 72; i++) self.setByte(i, (byte) 0);

        long impAddr = (base + impFileOff) & 0xffffffffL;
        long strbAddr = (base + strbFileOff) & 0xffffffffL;
        UnHook[] uh = new UnHook[1];
        CodeHook hook = new CodeHook() {
            @Override public void hook(Backend b, long address, int size, Object user) {
                long r0 = b.reg_read(ArmConst.UC_ARM_REG_R0).intValue() & 0xffffffffL;
                long r1 = b.reg_read(ArmConst.UC_ARM_REG_R1).intValue() & 0xffffffffL;
                long r2 = b.reg_read(ArmConst.UC_ARM_REG_R2).intValue() & 0xffffffffL;
                System.out.printf("RESULT   @0x%x r0=0x%x r1=0x%x r2=0x%x%s%n",
                        address, r0, r1, r2,
                        address == strbAddr ? "  <== strb (r1=ivar off, r2=value)" : "");
            }
            @Override public void onAttach(UnHook unHook) { uh[0] = unHook; }
            @Override public void detach() { if (uh[0] != null) uh[0].unhook(); }
        };
        backend.hook_add_new(hook, impAddr, impAddr + 0x20, null);
        try {
            module.callFunction(emulator, impFileOff, self.peer, 0xAAAAL /*_cmd*/, 1L /*value*/);
            StringBuilder w = new StringBuilder();
            for (int i = 0; i < 72; i++) if (self.getByte(i) != 0) w.append(String.format(" 0x%x=%d", i, self.getByte(i) & 0xff));
            System.out.println("RESULT   after call, nonzero bytes:" + (w.length() == 0 ? " (none)" : w));
        } catch (Throwable t) {
            System.out.println("RESULT   FAILED: " + t);
        } finally {
            hook.detach();
            blk.free();
        }
    }
}
