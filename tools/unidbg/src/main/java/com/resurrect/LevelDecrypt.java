package com.resurrect;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.Emulator;
import com.github.unidbg.Module;
import com.github.unidbg.arm.backend.Backend;
import com.github.unidbg.arm.backend.CodeHook;
import com.github.unidbg.arm.backend.UnHook;
import com.github.unidbg.file.FileResult;
import com.github.unidbg.file.IOResolver;
import com.github.unidbg.file.linux.AndroidFileIO;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.linux.android.dvm.DalvikModule;
import com.github.unidbg.linux.android.dvm.VM;
import com.github.unidbg.linux.file.SimpleFileIO;
import com.github.unidbg.memory.Memory;
import com.github.unidbg.memory.MemoryBlock;
import unicorn.ArmConst;

import java.io.File;

/**
 * Phase-2 unblock: run the game's own no-password +[NSData DataWithContentsOfFile:]
 * (IMP 0x64f378) on a fabricated NSString path, serving the real level file via an
 * IOResolver so the read succeeds and the decrypt path runs. Hook the decrypt
 * methods to capture the constant Password (r3), and log the returned NSData.
 */
public class LevelDecrypt {
    static final long DWF_PW   = 0x64ea98L; // +[NSData DataWithContentsOfFile:Password:]
    static final long DWF_NOPW = 0x64f378L; // +[NSData DataWithContentsOfFile:]
    static final long DEC_PW   = 0x64e93cL; // +[NSData DataDecryptedFromData:Password:]
    static final long NSCONST_SRC = 0xc54ad0L;

    static AndroidEmulator emulator;

    static int rd32(long a){ byte[] b=emulator.getBackend().mem_read(a&0xffffffffL,4);
        return (b[0]&0xff)|((b[1]&0xff)<<8)|((b[2]&0xff)<<16)|((b[3]&0xff)<<24); }
    static String cstr(long a,int max){ StringBuilder s=new StringBuilder();
        byte[] b=emulator.getBackend().mem_read(a&0xffffffffL,max);
        for(int i=0;i<max;i++){int c=b[i]&0xff; if(c==0)break; s.append(c>=32&&c<127?(char)c:'.');} return s.toString(); }
    static byte[] le(int v){ return new byte[]{(byte)v,(byte)(v>>8),(byte)(v>>16),(byte)(v>>24)}; }

    static File levelFile(){
        for(String p: new String[]{"../../build/work/assets/unpack/1_1.dat","build/work/assets/unpack/1_1.dat"}){
            File f=new File(p); if(f.exists()) return f.getAbsoluteFile();
        }
        return new File("1_1.dat").getAbsoluteFile();
    }

    public static void main(String[] args) throws Exception {
        File so = new File("src/main/resources/libgame.so");
        if(!so.exists()) so=new File("tools/unidbg/src/main/resources/libgame.so");
        emulator = AndroidEmulatorBuilder.for32Bit().setProcessName("com.miniclip.bikerivals").build();
        Memory memory = emulator.getMemory();
        memory.setLibraryResolver(new AndroidResolver(23));

        final File lvl = levelFile();
        log("level file: " + lvl + " exists=" + lvl.exists() + " size=" + lvl.length());
        emulator.getSyscallHandler().addIOResolver(new IOResolver<AndroidFileIO>(){
            public FileResult<AndroidFileIO> resolve(Emulator<AndroidFileIO> emu, String path, int oflags){
                log("  [vfs] resolve("+path+", oflags=0x"+Integer.toHexString(oflags)+")");
                if(path!=null && (path.endsWith(".dat") || path.contains("1_1"))){
                    log("  [vfs]   -> serving "+lvl.getName());
                    return FileResult.<AndroidFileIO>success(new SimpleFileIO(oflags, lvl, path));
                }
                return null;
            }
        });

        VM vm = emulator.createDalvikVM();
        vm.setVerbose(false);
        DalvikModule dm = vm.loadLibrary(so, false);
        Module module = dm.getModule();
        long base = module.base;
        final Backend backend = emulator.getBackend();
        log("loaded base=0x"+Long.toHexString(base));

        int isa = rd32(base+NSCONST_SRC);
        // fabricate NSConstantString path
        byte[] path = "1_1.dat".getBytes();
        MemoryBlock cblk = memory.malloc(path.length+1,true);
        long cstrAddr = cblk.getPointer().peer & 0xffffffffL;
        byte[] pz=new byte[path.length+1]; System.arraycopy(path,0,pz,0,path.length);
        backend.mem_write(cstrAddr, pz);
        MemoryBlock sblk = memory.malloc(12,true);
        long sAddr = sblk.getPointer().peer & 0xffffffffL;
        byte[] st=new byte[12];
        System.arraycopy(le(isa),0,st,0,4);
        System.arraycopy(le((int)cstrAddr),0,st,4,4);
        System.arraycopy(le(path.length),0,st,8,4);
        backend.mem_write(sAddr, st);
        log("fabricated NSString \""+cstr(cstrAddr,16)+"\" @0x"+Long.toHexString(sAddr));

        final long nopw=(base+DWF_NOPW)&0xffffffffL, dwfpw=(base+DWF_PW)&0xffffffffL, dec=(base+DEC_PW)&0xffffffffL;
        final String[] pw = {null};
        CodeHook hook = new CodeHook(){
            public void hook(Backend b, long address, int size, Object u){
                if(address==nopw) log("  >> no-pw DataWithContentsOfFile: entered");
                else if(address==dwfpw || address==dec){
                    String which = address==dwfpw ? "DataWithContentsOfFile:Password:" : "DataDecryptedFromData:Password:";
                    long r2=b.reg_read(ArmConst.UC_ARM_REG_R2).intValue()&0xffffffffL;
                    long r3=b.reg_read(ArmConst.UC_ARM_REG_R3).intValue()&0xffffffffL;
                    log("  >> "+which+"  r2=0x"+Long.toHexString(r2)+" r3=0x"+Long.toHexString(r3));
                    try {
                        long pcstr=rd32(r3+4)&0xffffffffL;
                        String s=cstr(pcstr,64);
                        log("     r3.isa=0x"+Integer.toHexString(rd32(r3))+" [r3+4]->\""+s+"\"  raw[r3..]="+cstr(r3,24));
                        if(s.length()>0 && pw[0]==null) pw[0]=s;
                    } catch(Throwable t){ log("     read fail: "+t); }
                }
            }
            public void onAttach(UnHook h){}
            public void detach(){}
        };
        backend.hook_add_new(hook, nopw, nopw+4, null);
        backend.hook_add_new(hook, dwfpw, dwfpw+4, null);
        backend.hook_add_new(hook, dec, dec+4, null);

        try {
            Number ret = module.callFunction(emulator, DWF_NOPW, 0L, 0L, sAddr);
            long r=ret==null?0:ret.longValue()&0xffffffffL;
            log("no-pw returned 0x"+Long.toHexString(r));
            if(r!=0){
                // result is an NSData*; dump a little of its struct + try common bytes-ptr layouts
                log("  result NSData @0x"+Long.toHexString(r)+" struct="+cstr(r,4));
            }
        } catch(Throwable t){ log("call threw: "+t); }
        log("=== captured PASSWORD = " + (pw[0]==null?"(none)":("\""+pw[0]+"\"")));
        emulator.close();
    }
    static void log(String s){ System.out.println("[LD] "+s); }
}
