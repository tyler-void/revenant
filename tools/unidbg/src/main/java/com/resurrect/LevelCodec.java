package com.resurrect;

import com.github.unidbg.AndroidEmulator;
import com.github.unidbg.Module;
import com.github.unidbg.arm.backend.Backend;
import com.github.unidbg.linux.android.AndroidEmulatorBuilder;
import com.github.unidbg.linux.android.AndroidResolver;
import com.github.unidbg.linux.android.dvm.DalvikModule;
import com.github.unidbg.linux.android.dvm.VM;
import com.github.unidbg.memory.Memory;
import com.github.unidbg.memory.MemoryBlock;

import java.io.File;
import java.nio.file.Files;
import java.util.zip.GZIPInputStream;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;

/**
 * Revenant level codec oracle — drives the game's OWN Blowfish cipher in unidbg.
 *
 *   decrypt : .dat        → raw (gzip) plaintext
 *   encrypt : gzip(bplist) → device-loadable .dat
 *
 * The cipher is Blowfish, with mirror-image process functions in libgame.so:
 *   DECRYPT  cipher_process @ 0x65085c  (block fn 0x650ca8)
 *   ENCRYPT  cipher_process @ 0x6507d4  (block fn 0x6508e4)
 * plus cipher_init @ 0x650090 and cipher_setkey @ 0x650570 (key = raw char*).
 *
 * Decrypt is done via the high-level reader +[NSData DataWithContentsOfFile:
 * Password:] (0x64ea98), which = atoi(header) + cipher_process_DECRYPT(file+8,
 * len-8) + take declLen bytes (no nibble-swap). Encrypt mirrors that framing
 * with the encrypt-direction cipher:
 *
 *   file = "<declLen>\0" + filler-to-offset-8 + cipher_process_ENCRYPT(plaintext ×8)
 *
 * Verified end-to-end: a re-encrypted level round-trips through the game's own
 * decryptor byte-identically, and an edited level loads on a real device.
 *
 * Env:  BR_MODE = decrypt | encrypt | roundtrip   (default decrypt)
 *       BR_KEY  = per-level key, hex (NEVER persisted — circumvention material)
 *       BR_IN   = input (.dat for decrypt/roundtrip; gzip(bplist) for encrypt)
 *       BR_OUT  = output (gzip plaintext for decrypt; .dat for encrypt/roundtrip)
 *
 * NOTE: run with stdin closed (</dev/null). On an emulation exception unidbg
 * drops into an interactive debugger that blocks on stdin; EOF lets it bail
 * (the codec result is already written by then).
 */
public class LevelCodec {
    static final long DWF_PW   = 0x64ea98L; // +[NSData DataWithContentsOfFile:Password:] (decrypt reader)
    static final long C_INIT   = 0x650090L; // cipher_init(ctx)
    static final long C_SETKEY = 0x650570L; // cipher_setkey(ctx, char* key)
    static final long C_PROC_E = 0x6507d4L; // cipher_process ENCRYPT (→ block 0x6508e4)
    static final long GETCLASS = 0x37295cL; // objc_getClass(char*)
    static final long MSGSEND  = 0x3783d4L; // objc_msgSend(recv, sel, ...)
    static final long SEL_REG  = 0x3775e0L; // sel_registerName(char*)
    static final long DATAWBYTES = 0x398c48L; // +[NSData dataWithBytes:length:]

    static AndroidEmulator emu;
    static Module MOD;
    static Backend BE;
    static Memory MEM;
    static long CLS_NSDATA;

