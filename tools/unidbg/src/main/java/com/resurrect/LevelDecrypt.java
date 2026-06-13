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
    static final long SETKEY   = 0x650570L; // cipher_setkey(ctx, key)
    static final long PROCESS  = 0x65085cL; // cipher_process(ctx, data, len)
    static final long SEL_REG  = 0x3775e0L; // sel_registerName(char*)
    static final long MSGSEND  = 0x3783d4L; // objc_msgSend(recv, sel, ...)
    static final long NSCONST_SRC = 0xc54ad0L;
    static Module MOD;

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

        final File lvl = levelFile();
        log("level file: " + lvl + " exists=" + lvl.exists() + " size=" + lvl.length());
        // back the VFS with a real rootfs so stat()+open() of "1/1_1.dat" both succeed
        File rootfs = new File("/tmp/br-rootfs");
        new File(rootfs, "1").mkdirs();
        java.nio.file.Files.copy(lvl.toPath(), new File(rootfs, "1/1_1.dat").toPath(),
                java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        log("rootfs: " + rootfs + "/1/1_1.dat");

        AndroidEmulatorBuilder builder = AndroidEmulatorBuilder.for32Bit();
        builder.setProcessName("com.miniclip.bikerivals");
        builder.setRootDir(rootfs);
        emulator = builder.build();
        Memory memory = emulator.getMemory();
        memory.setLibraryResolver(new AndroidResolver(23));
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
        DalvikModule dm = vm.loadLibrary(so, true); // forceCallInit=true -> runs init_array / __objc_exec_class (registers ObjC classes)
        Module module = dm.getModule();
        long base = module.base;
        final Backend backend = emulator.getBackend();
        log("loaded base=0x"+Long.toHexString(base));

        int isa = rd32(base+NSCONST_SRC);
        // 0x64ea98 decrypts the object passed as r2 (calls [arg bytes]/[arg length]).
        // Fabricate an NSData-like object {isa, bytesPtr, length} over the real level file bytes.
        byte[] fileBytes = java.nio.file.Files.readAllBytes(lvl.toPath());
        MemoryBlock dblk = memory.malloc(fileBytes.length, true);
        long dataPtr = dblk.getPointer().peer & 0xffffffffL;
        backend.mem_write(dataPtr, fileBytes);
        MemoryBlock sblk = memory.malloc(12,true);
        long sAddr = sblk.getPointer().peer & 0xffffffffL;
        byte[] st=new byte[12];
        System.arraycopy(le(isa),0,st,0,4);
        System.arraycopy(le((int)dataPtr),0,st,4,4);
        System.arraycopy(le(fileBytes.length),0,st,8,4);
        backend.mem_write(sAddr, st);
        log("fabricated data obj @0x"+Long.toHexString(sAddr)+" bytes=0x"+Long.toHexString(dataPtr)+" len="+fileBytes.length);

        MOD = module;
        final long dwfpw=(base+DWF_PW)&0xffffffffL, dec=(base+DEC_PW)&0xffffffffL;
        final long setkey=(base+SETKEY)&0xffffffffL, process=(base+PROCESS)&0xffffffffL;
        CodeHook hook = new CodeHook(){
            public void hook(Backend b, long address, int size, Object u){
                long r0=b.reg_read(ArmConst.UC_ARM_REG_R0).intValue()&0xffffffffL;
                long r1=b.reg_read(ArmConst.UC_ARM_REG_R1).intValue()&0xffffffffL;
                long r2=b.reg_read(ArmConst.UC_ARM_REG_R2).intValue()&0xffffffffL;
                long r3=b.reg_read(ArmConst.UC_ARM_REG_R3).intValue()&0xffffffffL;
                if(address==dwfpw) log("  >> DataWithContentsOfFile:Password:  r0=0x"+Long.toHexString(r0)+" r1=0x"+Long.toHexString(r1)+" r2(path)=0x"+Long.toHexString(r2)+" r3(pw)=0x"+Long.toHexString(r3));
                else if(address==dec) log("  >> DataDecryptedFromData:Password:  data=0x"+Long.toHexString(r2)+" pw=0x"+Long.toHexString(r3));
                else if(address==setkey){
                    log("  >> cipher_setkey(ctx=0x"+Long.toHexString(r0)+", key=0x"+Long.toHexString(r1)+")");
                    if(r1!=0){ try{ log("       key NSStr[+4]->\""+cstr(rd32(r1+4)&0xffffffffL,48)+"\"  raw=\""+cstr(r1,32)+"\""); }catch(Throwable t){} }
                }
                else if(address==process) log("  >> cipher_process(ctx=0x"+Long.toHexString(r0)+", data=0x"+Long.toHexString(r1)+", len="+r2+")");
            }
            public void onAttach(UnHook h){}
            public void detach(){}
        };
        for(long a: new long[]{dwfpw,dec,setkey,process}) backend.hook_add_new(hook, a, a+4, null);

        // Call DataWithContentsOfFile:Password: IMP directly (self/_cmd unused), path=fabricated, pw=nil
        log("=== MY CALL NOW: DWF_PW(self=0,_cmd=0,path=0x"+Long.toHexString(sAddr)+",pw=0) ===");
        try {
            Number ret = module.callFunction(emulator, DWF_PW, 0, 0, (int) sAddr, 0); // r0=self,r1=_cmd,r2=path,r3=pw(nil)
            long r=ret==null?0:ret.longValue()&0xffffffffL;
            log("DataWithContentsOfFile:Password: returned 0x"+Long.toHexString(r));
            if(r!=0) dumpNSData(r);
        } catch(Throwable t){ log("call threw: "+t); }
        emulator.close();
    }
    static long cstrAlloc(String s){
        byte[] b=(s+"\0").getBytes();
        MemoryBlock m=emulator.getMemory().malloc(b.length,true);
        long a=m.getPointer().peer&0xffffffffL;
        emulator.getBackend().mem_write(a,b);
        return a;
    }
    static void dumpNSData(long obj){
        try{
            long selB=MOD.callFunction(emulator,SEL_REG,(int)cstrAlloc("bytes")).longValue()&0xffffffffL;
            long selL=MOD.callFunction(emulator,SEL_REG,(int)cstrAlloc("length")).longValue()&0xffffffffL;
            long ptr=MOD.callFunction(emulator,MSGSEND,(int)obj,(int)selB).longValue()&0xffffffffL;
            long len=MOD.callFunction(emulator,MSGSEND,(int)obj,(int)selL).longValue()&0xffffffffL;
            log("  NSData -bytes=0x"+Long.toHexString(ptr)+" -length="+len);
            if(ptr!=0 && len>0 && len<10_000_000){
                int n=(int)Math.min(len,4096);
                byte[] data=emulator.getBackend().mem_read(ptr,n);
                int pr=0; for(byte x:data){int c=x&0xff; if((c>=32&&c<127)||c==9||c==10||c==13) pr++;}
                String head=new String(data,0,Math.min(n,220)).replaceAll("[^\\x20-\\x7e\\n\\t]",".");
                log("  printable="+(100*pr/n)+"%  head="+head);
                byte[] full=emulator.getBackend().mem_read(ptr,(int)Math.min(len,2_000_000));
                java.nio.file.Files.write(java.nio.file.Paths.get("/tmp/1_1.decrypted"), full);
                log("  wrote /tmp/1_1.decrypted ("+full.length+" bytes)");
            }
        }catch(Throwable t){ log("  dumpNSData fail: "+t); }
    }
    static void log(String s){ System.out.println("[LD] "+s); }
}
