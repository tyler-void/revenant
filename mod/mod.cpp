// Revenant native mod lib (libmod.so) — injected into Bike Rivals.
//
//   * MOD-LOADER: hook the encrypted-asset reader and redirect to a writable mods/ folder.
//   * ImGui OVERLAY (M1): hook eglSwapBuffers, init ImGui on GLES2, draw a menu each frame.
//
// Loaded via GameActivity.<clinit> System.loadLibrary("mod"); it loads before libgame,
// polls dl_iterate_phdr until libgame is mapped, then installs inline hooks.

extern "C" {
#include <android/log.h>
#include <dlfcn.h>
#include <link.h>
#include <string.h>
#include <stdint.h>
#include <stdio.h>
#include <pthread.h>
#include <unistd.h>
#include <sys/mman.h>
}
#include <GLES2/gl2.h>
#include "imgui.h"
#include "backends/imgui_impl_opengl3.h"

#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  "RVMOD", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "RVMOD", __VA_ARGS__)

// ── libgame static vaddrs (load base added at runtime) ───────────────────────
#define OFF_READER   0x64ec3c   // +[NSData DataWithContentsOfFile:Password:] (path,pw)->NSData
#define OFF_MSGSEND  0x3783d4
#define OFF_SELREG   0x3775e0
#define OFF_GETCLASS 0x37295c
#define OFF_SWAP     0x4d095c   // -[CC*View swapBuffers] (ARM) — called each frame before present
#define MODS_DIR "/sdcard/Android/data/com.miniclip.bikerivals/files/mods"

typedef void* id; typedef void* SEL; typedef void* Class;
static uintptr_t g_base = 0;
static id    (*msgSend)(id, SEL, ...) = 0;
static SEL   (*selReg)(const char*) = 0;
static Class (*getClass)(const char*) = 0;
static SEL sel_utf8 = 0, sel_strWithUTF = 0;
static Class cls_NSString = 0;