    public static void main(String[] args) throws Exception {
        String mode = env("BR_MODE", "decrypt");
        String keyHex = env("BR_KEY", null);
        String in = env("BR_IN", null);
        String out = env("BR_OUT", null);
        if (keyHex == null || in == null) { log("need BR_KEY + BR_IN (+BR_OUT)"); return; }

        File so = new File("src/main/resources/libgame.so");
        if (!so.exists()) so = new File("tools/unidbg/src/main/resources/libgame.so");
        File rootfs = new File("/tmp/br-rootfs"); rootfs.mkdirs();

        AndroidEmulatorBuilder b = AndroidEmulatorBuilder.for32Bit();
        b.setProcessName("com.miniclip.bikerivals");
        b.setRootDir(rootfs);
        emu = b.build();
        MEM = emu.getMemory();
        BE = emu.getBackend();
        MEM.setLibraryResolver(new AndroidResolver(23));
        VM vm = emu.createDalvikVM();
        vm.setVerbose(false);
        DalvikModule dm = vm.loadLibrary(so, true);   // forceCallInit → ObjC classes registered
        MOD = dm.getModule();
        try { dm.callJNI_OnLoad(emu); } catch (Throwable t) { /* not required */ }

        CLS_NSDATA = getClass("NSData");
        byte[] key = hex(keyHex);
        long keyPtr = cbuf(key);  // raw char* (password is a char*, per the method type encoding)
        log("mode=" + mode + " key=" + key.length + "B  NSData=0x" + hex(CLS_NSDATA));

        if (mode.equals("batch")) {
            // BR_IN = manifest file, each line "inDat\toutRaw" — decrypt all in ONE JVM session
            int ok = 0, fail = 0;
            for (String line : Files.readAllLines(new File(in).toPath())) {
                line = line.trim(); if (line.isEmpty()) continue;
                String[] pp = line.split("\t");
                try {
                    byte[] raw = decrypt(Files.readAllBytes(new File(pp[0]).toPath()), keyPtr);
                    if (raw != null && raw.length > 0) { write(pp[1], raw); ok++; }
                    else { log("FAIL " + pp[0]); fail++; }
                } catch (Throwable t) { log("ERR " + pp[0] + ": " + t); fail++; }
            }
            log("batch done: " + ok + " ok, " + fail + " failed");
            emu.close(); return;
        }
        if (mode.equals("decrypt")) {
            byte[] raw = decrypt(Files.readAllBytes(new File(in).toPath()), keyPtr);
            write(out, raw);
            log("decrypted " + in + " -> " + out + " (" + (raw == null ? -1 : raw.length) + " B, head=" + head(raw) + ")");
        } else if (mode.equals("encrypt")) {
            // BR_IN = gzip(bplist) payload (exactly what decrypt emits); BR_OUT = encrypted .dat
            byte[] dat = encrypt(Files.readAllBytes(new File(in).toPath()), keyPtr);
            write(out, dat);
            log("encrypted " + in + " -> " + out + " (" + (dat == null ? -1 : dat.length) + " B)");
        } else { // roundtrip self-test: decrypt → re-encrypt the gzip stream → decrypt, prove identity
            byte[] datIn = Files.readAllBytes(new File(in).toPath());
            byte[] decGz = decrypt(datIn, keyPtr);
            byte[] plistX = gunzip(decGz);
            log("decrypt: " + datIn.length + "B .dat -> " + decGz.length + "B gz -> " + plistX.length + "B " + head(plistX));
            byte[] datOut = encrypt(decGz, keyPtr);
            if (datOut == null) { log("ENCRYPT FAILED"); emu.close(); return; }
            if (out != null) write(out, datOut);
            byte[] decGz2 = decrypt(datOut, keyPtr);
            boolean gzEq = java.util.Arrays.equals(decGz, decGz2);
            boolean plistEq = java.util.Arrays.equals(plistX, gunzip(decGz2));
            log("encrypt: " + decGz.length + "B gz -> " + datOut.length + "B .dat (orig " + datIn.length + "B)");
            log("  gzip-stream identical: " + gzEq + "   plist content identical: " + plistEq);
            log(gzEq && plistEq
                ? "✅ ROUND-TRIP OK — cipher_process_ENCRYPT produces a device-loadable .dat"
                : "✗ round-trip mismatch");
        }
        emu.close();
    }

    // decrypt: +[NSData DataWithContentsOfFile:Password:] decrypts the passed NSData
    static byte[] decrypt(byte[] datBytes, long keyPtr) {
        long nsdata = makeNSData(datBytes);
        return readNSData(call(DWF_PW, 0, 0, (int) nsdata, (int) keyPtr));
    }

