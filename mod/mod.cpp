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
#include <time.h>
#include <dirent.h>
}
#include <GLES2/gl2.h>
#include "imgui.h"
#include "backends/imgui_impl_opengl3.h"

#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  "RVMOD", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "RVMOD", __VA_ARGS__)

// ── libgame static vaddrs (load base added at runtime) ───────────────────────
#define OFF_READER   0x64ec3c   // +[NSData DataWithContentsOfFile:Password:] (path,pw)->NSData
#define OFF_MSGSEND  0x3783d4
#define OFF_MSGSEND_STRET 0x378578  // objc_msgSend_stret (isa from r1; r0=hidden CGPoint ret ptr)
#define OFF_SELREG   0x3775e0
#define OFF_GETCLASS 0x37295c
#define OFF_SWAP     0x4d095c   // -[CC*View swapBuffers] (ARM) — called each frame before present
// Miniclip native touch handlers (from libgame dynsym). Called by the JNI
// nativeTouches* after converting MotionEvent floats -> ints: M*(id=r0, x=r1, y=r2, d=r3).
#define OFF_MDOWN    0x701958   // MtouchDown
#define OFF_MUP      0x701c20   // MtouchUp
#define OFF_MMOVE    0x702000   // MtouchMove
#define OFF_SETGRAV  0x64d3a4   // -[GameLayer setGravity:(b2Vec2)] — gx=r2, gy=r3 (float bits)
#define OFF_STEP     0x64d510   // -[World step:(ccTime)] — per-frame physics tick (drives gravity)
#define OFF_DRAW     0x648eec   // -[GameLayer draw] — per-frame; owns setCameraZoom: (drives zoom)
#define OFF_SETSPEED   0x66e1cc // -[Bike setSpeedLimit:(float)]      (value = r2)
#define OFF_SETNITRO   0x66e2a4 // -[Bike setNitroPerformance:(float)]
#define OFF_SETFORCE   0x66e214 // -[Bike setForceScale:(float)]
#define OFF_SETBURN    0x66e2ec // -[Bike setBurnoutSpeed:(float)]
#define OFF_SETWHEELIE 0x66e37c // -[Bike setMaxWheelieSpeed:(float)]
#define OFF_PROCCOND 0x50e104  // -[ConditionManager processConditionInfo:Achievements:] (info=r2, achs=r3 BOOL)
#define OFF_CLICKSTATS 0x5a3db0 // -[? clickStats:] — Stats button handler (online; fails offline)
// app-private save dir (mod runs as the app UID -> rw, no root): ghosts g_*.dat, data.dat
#define SAVE_DIR "/data/data/com.miniclip.bikerivals/files/Contents/Resources"
#define MENU_SCALE   3.0f       // ImGui scale for the phone (big, touch-friendly)
#define MODS_DIR "/sdcard/Android/data/com.miniclip.bikerivals/files/mods"

