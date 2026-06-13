package com.resurrect;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.Module;
import com.github.unidbg.arm.backend.Backend;
import com.github.unidbg.arm.backend.CodeHook;
import com.github.unidbg.arm.backend.UnHook;
import com.github.unidbg.arm.context.RegisterContext;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.linux.android.dvm.DalvikModule;
import com.github.unidbg.linux.android.dvm.VM;
import com.github.unidbg.memory.Memory;
import com.github.unidbg.memory.MemoryBlock;
import com.github.unidbg.pointer.UnidbgPointer;
import unicorn.Arm64Const;
import unicorn.ArmConst;

import java.io.File;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Bike Rivals — unidbg cracker.
 *
 * Milestone 1: load libgame.so (32-bit ARM).
 * Milestone 3: recover BikeInfo ivar offsets by CALLING the ObjC getter IMPs
 *              (purchased / revealed / locked) on a controlled fake instance and
 *              hooking the `ldrb r0,[r0,r1]` instruction to read r1 = ivar byte offset.
 *
 * Why this works where static analysis failed: GNUstep/Apportable libobjc uses
 * NON-FRAGILE ivars. The getter doesn't embed a literal offset; it loads it from a
 * runtime-populated ivar-offset variable (filled at class load via relocations /
 * __objc_exec_class). unidbg applies relocations + runs init_array, so by the time
 * we call the getter the variable holds the real offset.
 */
public class BikeRivalsRunner {

    // file offset == vaddr for this .so
    static final long PURCHASED_GETTER   = 0x5eed14L; // 'purchased' getter, ldrb at +0x14
    static final long REVEALED_GETTER    = 0x50d93cL; // 'revealed' getter,  ldrb at +0x14
    static final long ISBIKEUNLOCKED     = 0x6a3f24L; // isBikeUnlocked:ArrayFile:
    static final long GETBIKEINFO        = 0x6a3ae4L; // getBikeInfoFromBid:ArrayFile:

    static AndroidEmulator emulator;
    static Module module;
    static long base;

    public static void main(String[] args) throws Exception {
        File so = new File("src/main/resources/libgame.so");
        if (!so.exists()) so = new File("tools/unidbg/src/main/resources/libgame.so");
        log("libgame.so = " + so.getAbsolutePath() + " exists=" + so.exists());

        emulator = AndroidEmulatorBuilder.for32Bit()
                .setProcessName("com.miniclip.bikerivals")
                .build();
        Memory memory = emulator.getMemory();
        memory.setLibraryResolver(new AndroidResolver(23));
        VM vm = emulator.createDalvikVM();
        vm.setVerbose(false);

        DalvikModule dm = vm.loadLibrary(so, false); // do NOT run JNI_OnLoad
        module = dm.getModule();
        base = module.base;
        log("MILESTONE 1: loaded. base=0x" + Long.toHexString(base)
                + " size=0x" + Long.toHexString(module.size));

        // ---- Milestone 3: recover ivar offsets via getter call + instruction hook ----
        Map<String, Long> getters = new LinkedHashMap<>();
        getters.put("purchased", PURCHASED_GETTER);
        getters.put("revealed",  REVEALED_GETTER);

        log("");
        log("MILESTONE 3: recovering BikeInfo ivar offsets by emulating getters");
        for (Map.Entry<String, Long> e : getters.entrySet()) {
            recoverIvarOffset(e.getKey(), e.getValue());
        }

        emulator.close();
        log("done.");
    }

    /**
     * Call an ObjC boolean getter IMP and capture the ivar byte offset it loads.
     * The getter shape is:  ... ; ldr r1,[r1] (r1 = ivar offset) ; ldrb r0,[r0,r1] ; bx lr
     * We hook the `ldrb` instruction and read r1 right before it executes.
     */
    static void recoverIvarOffset(String name, long impFileOff) {
        final Backend backend = emulator.getBackend();
        // Build a fake BikeInfo instance: 512 zero bytes. self = ptr to it.
        MemoryBlock instBlock = emulator.getMemory().malloc(512, true);
        final UnidbgPointer self = instBlock.getPointer();
        // sentinel: write 0x01 at every byte so a BOOL read returns 1 regardless of offset
        for (int i = 0; i < 512; i++) self.setByte(i, (byte) 1);

        final long impAddr = base + impFileOff;
        final long ldrbAddr = base + impFileOff + 0x14; // the ldrb r0,[r0,r1] for these getters
        final long[] captured = { -1L };

        // hook only the ldrb instruction
        UnHook[] uh = new UnHook[1];
        CodeHook hook = new CodeHook() {
            @Override public void hook(Backend b, long address, int size, Object user) {
                if (address == (ldrbAddr & 0xffffffffL)) {
                    long r1 = b.reg_read(ArmConst.UC_ARM_REG_R1).intValue() & 0xffffffffL;
                    long r0 = b.reg_read(ArmConst.UC_ARM_REG_R0).intValue() & 0xffffffffL;
                    captured[0] = r1;
                    log("    [" + name + "] at ldrb: r1(ivar offset)=0x" + Long.toHexString(r1)
                            + " (=" + r1 + " dec)  r0(self)=0x" + Long.toHexString(r0));
                }
            }
            @Override public void onAttach(UnHook unHook) { uh[0] = unHook; }
            @Override public void detach() { if (uh[0] != null) uh[0].unhook(); }
        };
        // restrict hook to this IMP's range
        backend.hook_add_new(hook, impAddr & 0xffffffffL, (impAddr + 0x40) & 0xffffffffL, null);

        try {
            // call: r0=self, r1=_cmd(SEL) — getter ignores _cmd. emulate from impAddr.
            Number ret = module.callFunction(emulator, impFileOff, self.peer, 0L);
            long r0 = (ret == null ? -1 : ret.longValue() & 0xff);
            log("    [" + name + "] getter returned r0=0x" + Long.toHexString(r0)
                    + "  => ivar byte offset = "
                    + (captured[0] >= 0 ? ("0x" + Long.toHexString(captured[0]) + " (" + captured[0] + ")") : "NOT CAPTURED"));
        } catch (Throwable t) {
            log("    [" + name + "] CALL FAILED: " + t);
        } finally {
            hook.detach();
            instBlock.free();
        }
    }

    static void log(String s) { System.out.println("[*] " + s); }
}