    // encrypt: mirror the decrypt framing using the encrypt-direction cipher.
    //   file = "<declLen>\0" + filler-to-offset-8 + cipher_process_ENCRYPT(plaintext padded ×8)
    static byte[] encrypt(byte[] gzPlaintext, long keyPtr) {
        int declLen = gzPlaintext.length;
        int padded = ((declLen + 7) / 8) * 8;                 // cipher region must be a multiple of 8
        byte[] buf = new byte[padded];
        System.arraycopy(gzPlaintext, 0, buf, 0, declLen);    // pad tail with zeros (decrypt truncates to declLen)
        byte[] ciphered = cipherProcessEnc(buf, keyPtr);
        if (ciphered == null) return null;
        byte[] header = (Integer.toString(declLen) + "\0").getBytes();
        byte[] file = new byte[8 + ciphered.length];           // cipher always begins at file[8]
        System.arraycopy(header, 0, file, 0, Math.min(header.length, 8));
        System.arraycopy(ciphered, 0, file, 8, ciphered.length);
        return file;
    }

    // Blowfish encrypt-direction: fresh init + setkey, then process(data,len) in place.
    static byte[] cipherProcessEnc(byte[] data, long keyPtr) {
        if (data.length % 8 != 0) { log("  cipherProcessEnc: len " + data.length + " not %8"); return null; }
        long ctx = MEM.malloc(0x2000, true).getPointer().peer & 0xffffffffL;
        long buf = cbuf(data);
        call(C_INIT, (int) ctx);
        call(C_SETKEY, (int) ctx, (int) keyPtr);
        call(C_PROC_E, (int) ctx, (int) buf, data.length);
        return BE.mem_read(buf, data.length);
    }

    // ── helpers ───────────────────────────────────────────────────────────────
    static long call(long addr, Number... a) { return MOD.callFunction(emu, addr, a).longValue() & 0xffffffffL; }
    static long getClass(String n) { return call(GETCLASS, (int) cstr(n)); }
    static long sel(String n) { return call(SEL_REG, (int) cstr(n)); }
    static long makeNSData(byte[] bytes) {
        return call(DATAWBYTES, (int) CLS_NSDATA, 0, (int) cbuf(bytes), bytes.length);
    }
    static byte[] readNSData(long obj) {
        if (obj == 0 || obj == 0xffffffffL) return null;
        long ptr = call(MSGSEND, (int) obj, (int) sel("bytes"));
        long len = call(MSGSEND, (int) obj, (int) sel("length"));
        if (ptr == 0 || len <= 0 || len > 20_000_000L) return null;
        return BE.mem_read(ptr, len);
    }
    static long cstr(String s) { return cbuf((s + "\0").getBytes()); }
    static long cbuf(byte[] b) {
        MemoryBlock m = MEM.malloc(Math.max(1, b.length), true);
        long a = m.getPointer().peer & 0xffffffffL; BE.mem_write(a, b); return a;
    }
    static byte[] hex(String h) { byte[] o = new byte[h.length() / 2];
        for (int i = 0; i < o.length; i++) o[i] = (byte) Integer.parseInt(h.substring(2*i, 2*i+2), 16); return o; }
    static String hex(long v) { return Long.toHexString(v); }
    static String head(byte[] b) { if (b == null) return "null"; int n = Math.min(6, b.length);
        StringBuilder s = new StringBuilder(); for (int i = 0; i < n; i++) s.append(String.format("%02x", b[i] & 0xff)); return s.toString(); }
    static byte[] gunzip(byte[] g) throws Exception {
        if (g == null || g.length < 2 || (g[0] & 0xff) != 0x1f) return g; // already bplist
        GZIPInputStream in = new GZIPInputStream(new ByteArrayInputStream(g));
        ByteArrayOutputStream o = new ByteArrayOutputStream(); byte[] buf = new byte[8192]; int n;
        while ((n = in.read(buf)) > 0) o.write(buf, 0, n); return o.toByteArray();
    }
    static void write(String p, byte[] b) throws Exception { if (p != null && b != null) Files.write(new File(p).toPath(), b); }
    static String env(String k, String d) { String v = System.getenv(k); return (v == null || v.isEmpty()) ? d : v; }
    static void log(String s) { System.out.println("[LC] " + s); }
}
