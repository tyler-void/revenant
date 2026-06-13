package com.resurrect;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.Module;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.linux.android.dvm.DalvikModule;
import com.github.unidbg.linux.android.dvm.VM;
import com.github.unidbg.memory.Memory;
import com.github.unidbg.pointer.UnidbgPointer;

import java.io.File;

/**
 * Milestone 3 (independent in-emulator confirmation):
 * Walk the *relocated* GNUstep/Apportable BikeInfo class struct inside unidbg and
 * print every ivar name+type+offset, reading them from emulator memory (so the
 * R_ARM_RELATIVE relocations are applied — i.e. the live in-memory layout, not the
 * static file image).
 *
 * GNUstep/Apportable ObjC class struct (vaddr==fileoff; instance ivar list is on
 * the class object whose name field is at file 0xcadfc8 -> class base 0xcadfc0):
 *   +0x00 isa  +0x04 super  +0x08 name  +0x0c version  +0x10 info
 *   +0x14 instance_size  +0x18 ivars  +0x1c methods ...
 * ivar_list: { int32 count; objc_ivar[count] }, objc_ivar = {char* name; char* type; int32 offset}
 */
public class BikeInfoDump {
    static final long CLASS_FILEOFF = 0xcadfc0L; // BikeInfo class object holding the ivar list

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

        long cls = base + CLASS_FILEOFF;
        UnidbgPointer pCls = UnidbgPointer.pointer(emulator, cls);
        long isa     = u32(pCls, 0x00);
        long sup     = u32(pCls, 0x04);
        long namePtr = u32(pCls, 0x08);
        long isize   = u32(pCls, 0x14);
        long ivars   = u32(pCls, 0x18);
        long methods = u32(pCls, 0x1c);
        System.out.println("[*] BikeInfo class @0x" + Long.toHexString(cls)
                + " name='" + cstr(emulator, namePtr) + "'"
                + " instance_size=" + (int) isize
                + " ivars_ptr=0x" + Long.toHexString(ivars)
                + " methods_ptr=0x" + Long.toHexString(methods));

        if (ivars == 0) { System.out.println("[!] ivars ptr null"); emulator.close(); return; }
        UnidbgPointer pIv = UnidbgPointer.pointer(emulator, ivars);
        int count = (int) u32(pIv, 0);
        System.out.println("[*] ivar count = " + count);
        System.out.println("[*] --- BikeInfo ivar layout (live, relocated) ---");
        for (int k = 0; k < count && k < 64; k++) {
            long ent = ivars + 4 + (long) k * 12; // 12-byte objc_ivar
            UnidbgPointer pe = UnidbgPointer.pointer(emulator, ent);
            long nmp = u32(pe, 0);
            long typ = u32(pe, 4);
            long off = u32(pe, 8);
            String nm = cstr(emulator, nmp);
            String ty = cstr(emulator, typ);
            String tag = "";
            if ("_purchased".equals(nm)) tag = "   <== 'purchased' getter (IMP 0x5eed14) reads THIS";
            if ("purchased_".equals(nm)) tag = "   <== alt purchased BOOL";
            if ("unlocked".equals(nm))   tag = "   <== 'unlocked' BOOL";
            System.out.printf("[*]   %-18s type='%s'  offset=0x%x (%d)%s%n", nm, ty, off, off, tag);
        }
        emulator.close();
        System.out.println("[*] done.");
    }

    static long u32(UnidbgPointer p, int off) {
        return p.getInt(off) & 0xffffffffL;
    }

    static String cstr(AndroidEmulator emu, long addr) {
        if (addr == 0) return null;
        UnidbgPointer p = UnidbgPointer.pointer(emu, addr);
        return p == null ? null : new String(p.getString(0));
    }
}