typedef void* id; typedef void* SEL; typedef void* Class;
static uintptr_t g_base = 0;
static id    (*msgSend)(id, SEL, ...) = 0;
static void  (*msgSend_stret)(void*, id, SEL, ...) = 0;   // for CGPoint/CGRect returns
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
// Overwrites the target's first 8 bytes (2 ARM insns) with `ldr pc,[pc,#-4]; <repl>`.
// The 2 displaced insns are RELOCATED into the trampoline, which then jumps to target+8:
//   * `ldr Rt,[pc,#imm]` literal loads -> reload the SAME value from the trampoline's pool
//     (the in-place `add Rt,pc,Rt` that follows runs after the jump-back, so its PC is right)
//   * PC-independent insns -> copied verbatim
//   * any other PC-relative insn (branch / Rn=pc / Rm=pc) -> ABORT rather than corrupt.
static void* inline_hook(void* target, void* repl){
    uint32_t* t=(uint32_t*)target;
    uint32_t insns[2], pool[2]; int np=0, pool_of[2]={-1,-1};
    for(int i=0;i<2;i++){
        uint32_t w=t[i]; uint32_t ia=(uint32_t)(uintptr_t)(t+i);
        if((w & 0x0F7F0000u)==0x051F0000u){                  // ldr Rt,[pc,#imm] (literal)
            uint32_t Rt=(w>>12)&0xF, imm=w&0xFFF, U=(w>>23)&1;
            uint32_t lit=ia+8+(U?imm:(uint32_t)(0-(int)imm));
            pool[np]=*(uint32_t*)(uintptr_t)lit;
            insns[i]=0xE59F0000u|(Rt<<12); pool_of[i]=np; np++; // imm patched after layout
        } else {
            bool is_bxblx = (w & 0x0FFFFFD0u)==0x012FFF10u;   // bx/blx Rm — position-INDEPENDENT
            bool is_branch= (w & 0x0E000000u)==0x0A000000u;   // b / bl (pc-relative immediate)
            bool rn_pc    = ((w>>16)&0xFu)==0xFu && (w&0x0C000000u)!=0x0C000000u; // Rn = pc
            bool rm_pc    = (w&0xFu)==0xFu && (w&0x0E000000u)==0u;                 // Rm = pc (dp reg)
            if(!is_bxblx && (is_branch || rn_pc || rm_pc)){
                LOGE("inline_hook: unhandled PC-relative insn %08x @ %08x", w, ia); return 0;
            }
            insns[i]=w;                                       // PC-independent — copy verbatim
        }
    }
    uint32_t* tr=(uint32_t*)mmap(0,32,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if(tr==MAP_FAILED){ LOGE("tramp mmap failed"); return 0; }
    for(int i=0;i<2;i++){
        uint32_t w=insns[i];
        if(pool_of[i]>=0){ int slot=4+pool_of[i]; w |= (uint32_t)((slot-i)*4-8) & 0xFFFu; }
        tr[i]=w;
    }
    tr[2]=0xe51ff004u; tr[3]=(uint32_t)((uintptr_t)t+8);      // ldr pc,[pc,#-4]; target+8
    for(int k=0;k<np;k++) tr[4+k]=pool[k];
    prot(tr,32,PROT_READ|PROT_EXEC); flush(tr,32);
    if(prot(t,8,PROT_READ|PROT_WRITE|PROT_EXEC)!=0){ LOGE("mprotect target failed"); return 0; }
    t[0]=0xe51ff004u; t[1]=(uint32_t)(uintptr_t)repl; flush(t,8);
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
static void (*orig_mdown)(int,int,int,int) = 0;
static void (*orig_mup)(int,int,int,int)   = 0;
static void (*orig_mmove)(int,int,int,int) = 0;
static bool imgui_ready = false;
static bool g_menu_open = true;
// Touch (MtouchDown/Move/Up) runs on the SAME GLThread as hook_swap, so we feed ImGui's
// input queue DIRECTLY from these hooks — ImGui event-trickling then splits a fast
// down+up tap across two NewFrames so the press is never lost (per-frame sampling would).
// Menu window rect (pixels) is published by draw_menu each frame; the touch hook uses the
// previous frame's rect to decide ownership synchronously (no 1-frame capture lag).
static float g_win_x0=0,g_win_y0=0,g_win_x1=0,g_win_y1=0;
static float g_aw_x0=0,g_aw_y0=0,g_aw_x1=0,g_aw_y1=0;   // achievements window rect
static bool  g_show_ach = false;         // achievements viewer open (Stats button reroute)
static bool  g_owned = false;            // current touch sequence began on the menu

static inline bool in_menu(float x,float y){
    if(x>=g_win_x0 && x<=g_win_x1 && y>=g_win_y0 && y<=g_win_y1) return true;
    if(g_show_ach && x>=g_aw_x0 && x<=g_aw_x1 && y>=g_aw_y0 && y<=g_aw_y1) return true;
    return false;
}
static void hook_mdown(int id_, int x, int y, int d){
    if(imgui_ready && in_menu((float)x,(float)y)){
        ImGuiIO& io = ImGui::GetIO();
        io.AddMousePosEvent((float)x,(float)y);
        io.AddMouseButtonEvent(0, true);
        g_owned = true;
        return;                          // menu grabs the whole sequence
    }
    orig_mdown(id_,x,y,d);
}
static void hook_mmove(int id_, int x, int y, int d){
    if(g_owned){
        if(imgui_ready) ImGui::GetIO().AddMousePosEvent((float)x,(float)y);
        return;
    }
    orig_mmove(id_,x,y,d);
}
static void hook_mup(int id_, int x, int y, int d){
    bool owned = g_owned; g_owned = false;
    if(owned){
        if(imgui_ready){
            ImGuiIO& io = ImGui::GetIO();
            io.AddMousePosEvent((float)x,(float)y);
            io.AddMouseButtonEvent(0, false);
        }
        return;
    }
    orig_mup(id_,x,y,d);
}

// ── LIVE GRAVITY + CAMERA ZOOM ───────────────────────────────────────────────
// Both ride on -[GameLayer setCameraZoom:], which the follow-camera calls EVERY FRAME in
// a level, so `self` is the live GameLayer. Gravity: the level's b2World is CONSTRUCTED
// with its gravity (setGravity: isn't called on a normal level), so we read the level's
// base gravity via the game's own `gravity` getter (b2Vec2 -> stret) and call its
// `setGravity:` setter each frame with base*mult — using the game's own code, no offsets.
// Zoom: scale the zoom arg by a multiplier.
static void (*orig_step)(id,SEL,float) = 0;
static void (*orig_draw)(id,SEL) = 0;
static SEL   sel_world=0, sel_gravity=0, sel_setgrav=0;
static SEL   sel_setzoom=0, sel_camzoom=0, sel_responds=0;
static id    g_game_self = 0, g_world_last = 0;
static id    g_cam_self = 0, g_cam_last = 0;
static float g_base_gx = 0.0f, g_base_gy = 0.0f;
static float g_grav_mult = 1.0f;     // -30..30 (negative = inverted, 1.0 = normal)
static float g_zoom_mult = 1.0f;     // 0.2..5
static int   g_zoom_mode = 0;        // 0 = flexible (dynamic x mult), 1 = locked (fixed)
static int   g_step_calls = 0;
static bool  g_grav_ok = false, g_can_zoom = false;
struct V2 { float x, y; };

static void apply_specs();   // defined below (bike spec multipliers)

// -[World step:(ccTime)] — the per-frame physics tick (class owns world/gravity/setGravity:).
// Read the level's base gravity via the game's own getter, write base*mult via setGravity:.
static void hook_step(id self, SEL cmd, float dt){
    g_game_self = self; g_step_calls++;
    id world = ((id(*)(id,SEL))msgSend)(self, sel_world);          // [self world]
    if(world && world != g_world_last){                           // new level
        g_world_last = world;
        V2 g = {0,0}; msgSend_stret(&g, self, sel_gravity);       // [self gravity] -> b2Vec2
        g_base_gx = g.x; g_base_gy = g.y; g_grav_ok = true;
        g_cam_self = 0; g_cam_last = 0; g_can_zoom = false;       // force camera re-capture
        LOGI("level world=%p base gravity (%.3f, %.3f)", world, g.x, g.y);
    }
    if(g_grav_ok)
        ((void(*)(id,SEL,float,float))msgSend)(self, sel_setgrav, g_base_gx, g_base_gy*g_grav_mult);
    apply_specs();              // live bike-spec multipliers on the current bike
    orig_step(self, cmd, dt);
}

// -[GameLayer draw] — per-frame; this class owns setCameraZoom:/cameraZoom (the physics
// World does not). The game drives a per-level DYNAMIC zoom (in/out at map sections) by
// writing cameraZoom each frame, so we MULTIPLY that live value instead of locking it:
// read the current zoom, tell whether the game changed it (vs our own last write), scale
// the game's value. At mult==1 we don't touch it — the game's dynamic zoom runs untouched.
static float g_zprev_set = -999.0f, g_zprev_base = 1.0f;

static void hook_draw(id self, SEL cmd){
    bool ok = (self == g_cam_self);
    if(!ok && self != g_cam_last){                                // unknown node -> probe once
        g_cam_last = self;
        if(((bool(*)(id,SEL,SEL))msgSend)(self, sel_responds, sel_setzoom)){
            g_cam_self = self; ok = true; g_can_zoom = true; g_zprev_set = -999.0f;
            LOGI("camera self=%p hooked", self);
        }
    }
    if(ok && g_zoom_mode == 1){
        // LOCKED: force a constant zoom, overriding the game's per-section dynamic zoom.
        ((void(*)(id,SEL,float))msgSend)(self, sel_setzoom, g_zoom_mult);
        g_zprev_set = -999.0f;
    } else if(ok && g_zoom_mult != 1.0f){
        // FLEXIBLE: keep the game's dynamic zoom but multiply it live.
        float C = ((float(*)(id,SEL))msgSend)(self, sel_camzoom);   // current zoom (game's dynamic value)
        float diff = C - g_zprev_set; if(diff < 0) diff = -diff;
        float base = (g_zprev_set > -900.0f && diff < 1e-4f) ? g_zprev_base : C; // reuse if game didn't change it
        g_zprev_base = base;
        float scaled = base * g_zoom_mult;
        ((void(*)(id,SEL,float))msgSend)(self, sel_setzoom, scaled);
        g_zprev_set = scaled;
    } else {
        g_zprev_set = -999.0f;     // flexible + mult==1 -> leave the game's dynamic zoom alone
    }
    orig_draw(self, cmd);
}

// ── BIKE SPEC MULTIPLIERS ────────────────────────────────────────────────────
// The game sets a bike's specs via these setters when the bike is set up (per bike). We
// hook them: capture the bike object + its CONFIG base value, and apply base*mult. Because
// each bike scales its OWN base at its OWN setup, there's no stored per-bike state to bleed
// (the pizza-bike→regular-bike edge case can't happen). Live re-apply in step:.
static void (*orig_sspeed)(id,SEL,float)=0, (*orig_snitro)(id,SEL,float)=0;
static void (*orig_sforce)(id,SEL,float)=0, (*orig_sburn)(id,SEL,float)=0;
static void (*orig_swheelie)(id,SEL,float)=0;
static id    g_bike_self = 0;
static float g_bspeed=0, g_bnitro=0, g_bforce=0, g_bburn=0, g_bwheelie=0;          // captured base
static float g_mspeed=1, g_mnitro=1, g_mforce=1, g_mburn=1, g_mwheelie=1;          // user multipliers
static bool  g_spec_have = false;

static void hook_sspeed (id s,SEL c,float v){ g_bike_self=s; g_bspeed=v;   g_spec_have=true; orig_sspeed (s,c,v*g_mspeed);  }
static float nitro_val(float v){ float n=v; if(n>1.6f) n=1.6f; return n; }  // >~1.65 = runaway nitro
static void hook_snitro (id s,SEL c,float v){ g_bike_self=s; g_bnitro=v;   g_spec_have=true; orig_snitro (s,c,nitro_val(v*g_mnitro)); }
static void hook_sforce (id s,SEL c,float v){ g_bike_self=s; g_bforce=v;   g_spec_have=true; orig_sforce (s,c,v*g_mforce);  }
static void hook_sburn  (id s,SEL c,float v){ g_bike_self=s; g_bburn=v;    g_spec_have=true; orig_sburn  (s,c,v*g_mburn);   }
static void hook_swheelie(id s,SEL c,float v){g_bike_self=s; g_bwheelie=v; g_spec_have=true; orig_swheelie(s,c,v*g_mwheelie);}

static void apply_specs(){   // re-push base*mult every frame so Reset (mult=1 -> base) restores it
    if(!g_spec_have || !g_bike_self) return;
    if(orig_sspeed)   orig_sspeed  (g_bike_self,0,g_bspeed  *g_mspeed);
    if(orig_snitro)   orig_snitro  (g_bike_self,0,nitro_val(g_bnitro*g_mnitro));
    if(orig_sforce)   orig_sforce  (g_bike_self,0,g_bforce  *g_mforce);
    if(orig_sburn)    orig_sburn   (g_bike_self,0,g_bburn   *g_mburn);
    if(orig_swheelie) orig_swheelie(g_bike_self,0,g_bwheelie*g_mwheelie);
}

// ── RESET PROGRESS ───────────────────────────────────────────────────────────
// Delete per-level ghosts (g_*.dat) + the progress save (data.dat / ConditionState.dat)
// from the app-private save dir, then _exit so the in-memory copy can't re-save over it.
// Unlocks survive (native patches, not save data); settings (NSUserDefaults.plist) kept.
static void do_reset_progress(){
    DIR* dp = opendir(SAVE_DIR);
    int n = 0;
    if(dp){
        struct dirent* e; char path[600];
        while((e = readdir(dp))){
            const char* f = e->d_name;
            if(strncmp(f,"g_",2)==0 || strcmp(f,"data.dat")==0 || strcmp(f,"ConditionState.dat")==0){
                snprintf(path,sizeof(path),"%s/%s",SAVE_DIR,f);
                if(unlink(path)==0) n++;
            }
        }
        closedir(dp);
    }
    LOGI("reset progress: deleted %d save files; exiting to apply", n);
    _exit(0);
}

// ── ACHIEVEMENTS (offline viewer — data layer spike) ─────────────────────────
// processConditionInfo:Achievements: runs at startup on the ConditionManager (self) with the
// achievements collection in r3. Capture both, and log each achievement's name(+0x4) /
// text(+0x8) / unlocked(+0xb) to prove we can read them for an offline (no-GPGS) viewer.
static void (*orig_proccond)(id,SEL,id,id)=0;
static void (*orig_clickstats)(id,SEL,id)=0;
static id  g_condmgr = 0, g_achs = 0;
#define ACH_MAX 32
static char g_ach_text[ACH_MAX][160];     // display strings built once (no per-frame ObjC)
static int  g_ach_count = 0;

static void cstr(id nsstr, char* out, int cap){   // [nsstr UTF8String] -> out (truncated)
    out[0]=0;
    if(!nsstr) return;
    const char* s=(const char*)msgSend(nsstr, sel_utf8);
    if(!s) return;
    int j=0; for(; s[j] && j<cap-1; j++) out[j]=s[j]; out[j]=0;
}

static void hook_proccond(id self, SEL cmd, id info, id achs){
    // "Achievements:" (achs) is a BOOL flag (1=achievements pass, 0=conditions). The real
    // data is the ConditionManager ivar activeAchievements_(+0x4), an NSDictionary.
    g_condmgr = self;
    orig_proccond(self,cmd,info,achs);
    id aAch = *(id*)((char*)self + 0x4);
    if(achs && aAch && g_achs != aAch){      // achievements pass, once per collection
        g_achs = aAch;
        SEL s_count=selReg("count"), s_obj=selReg("objectAtIndex:"),
            s_resp=selReg("respondsToSelector:"), s_vals=selReg("allValues");
        // dict -> allValues array; or already an array
        id list = aAch;
        if(!((bool(*)(id,SEL,SEL))msgSend)(aAch,s_resp,s_obj))
            list = ((bool(*)(id,SEL,SEL))msgSend)(aAch,s_resp,s_vals)
                 ? ((id(*)(id,SEL))msgSend)(aAch,s_vals) : 0;
        int n = list ? (int)(intptr_t)((id(*)(id,SEL))msgSend)(list,s_count) : 0;
        if(n>ACH_MAX) n=ACH_MAX;
        SEL s_desc=selReg("description");
        for(int i=0;i<n;i++){
            id a=((id(*)(id,SEL,int))msgSend)(list,s_obj,i);   // [description] is safe on any object
            id d=a?((id(*)(id,SEL))msgSend)(a,s_desc):0;
            cstr(d, g_ach_text[i], sizeof g_ach_text[i]);
            if(!g_ach_text[i][0]) snprintf(g_ach_text[i],sizeof g_ach_text[i],"achievement %d",i);
            if(i<4) LOGI("  ach[%d]=%s", i, g_ach_text[i]);
        }
        g_ach_count=n;
        LOGI("ACH list built: %d", n);
    }
}

// Stats button -> open our OFFLINE achievements viewer instead of the online stats call
// (which shows "Unable to view stats" offline). Don't call orig (skips the failing request).
static void hook_clickstats(id self, SEL cmd, id sender){
    (void)self;(void)cmd;(void)sender;
    g_show_ach = true;
}

// ── process telemetry (no game internals): RAM RSS + process CPU% ─────────────
static int   g_ram_mb  = 0;
static float g_cpu_pct  = 0.0f;
static bool  g_hud_on   = false;   // debug HUD toggle (top of screen)

static void update_stats(){
    FILE* f = fopen("/proc/self/statm", "r");
    if(f){ long sz=0,res=0; if(fscanf(f,"%ld %ld",&sz,&res)==2)
            g_ram_mb = (int)((long long)res * sysconf(_SC_PAGESIZE) / (1024*1024)); fclose(f); }
    FILE* s = fopen("/proc/self/stat", "r");
    if(s){
        char buf[512]; size_t n = fread(buf,1,sizeof(buf)-1,s); fclose(s); buf[n]=0;
        char* rp = strrchr(buf, ')');                     // skip the (comm) field
        if(rp){
            unsigned long ut=0, st=0;
            // after ')': state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt utime stime
            if(sscanf(rp+1," %*c %*d %*d %*d %*d %*d %*u %*lu %*lu %*lu %*lu %lu %lu",&ut,&st)==2){
                struct timespec ts; clock_gettime(CLOCK_MONOTONIC,&ts);
                double now = ts.tv_sec + ts.tv_nsec/1e9;
                static long last_j = -1; static double last_t = 0;
                long j = (long)(ut+st), hz = sysconf(_SC_CLK_TCK);
                if(last_j>=0 && now>last_t)
                    g_cpu_pct = (float)(100.0*(j-last_j)/(double)hz/(now-last_t));
                last_j = j; last_t = now;
            }
        }
    }
}

// Top-of-screen telemetry overlay (non-interactive, doesn't clutter the drive view).
static void draw_hud(){
    ImGuiIO& io = ImGui::GetIO();
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x*0.5f, 6), ImGuiCond_Always, ImVec2(0.5f,0.0f));
    ImGui::SetNextWindowBgAlpha(0.40f);
    ImGui::Begin("##rvhud", nullptr,
        ImGuiWindowFlags_NoDecoration|ImGuiWindowFlags_NoInputs|ImGuiWindowFlags_AlwaysAutoResize|
        ImGuiWindowFlags_NoSavedSettings|ImGuiWindowFlags_NoNav|ImGuiWindowFlags_NoFocusOnAppearing);
    ImGui::Text("FPS %.0f   |   RAM %d MB   |   CPU %.0f%%", io.Framerate, g_ram_mb, g_cpu_pct);
    ImGui::End();
}