// ── minimal ARM32 prologue-relocating inline hook ────────────────────────────
static void flush(void* a, size_t n){ __builtin___clear_cache((char*)a, (char*)a + n); }
static int prot(void* a, size_t n, int p){
    uintptr_t pg=(uintptr_t)a & ~(uintptr_t)0xfff, end=((uintptr_t)a+n+0xfff)&~(uintptr_t)0xfff;
    return mprotect((void*)pg, end-pg, p);
}
static void* inline_hook(void* target, void* repl){
    uint8_t* t=(uint8_t*)target;
    void* tr=mmap(0,16,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if(tr==MAP_FAILED){ LOGE("tramp mmap failed"); return 0; }
    memcpy(tr,t,8);
    uint32_t* v=(uint32_t*)((uint8_t*)tr+8);
    v[0]=0xe51ff004; v[1]=(uint32_t)((uintptr_t)t+8);
    prot(tr,16,PROT_READ|PROT_EXEC); flush(tr,16);
    if(prot(t,8,PROT_READ|PROT_WRITE|PROT_EXEC)!=0){ LOGE("mprotect target failed"); return 0; }
    uint32_t* p=(uint32_t*)t; p[0]=0xe51ff004; p[1]=(uint32_t)(uintptr_t)repl; flush(t,8);
    return tr;
}

// ── MOD-LOADER ───────────────────────────────────────────────────────────────
static id (*orig_reader)(id, SEL, id, const char*) = 0;
static id hook_reader(id self, SEL cmd, id file, const char* pw){
    const char* p = file ? (const char*)msgSend(file, sel_utf8) : 0;
    if(p && *p){
        const char* slash=strrchr(p,'/'); const char* base=slash?slash+1:p;
        char modpath[600]; snprintf(modpath,sizeof(modpath),"%s/%s",MODS_DIR,base);
        if(access(modpath,R_OK)==0){
            LOGI("[MOD] %s -> %s", base, modpath);
            id ns=((id(*)(Class,SEL,const char*))msgSend)(cls_NSString,sel_strWithUTF,modpath);
            return orig_reader(self,cmd,ns,pw);
        }
    }
    return orig_reader(self,cmd,file,pw);
}

// ── ImGui OVERLAY ────────────────────────────────────────────────────────────
static void (*orig_swap)(id, SEL) = 0;
static bool imgui_ready = false;
static bool g_menu_open = true;   // M1: always show so we can confirm it renders

static void draw_menu(){
    ImGui::SetNextWindowPos(ImVec2(60,60), ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowSize(ImVec2(460,280), ImGuiCond_FirstUseEver);
    ImGui::Begin("Revenant Mod Menu");
    ImGui::Text("libmod ImGui overlay is LIVE");
    ImGui::Text("FPS: %.1f", ImGui::GetIO().Framerate);
    ImGui::Separator();
    ImGui::TextWrapped("Mod-loader active: drop mods/<world>_<level>.dat to override.");
    ImGui::Separator();
    ImGui::Text("(touch input + live sliders next)");
    ImGui::End();
}

// Hook cocos2d swapBuffers: scene is drawn + GL context current; draw ImGui on top,
// then call the original (which presents). ARM, in libgame — no Thumb hazard.
static void hook_swap(id self, SEL cmd){
    if(!imgui_ready){
        IMGUI_CHECKVERSION();
        ImGui::CreateContext();
        ImGuiIO& io = ImGui::GetIO();
        io.IniFilename = nullptr;
        io.MouseDrawCursor = true;
        ImGui::StyleColorsDark();
        if(!ImGui_ImplOpenGL3_Init("#version 100")) LOGE("ImGui_ImplOpenGL3_Init failed");
        imgui_ready = true;
        LOGI("ImGui initialized (GLES2)");
    }
    GLint vp[4] = {0,0,0,0};
    glGetIntegerv(GL_VIEWPORT, vp);
    ImGuiIO& io = ImGui::GetIO();
    io.DisplaySize = ImVec2(vp[2]>0?(float)vp[2]:1280.0f, vp[3]>0?(float)vp[3]:720.0f);
    io.DeltaTime = 1.0f/60.0f;
    ImGui_ImplOpenGL3_NewFrame();
    ImGui::NewFrame();
    if(g_menu_open) draw_menu();
    ImGui::Render();
    ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
    orig_swap(self, cmd);
}

// ── install ──────────────────────────────────────────────────────────────────
static void install_hooks(){
    msgSend=(id(*)(id,SEL,...))(g_base+OFF_MSGSEND);
    selReg=(SEL(*)(const char*))(g_base+OFF_SELREG);
    getClass=(Class(*)(const char*))(g_base+OFF_GETCLASS);
    sel_utf8=selReg("UTF8String"); sel_strWithUTF=selReg("stringWithUTF8String:");
    cls_NSString=getClass("NSString");
    orig_reader=(id(*)(id,SEL,id,const char*))inline_hook((void*)(g_base+OFF_READER),(void*)hook_reader);
    LOGI("mod-loader installed (reader=%p)", (void*)orig_reader);

    orig_swap=(void(*)(id,SEL))inline_hook((void*)(g_base+OFF_SWAP),(void*)hook_swap);
    LOGI("swapBuffers hooked (orig=%p) — ImGui overlay armed", (void*)orig_swap);
}

static int find_cb(struct dl_phdr_info* info, size_t sz, void* d){
    (void)sz;(void)d;
    if(info->dlpi_name && strstr(info->dlpi_name,"libgame.so")){ g_base=info->dlpi_addr; return 1; }
    return 0;
}
static void* waiter(void*){
    for(int i=0;i<600 && !g_base;i++){ dl_iterate_phdr(find_cb,0); if(!g_base) usleep(50000); }
    if(g_base){ LOGI("libgame @ %p — installing hooks", (void*)g_base); install_hooks(); }
    else LOGE("gave up waiting for libgame");
    return 0;
}
__attribute__((constructor)) static void on_load(){
    LOGI("libmod.so loaded");
    dl_iterate_phdr(find_cb,0);
    if(g_base) install_hooks();
    else { pthread_t th; pthread_create(&th,0,waiter,0); }
}
