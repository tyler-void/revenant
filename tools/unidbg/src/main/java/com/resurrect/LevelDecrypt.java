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
    static final long GETCLASS = 0x37295cL; // objc_getClass(char*) — called with "NSData"/"Encryption"
    static final long DATAWBYTES = 0x398c48L; // +[NSData dataWithBytes:length:]
    static final long STRWUTF8 = 0x408000L;   // +[NSString stringWithUTF8String:]
    static long CLS_NSSTRING;
    static final long NSCONST_SRC = 0xc54ad0L;
    static Module MOD;
    static int NSCONST_ISA;

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

        final Backend backend = emulator.getBackend();
        // Install hooks at the EXPECTED load base BEFORE init runs, so the game's OWN
        // startup config-decryption is captured (reveals the real key) — we missed it before.
        final long EB = 0x40000000L;
        final long dwfpw=(EB+DWF_PW)&0xffffffffL, dec=(EB+DEC_PW)&0xffffffffL;
        final long setkey=(EB+SETKEY)&0xffffffffL, process=(EB+PROCESS)&0xffffffffL;
        CodeHook hook = new CodeHook(){
            public void hook(Backend b, long address, int size, Object u){
                long r0=b.reg_read(ArmConst.UC_ARM_REG_R0).intValue()&0xffffffffL;
                long r1=b.reg_read(ArmConst.UC_ARM_REG_R1).intValue()&0xffffffffL;
                long r2=b.reg_read(ArmConst.UC_ARM_REG_R2).intValue()&0xffffffffL;
                long r3=b.reg_read(ArmConst.UC_ARM_REG_R3).intValue()&0xffffffffL;
                if(address==dwfpw) log("  >> decrypt(0x64ea98) data=0x"+Long.toHexString(r2)+" pw=0x"+Long.toHexString(r3));
                else if(address==dec) log("  >> DataDecryptedFromData(0x64e93c) data=0x"+Long.toHexString(r2)+" pw=0x"+Long.toHexString(r3));
                else if(address==setkey){
                    log("  >> cipher_setkey ctx=0x"+Long.toHexString(r0)+" key=0x"+Long.toHexString(r1));
                    if(r1!=0){ try{
                        log("       raw[key]=\""+cstr(r1,40)+"\"");
                        for(int off: new int[]{0,4,8,12,16}){ long p=rd32(r1+off)&0xffffffffL; if(p>0x1000){ String s=cstr(p,48); if(s.length()>=3) log("       [key+"+off+"]->0x"+Long.toHexString(p)+" = \""+s+"\""); } }
                    }catch(Throwable t){} }
                }
                else if(address==process) log("  >> cipher_process ctx=0x"+Long.toHexString(r0)+" data=0x"+Long.toHexString(r1)+" len="+r2);
            }
            public void onAttach(UnHook h){}
            public void detach(){}
        };
        for(long a: new long[]{dwfpw,dec,setkey,process}) backend.hook_add_new(hook, a, a+4, null);

        VM vm = emulator.createDalvikVM();
        vm.setVerbose(false);
        log("=== loadLibrary(forceCallInit=true) — watching for STARTUP decryption ===");
        DalvikModule dm = vm.loadLibrary(so, true);
        Module module = dm.getModule();
        long base = module.base;
        log("loaded base=0x"+Long.toHexString(base)+(base!=EB?"  !! base != 0x40000000, hooks misplaced":""));
        MOD = module;
        try { log("=== calling JNI_OnLoad (may trigger native init/config decrypt) ==="); dm.callJNI_OnLoad(emulator); log("JNI_OnLoad returned"); }
        catch(Throwable t){ log("JNI_OnLoad: "+t); }

        NSCONST_ISA = rd32(base+NSCONST_SRC);
        long clsNSData = module.callFunction(emulator, GETCLASS, (int)cstrAlloc("NSData")).longValue()&0xffffffffL;
        CLS_NSSTRING = module.callFunction(emulator, GETCLASS, (int)cstrAlloc("NSString")).longValue()&0xffffffffL;
        log("objc_getClass NSData=0x"+Long.toHexString(clsNSData)+" NSString=0x"+Long.toHexString(CLS_NSSTRING));
        // sanity: a real NSString should report the right length
        long ts=makeNSString("hello"); log("test NSString(\"hello\").length="+module.callFunction(emulator,MSGSEND,(int)ts,(int)module.callFunction(emulator,SEL_REG,(int)cstrAlloc("length")).longValue()).longValue());
        byte[] fileBytes = java.nio.file.Files.readAllBytes(lvl.toPath());

        // === decrypt with the ON-DEVICE-CAPTURED key, passed as a RAW char* (not an NSString!) ===
        // (config key = 50 bytes, captured on-device; level key differs — separate cipher)
        // Key is captured per-device with build/patch_keylog.py (logged to logcat tag RVKEY) and
        // supplied via the BR_KEY env var as hex — NOT committed (a cipher key is circumvention
        // material; see docs/PRESERVATION-PLAYBOOK.md / LEGAL.md). The config key decrypts
        // ProductList/Shop/GameConfig/ConditionInfo; levels use a separate (Pass2) cipher.
        String keyHex = System.getenv("BR_KEY");
        if (keyHex == null || keyHex.isEmpty()) { log("set BR_KEY=<hex> (capture via build/patch_keylog.py + RVKEY logcat)"); emulator.close(); return; }
        byte[] key = new byte[keyHex.length()/2];
        for(int i=0;i<key.length;i++) key[i]=(byte)Integer.parseInt(keyHex.substring(2*i,2*i+2),16);
        MemoryBlock kb = memory.malloc(key.length+1, true);
        long keyPtr = kb.getPointer().peer & 0xffffffffL;
        byte[] kz=new byte[key.length+1]; System.arraycopy(key,0,kz,0,key.length);
        backend.mem_write(keyPtr, kz);
        log("captured key: "+key.length+" bytes @0x"+Long.toHexString(keyPtr));
        // captured key is the CONFIG key -> test it on a config file (ProductList.dat) to validate
        java.io.File dir=new java.io.File("../../build/work/assets/unpack");
        if(!dir.isDirectory()) dir=new java.io.File("build/work/assets/unpack");
        byte[] fb=java.nio.file.Files.readAllBytes(new java.io.File(dir,"1_1.dat").toPath());
        {   // dump 0x64ea98(1_1, level key) raw output for nibble-swap analysis
            long nsdata = makeNSData(clsNSData, fb);
            long r = module.callFunction(emulator, DWF_PW, 0, 0, (int)nsdata, (int)keyPtr).longValue()&0xffffffffL;
            log("=== 1_1.dat via 0x64ea98 -> "+pctPrintable(r)+"% (dumping raw) ===");
            if(r!=0 && r!=0xffffffffL){
                long selB=module.callFunction(emulator,SEL_REG,(int)cstrAlloc("bytes")).longValue()&0xffffffffL;
                long selL=module.callFunction(emulator,SEL_REG,(int)cstrAlloc("length")).longValue()&0xffffffffL;
                long ptr=module.callFunction(emulator,MSGSEND,(int)r,(int)selB).longValue()&0xffffffffL;
                long len=module.callFunction(emulator,MSGSEND,(int)r,(int)selL).longValue()&0xffffffffL;
                byte[] full=emulator.getBackend().mem_read(ptr,(int)Math.min(len,2000000));
                java.nio.file.Files.write(java.nio.file.Paths.get("/tmp/raw_1_1.bin"), full);
                log("   wrote /tmp/raw_1_1.bin ("+full.length+" B)");
            }
        }

        // CAPTURE the real password: run the game's own no-pw DataWithContentsOfFile: with a REAL
        // NSString path; it reads the file (rootfs/IOResolver serve it) and, for encrypted files,
        // decrypts via the Encryption class -> the cipher_setkey hook logs the REAL key it passes.
        log("=== no-pw capture: DataWithContentsOfFile:(real path) ===");
        for(String p : new String[]{"1/1_1.dat", "/1/1_1.dat", "1_1.dat"}){
            long rp = makeNSString(p);
            long rr = module.callFunction(emulator, DWF_NOPW, 0, 0, (int)rp).longValue()&0xffffffffL;
            log("  no-pw(\""+p+"\") -> 0x"+Long.toHexString(rr)+(rr!=0&&rr!=0xffffffffL?" printable="+pctPrintable(rr)+"%":""));
        }
        emulator.close();
    }
    static long makeNSString(String s){
        // build a REAL NSString via +[NSString stringWithUTF8String:] so it responds to every accessor
        return MOD.callFunction(emulator, STRWUTF8, (int)CLS_NSSTRING, 0, (int)cstrAlloc(s)).longValue()&0xffffffffL;
    }
    static long makeNSData(long cls, byte[] bytes){
        MemoryBlock d=emulator.getMemory().malloc(bytes.length,true);
        long dp=d.getPointer().peer&0xffffffffL; emulator.getBackend().mem_write(dp,bytes);
        return MOD.callFunction(emulator, DATAWBYTES, (int)cls, 0, (int)dp, bytes.length).longValue()&0xffffffffL;
    }
    static int pctPrintable(long obj){
        try{
            long selB=MOD.callFunction(emulator,SEL_REG,(int)cstrAlloc("bytes")).longValue()&0xffffffffL;
            long selL=MOD.callFunction(emulator,SEL_REG,(int)cstrAlloc("length")).longValue()&0xffffffffL;
            long ptr=MOD.callFunction(emulator,MSGSEND,(int)obj,(int)selB).longValue()&0xffffffffL;
            long len=MOD.callFunction(emulator,MSGSEND,(int)obj,(int)selL).longValue()&0xffffffffL;
            if(ptr==0||len<=0||len>10_000_000) return -1;
            int n=(int)Math.min(len,2048); byte[] d=emulator.getBackend().mem_read(ptr,n);
            int pr=0; for(byte x:d){int c=x&0xff; if((c>=32&&c<127)||c==9||c==10||c==13)pr++;}
            return 100*pr/n;
        }catch(Throwable t){ return -1; }
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