static bool g_reset_confirm = false;

static void spec_row(const char* label, float* m, float base, char tag, float maxmul){
    ImGui::Text("%s: %.2f  ->  %.2f", label, base, base*(*m));   // bike's stat -> tuned value
    char a[12],b[12],c[12],d[12];
    snprintf(a,sizeof a,"##s%c",tag); ImGui::SliderFloat(a, m, 0.1f, maxmul, "x%.2f");
    snprintf(b,sizeof b," - ##%c",tag); if(ImGui::Button(b)) *m -= 0.1f;
    snprintf(c,sizeof c," + ##%c",tag); ImGui::SameLine(); if(ImGui::Button(c)) *m += 0.1f;
    snprintf(d,sizeof d,"Reset##%c",tag); ImGui::SameLine(); if(ImGui::Button(d)) *m = 1.0f;
    if(*m < 0.1f)    *m = 0.1f;
    if(*m > maxmul)  *m = maxmul;
}

static void draw_menu(){
    ImGui::SetNextWindowPos(ImVec2(40,40), ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowSize(ImVec2(560,520), ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowCollapsed(false, ImGuiCond_FirstUseEver);   // open by default
    ImGui::Begin("Revenant Mod Menu");   // resizable + scrollable
    ImGui::Text("FPS %.0f   RAM %d MB   CPU %.0f%%", ImGui::GetIO().Framerate, g_ram_mb, g_cpu_pct);

    if(ImGui::BeginTabBar("##rvtabs")){
        if(ImGui::BeginTabItem("Drive")){
            ImGui::Text("Gravity   (step calls: %d)", g_step_calls);
            if(g_grav_ok) ImGui::TextDisabled("base %.1f  ->  applied %.1f", g_base_gy, g_base_gy*g_grav_mult);
            else          ImGui::TextDisabled("(load a level to enable)");
            ImGui::SliderFloat("##grav", &g_grav_mult, -30.0f, 30.0f, "%.2fx");
            if(ImGui::Button(" - ##g"))      g_grav_mult -= 0.1f;
            ImGui::SameLine(); if(ImGui::Button(" + ##g")) g_grav_mult += 0.1f;
            ImGui::SameLine(); if(ImGui::Button("Reset##g")) g_grav_mult = 1.0f;
            if(g_grav_mult < -30.0f) g_grav_mult = -30.0f;
            if(g_grav_mult >  30.0f) g_grav_mult =  30.0f;
            ImGui::TextDisabled("negative = inverted (fly up) | 1.0 = normal");
            ImGui::Separator();
            ImGui::Text("Camera Zoom");
            ImGui::RadioButton("Flexible (dynamic x mult)", &g_zoom_mode, 0);
            ImGui::RadioButton("Locked (fixed)", &g_zoom_mode, 1);
            ImGui::SliderFloat("##zoom", &g_zoom_mult, 0.2f, 5.0f, "%.2fx");
            if(ImGui::Button(" - ##z"))      g_zoom_mult -= 0.1f;
            ImGui::SameLine(); if(ImGui::Button(" + ##z")) g_zoom_mult += 0.1f;
            ImGui::SameLine(); if(ImGui::Button("Reset##z")) g_zoom_mult = 1.0f;
            if(g_zoom_mult < 0.2f) g_zoom_mult = 0.2f;
            if(g_zoom_mult > 5.0f) g_zoom_mult = 5.0f;
            ImGui::EndTabItem();
        }
        if(ImGui::BeginTabItem("Bike")){
            ImGui::Text("Bike Specs (stat -> tuned)");
            if(!g_spec_have) ImGui::TextDisabled("(drive a bike to read its stats)");
            // Labels follow the shop's stat names (mapping to the physics specs is inferred).
            spec_row("Max speed",    &g_mspeed,   g_bspeed,   's', 30.0f);   // speedLimit
            spec_row("Acceleration", &g_mforce,   g_bforce,   'f', 30.0f);   // forceScale
            spec_row("Handling",     &g_mwheelie, g_bwheelie, 'w', 30.0f);   // maxWheelieSpeed
            spec_row("Nitro",        &g_mnitro,   g_bnitro,   'n',  3.0f);   // nitroPerformance (clamped: scales nitro UI)
            spec_row("Burnout",      &g_mburn,    g_bburn,    'b', 30.0f);   // burnoutSpeed (no shop stat)
            ImGui::TextDisabled("Nitro value capped at 1.6 (above ~1.65 = runaway nitro + sprite).");
            ImGui::EndTabItem();
        }
        if(ImGui::BeginTabItem("System")){
            ImGui::Checkbox("Debug HUD (top of screen)", &g_hud_on);
            ImGui::Spacing();
            ImGui::TextDisabled("Mod-loader: ON");
            ImGui::Separator();
            if(!g_reset_confirm){
                if(ImGui::Button("Reset Progress (ghosts + medals)")) g_reset_confirm = true;
            } else {
                ImGui::TextColored(ImVec4(1.0f,0.45f,0.45f,1.0f), "Clears ghosts + medals, then closes the game.");
                if(ImGui::Button("CONFIRM RESET")) do_reset_progress();
                ImGui::SameLine(); if(ImGui::Button("Cancel")) g_reset_confirm = false;
            }
            ImGui::EndTabItem();
        }
        ImGui::EndTabBar();
    }
    // Publish this window's rect (pixels) so the touch hook can own DOWNs that land here.
    ImVec2 wp = ImGui::GetWindowPos(), ws = ImGui::GetWindowSize();
    g_win_x0 = wp.x; g_win_y0 = wp.y; g_win_x1 = wp.x + ws.x; g_win_y1 = wp.y + ws.y;
    ImGui::End();
}

// Offline achievements viewer — opened by the rerouted Stats button (g_show_ach).
static void draw_achievements(){
    ImGuiIO& io = ImGui::GetIO();
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x*0.5f, io.DisplaySize.y*0.5f),
                            ImGuiCond_Appearing, ImVec2(0.5f,0.5f));
    ImGui::SetNextWindowSize(ImVec2(640,560), ImGuiCond_FirstUseEver);
    ImGui::Begin("Achievements (offline)", &g_show_ach);   // [X] sets g_show_ach=false
    ImGui::Text("Achievements: %d", g_ach_count);
    ImGui::Separator();
    if(g_ach_count==0) ImGui::TextDisabled("None loaded yet — open from a screen that loads them.");
    else for(int i=0;i<g_ach_count;i++) ImGui::TextWrapped("%s", g_ach_text[i]);
    ImVec2 wp=ImGui::GetWindowPos(), ws=ImGui::GetWindowSize();
    g_aw_x0=wp.x; g_aw_y0=wp.y; g_aw_x1=wp.x+ws.x; g_aw_y1=wp.y+ws.y;
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
        io.FontGlobalScale = MENU_SCALE;
        ImGui::GetStyle().ScaleAllSizes(MENU_SCALE);
        if(!ImGui_ImplOpenGL3_Init("#version 100")) LOGE("ImGui_ImplOpenGL3_Init failed");
        imgui_ready = true;
        LOGI("ImGui initialized (GLES2), scale=%.1f", MENU_SCALE);
    }
    GLint vp[4] = {0,0,0,0};
    glGetIntegerv(GL_VIEWPORT, vp);
    ImGuiIO& io = ImGui::GetIO();
    io.DisplaySize = ImVec2(vp[2]>0?(float)vp[2]:1280.0f, vp[3]>0?(float)vp[3]:720.0f);
    io.DeltaTime = 1.0f/60.0f;
    ImGui_ImplOpenGL3_NewFrame();
    ImGui::NewFrame();
    static int sc = 0; if((sc++ % 30) == 0) update_stats();   // refresh telemetry ~2/s
    if(g_menu_open) draw_menu();
    if(g_show_ach)  draw_achievements();
    if(g_hud_on)    draw_hud();
    ImGui::Render();
    ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
    orig_swap(self, cmd);
}

