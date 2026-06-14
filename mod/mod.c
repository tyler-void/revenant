// Revenant native mod lib (libmod.so) — injected into Bike Rivals via
// GameActivity.<clinit> (System.loadLibrary("mod")). It loads BEFORE libgame, so it
// waits (dl_iterate_phdr poll) until libgame.so is mapped, then installs inline hooks
// and runs real C logic inside the game. Foundation for the mod-loader + ImGui menu.
//
// Milestone 2 (this file): a minimal ARM32 prologue-relocating inline hook, proven by
// hooking loadLevelInfo:FileName: to log the level filename FROM C (calls the game's
// own objc_msgSend for [filename UTF8String], then calls the original via a trampoline).

#include <android/log.h>
#include <dlfcn.h>
#include <link.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
#include <unistd.h>
#include <sys/mman.h>

#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  "RVMOD", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "RVMOD", __VA_ARGS__)

// libgame static vaddrs (load base added at runtime)
#define OFF_LOADLEVEL 0x6e25dc   // -[... loadLevelInfo:FileName:]
#define OFF_MSGSEND   0x3783d4   // objc_msgSend
#define OFF_SELREG    0x3775e0   // sel_registerName

typedef void* id; typedef void* SEL;
static uintptr_t g_base = 0;
static id  (*msgSend)(id, SEL, ...) = 0;
static SEL (*selReg)(const char*) = 0;
static SEL sel_utf8 = 0;
static void (*orig_loadLevel)(id, SEL, id, id) = 0;

// Our hook: log the FileName arg from C, then call the original.
static void hook_loadLevel(id self, SEL cmd, id info, id filename) {
    const char* name = filename ? (const char*)msgSend(filename, sel_utf8) : 0;
    LOGI("[C hook] loadLevelInfo FileName = %s", name ? name : "(null)");
    orig_loadLevel(self, cmd, info, filename);
}

// --- minimal ARM32 inline hook (relocates the first 8 prologue bytes) ---
static void flush(void* a, size_t n){ __builtin___clear_cache((char*)a, (char*)a + n); }
static int prot(void* addr, size_t len, int p) {
    uintptr_t pg  = (uintptr_t)addr & ~(uintptr_t)0xfff;
    uintptr_t end = ((uintptr_t)addr + len + 0xfff) & ~(uintptr_t)0xfff;
    return mprotect((void*)pg, end - pg, p);
}
// Patch target's first 8 bytes with a jump to repl; return a trampoline that runs the
// original 8 bytes then jumps to target+8. (Target prologue must be position-independent
// — loadLevelInfo's `push {...,lr}; add fp,sp,#imm` qualifies.)
static void* inline_hook(void* target, void* repl) {
    uint8_t* t = (uint8_t*)target;
    void* tr = mmap(0, 16, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (tr == MAP_FAILED) { LOGE("tramp mmap failed"); return 0; }
    memcpy(tr, t, 8);
    uint32_t* v = (uint32_t*)((uint8_t*)tr + 8);
    v[0] = 0xe51ff004;                        // LDR PC, [PC, #-4]
    v[1] = (uint32_t)((uintptr_t)t + 8);      // -> target+8
    prot(tr, 16, PROT_READ|PROT_EXEC); flush(tr, 16);
    if (prot(t, 8, PROT_READ|PROT_WRITE|PROT_EXEC) != 0) { LOGE("mprotect target failed"); return 0; }
    uint32_t* p = (uint32_t*)t;
    p[0] = 0xe51ff004;                        // LDR PC, [PC, #-4]
    p[1] = (uint32_t)(uintptr_t)repl;         // -> our hook
    flush(t, 8);
    return tr;
}

static void install_hooks(void) {
    msgSend = (void*)(g_base + OFF_MSGSEND);
    selReg  = (void*)(g_base + OFF_SELREG);
    sel_utf8 = selReg("UTF8String");
    orig_loadLevel = (void(*)(id,SEL,id,id))inline_hook((void*)(g_base + OFF_LOADLEVEL),
                                                         (void*)hook_loadLevel);
    LOGI("hooks installed (loadLevelInfo orig=%p)", (void*)orig_loadLevel);
}

static int find_cb(struct dl_phdr_info* info, size_t sz, void* d) {
    (void)sz; (void)d;
    if (info->dlpi_name && strstr(info->dlpi_name, "libgame.so")) { g_base = info->dlpi_addr; return 1; }
    return 0;
}
static void* waiter(void* a) {
    (void)a;
    for (int i = 0; i < 600 && !g_base; i++) { dl_iterate_phdr(find_cb, 0); if (!g_base) usleep(50000); }
    if (g_base) { LOGI("libgame mapped @ %p — installing hooks", (void*)g_base); install_hooks(); }
    else LOGE("gave up waiting for libgame");
    return 0;
}

__attribute__((constructor))
static void on_load(void) {
    LOGI("libmod.so loaded");
    dl_iterate_phdr(find_cb, 0);
    if (g_base) { LOGI("libgame already mapped @ %p", (void*)g_base); install_hooks(); }
    else { pthread_t th; pthread_create(&th, 0, waiter, 0); }
}