// ── install ──────────────────────────────────────────────────────────────────
static void install_hooks(){
    msgSend=(id(*)(id,SEL,...))(g_base+OFF_MSGSEND);
    msgSend_stret=(void(*)(void*,id,SEL,...))(g_base+OFF_MSGSEND_STRET);
    selReg=(SEL(*)(const char*))(g_base+OFF_SELREG);
    getClass=(Class(*)(const char*))(g_base+OFF_GETCLASS);
    sel_utf8=selReg("UTF8String"); sel_strWithUTF=selReg("stringWithUTF8String:");
    sel_world=selReg("world"); sel_gravity=selReg("gravity"); sel_setgrav=selReg("setGravity:");
    sel_setzoom=selReg("setCameraZoom:"); sel_camzoom=selReg("cameraZoom");
    sel_responds=selReg("respondsToSelector:");
    cls_NSString=getClass("NSString");
    orig_reader=(id(*)(id,SEL,id,const char*))inline_hook((void*)(g_base+OFF_READER),(void*)hook_reader);
    LOGI("mod-loader installed (reader=%p)", (void*)orig_reader);

    orig_swap =(void(*)(id,SEL))    inline_hook((void*)(g_base+OFF_SWAP), (void*)hook_swap);
    orig_mdown=(void(*)(int,int,int,int))inline_hook((void*)(g_base+OFF_MDOWN),(void*)hook_mdown);
    orig_mup  =(void(*)(int,int,int,int))inline_hook((void*)(g_base+OFF_MUP),  (void*)hook_mup);
    orig_mmove=(void(*)(int,int,int,int))inline_hook((void*)(g_base+OFF_MMOVE),(void*)hook_mmove);
    LOGI("ImGui overlay + touch armed (swap=%p mdown=%p)", (void*)orig_swap, (void*)orig_mdown);

    orig_step=(void(*)(id,SEL,float))inline_hook((void*)(g_base+OFF_STEP),(void*)hook_step);
    orig_draw=(void(*)(id,SEL))      inline_hook((void*)(g_base+OFF_DRAW),(void*)hook_draw);
    LOGI("gravity hook armed (step=%p) zoom hook armed (draw=%p)", (void*)orig_step, (void*)orig_draw);

    orig_sspeed  =(void(*)(id,SEL,float))inline_hook((void*)(g_base+OFF_SETSPEED),  (void*)hook_sspeed);
    orig_snitro  =(void(*)(id,SEL,float))inline_hook((void*)(g_base+OFF_SETNITRO),  (void*)hook_snitro);
    orig_sforce  =(void(*)(id,SEL,float))inline_hook((void*)(g_base+OFF_SETFORCE),  (void*)hook_sforce);
    orig_sburn   =(void(*)(id,SEL,float))inline_hook((void*)(g_base+OFF_SETBURN),   (void*)hook_sburn);
    orig_swheelie=(void(*)(id,SEL,float))inline_hook((void*)(g_base+OFF_SETWHEELIE),(void*)hook_swheelie);
    LOGI("bike-spec hooks armed (speed=%p)", (void*)orig_sspeed);

    orig_proccond=(void(*)(id,SEL,id,id))inline_hook((void*)(g_base+OFF_PROCCOND),(void*)hook_proccond);
    orig_clickstats=(void(*)(id,SEL,id))inline_hook((void*)(g_base+OFF_CLICKSTATS),(void*)hook_clickstats);
    LOGI("achievements hook armed (proccond=%p clickstats=%p)", (void*)orig_proccond, (void*)orig_clickstats);
}

static int find_cb(struct dl_phdr_info* info, size_t sz, void* d){
    (void)sz;(void)d;
    if(info->dlpi_name && strstr(info->dlpi_name,"libgame.so")){ g_base=info->dlpi_addr; return 1; }
    return 0;
}
static void* waiter(void*){
    // poll up to 120s — slow launches (Google Play sign-in retries) can delay libgame load
    for(int i=0;i<2400 && !g_base;i++){ dl_iterate_phdr(find_cb,0); if(!g_base) usleep(50000); }
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
