// Revenant agent bridge — see bridge.h for the protocol. Dev tool: enabled only by bridge=1 in
// <mods>/rvdebug.txt, so the distributed build never opens a socket.
//
// Threads:
//   socket thread(s): accept + parse JSON lines. ping / events_since / overlay / log are answered
//                     right there; everything that touches game objects is queued for the GL thread.
//   GL thread:        rv_bridge_frame() (called from the swapBuffers hook) runs queued commands, the
//                     goto_level job and race tracking. NEVER the physics step.
//
// Game-object access goes through libgame's exported ObjC runtime (object_getInstanceVariable,
// ivar_getOffset, object_getClassName, ...) resolved with dlsym, so ivars are read BY NAME at their
// runtime-realized offsets (Apportable realizes ivar layouts at load; static offsets can lie).

#include "bridge.h"
extern "C" {
#include <android/log.h>
#include <dlfcn.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <time.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
}
#include <string>
#include <vector>
#include <deque>
#include <memory>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <chrono>
#include <algorithm>

#define BLOG(...) __android_log_print(ANDROID_LOG_INFO,  "RVMOD", __VA_ARGS__)
#define BERR(...) __android_log_print(ANDROID_LOG_ERROR, "RVMOD", __VA_ARGS__)

static RvRuntime R;
static bool g_ready = false;

// ── libgame ObjC runtime (dlsym) ─────────────────────────────────────────────────────────────
static const char* (*p_className)(id) = 0;
static void*       (*p_getIvar)(id, const char*, void**) = 0;   // Ivar object_getInstanceVariable
static ptrdiff_t   (*p_ivarOffset)(void*) = 0;
static const char* (*p_ivarType)(void*) = 0;

static double now_mono(){ struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }

// ── JSON (tiny) ──────────────────────────────────────────────────────────────────────────────
struct JV {
    enum T { NUL, BOOL, NUM, STR, ARR, OBJ } t = NUL;
    bool b = false; double n = 0; std::string s;
    std::vector<JV> a; std::vector<std::pair<std::string, JV>> o;
    const JV* get(const char* k) const {
        if(t != OBJ) return nullptr;
        for(auto& p : o) if(p.first == k) return &p.second;
        return nullptr;
    }
    double num(const char* k, double d) const { auto v = get(k); return v && v->t == NUM ? v->n : d; }
    std::string str(const char* k, const char* d = "") const { auto v = get(k); return v && v->t == STR ? v->s : d; }
    bool has(const char* k) const { return get(k) != nullptr; }
};
static void jws(const char*& p, const char* e){ while(p < e && (*p==' '||*p=='\t'||*p=='\r'||*p=='\n')) p++; }
static bool jparse(const char*& p, const char* e, JV& v, int depth = 0){
    if(depth > 16) return false;
    jws(p, e); if(p >= e) return false;
    char c = *p;
    if(c == '{'){
        v.t = JV::OBJ; p++; jws(p, e);
        if(p < e && *p == '}'){ p++; return true; }
        for(;;){
            JV k; if(!jparse(p, e, k, depth+1) || k.t != JV::STR) return false;
            jws(p, e); if(p >= e || *p != ':') return false; p++;
            JV val; if(!jparse(p, e, val, depth+1)) return false;
            v.o.emplace_back(k.s, std::move(val)); jws(p, e);
            if(p < e && *p == ','){ p++; continue; }
            if(p < e && *p == '}'){ p++; return true; }
            return false;
        }
    }
    if(c == '['){
        v.t = JV::ARR; p++; jws(p, e);
        if(p < e && *p == ']'){ p++; return true; }
        for(;;){
            JV x; if(!jparse(p, e, x, depth+1)) return false;
            v.a.push_back(std::move(x)); jws(p, e);
            if(p < e && *p == ','){ p++; continue; }
            if(p < e && *p == ']'){ p++; return true; }
            return false;
        }
    }
    if(c == '"'){
        v.t = JV::STR; p++;
        while(p < e && *p != '"'){
            if(*p == '\\' && p + 1 < e){
                p++;
                switch(*p){
                    case 'n': v.s += '\n'; break; case 't': v.s += '\t'; break; case 'r': v.s += '\r'; break;
                    case 'u': if(p + 4 < e){ unsigned cp = strtoul(std::string(p+1, 4).c_str(), 0, 16);
                                             v.s += cp < 0x80 ? (char)cp : '?'; p += 4; } break;
                    default: v.s += *p;
                }
                p++;
            } else v.s += *p++;
        }
        if(p >= e) return false;
        p++; return true;
    }
    if(e - p >= 4 && !strncmp(p, "true", 4)){ v.t = JV::BOOL; v.b = true;  p += 4; return true; }
    if(e - p >= 5 && !strncmp(p, "false", 5)){ v.t = JV::BOOL; v.b = false; p += 5; return true; }
    if(e - p >= 4 && !strncmp(p, "null", 4)){ v.t = JV::NUL; p += 4; return true; }
    std::string tmp(p, std::min<long>(e - p, 64)); char* end;
    double d = strtod(tmp.c_str(), &end);
    if(end == tmp.c_str()) return false;
    v.t = JV::NUM; v.n = d; p += end - tmp.c_str(); return true;
}
static std::string js(const char* s){
    std::string o = "\"";
    for(; s && *s; s++){
        unsigned char c = *s;
        if(c == '"' || c == '\\'){ o += '\\'; o += c; }
        else if(c == '\n') o += "\\n";
        else if(c < 0x20){ char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += c;
    }
    return o + "\"";
}
static std::string jp(const void* p){ char b[24]; snprintf(b, sizeof b, "\"0x%08x\"", (unsigned)(uintptr_t)p); return b; }
static std::string jf(double d){ if(d != d) return "null"; char b[40]; snprintf(b, sizeof b, "%.9g", d); return b; }
static std::string ji(long long v){ return std::to_string(v); }

// ── ObjC helpers (GL thread) ─────────────────────────────────────────────────────────────────
static SEL sel(const char* s){ return R.selReg(s); }
static const char* cname(id o){ return o && p_className ? p_className(o) : "nil"; }
static id   m0(id o, const char* s){ return o ? ((id(*)(id,SEL))R.msgSend)(o, sel(s)) : 0; }
static int  mi0(id o, const char* s){ return o ? ((int(*)(id,SEL))R.msgSend)(o, sel(s)) : 0; }
static id   m1(id o, const char* s, uint32_t a){ return o ? ((id(*)(id,SEL,uint32_t))R.msgSend)(o, sel(s), a) : 0; }
static bool responds(id o, const char* s){
    return o && (((int(*)(id,SEL,SEL))R.msgSend)(o, sel("respondsToSelector:"), sel(s)) & 0xff);
}
static id parse_ptr(const std::string& s){ return (id)(uintptr_t)strtoul(s.c_str(), 0, 0); }
// "+ClassName" -> the class object; "0x..." -> an object pointer.
static id resolve_target(const std::string& t){
    if(!t.empty() && t[0] == '+') return (id)R.getClass(t.c_str() + 1);
    return parse_ptr(t);
}

static id running_scene(){
    Class d = R.getClass("CCDirector");
    id dir = d ? m0((id)d, "sharedDirector") : 0;
    return dir ? m0(dir, "runningScene") : 0;
}
static void kids(id node, std::vector<id>& out){
    if(!node || !responds(node, "children")) return;
    id arr = m0(node, "children");
    if(!arr) return;
    int n = mi0(arr, "count");
    if(n < 0 || n > 4096) return;
    for(int i = 0; i < n; i++) out.push_back(m1(arr, "objectAtIndex:", (uint32_t)i));
}
struct NodeRef { id o; int depth; };
static void walk(id node, int depth, int maxd, std::vector<NodeRef>& out, size_t cap){
    if(!node || out.size() >= cap) return;
    out.push_back({node, depth});
    if(depth >= maxd) return;
    std::vector<id> ch; kids(node, ch);
    for(id c : ch) walk(c, depth + 1, maxd, out, cap);
}
static id find_class(const char* cls, int maxd = 10){
    std::vector<NodeRef> all; walk(running_scene(), 0, maxd, all, 3000);
    for(auto& n : all) if(!strcmp(cname(n.o), cls)) return n.o;
    return 0;
}

// Ivar by name at its runtime-realized offset. Returns false if the object has no such ivar.
static bool ivar_loc(id o, const char* name, char** where, const char** type){
    if(!o || !p_getIvar || !p_ivarOffset) return false;
    void* tmp = 0; void* iv = p_getIvar(o, name, &tmp);
    if(!iv) return false;
    *where = (char*)o + p_ivarOffset(iv);
    *type = p_ivarType ? p_ivarType(iv) : "?";
    if(!*type) *type = "?";
    return true;
}
static std::string ivar_json(id o, const char* name){
    char* w; const char* t;
    if(!ivar_loc(o, name, &w, &t)) return "null";
    switch(t[0]){
        case 'f': return jf(*(float*)w);
        case 'd': return jf(*(double*)w);
        case 'i': case 'l': return ji(*(int32_t*)w);
        case 'I': case 'L': return ji(*(uint32_t*)w);
        case 's': return ji(*(int16_t*)w);
        case 'S': return ji(*(uint16_t*)w);
        case 'c': case 'B': return ji(*(int8_t*)w);
        case 'C': return ji(*(uint8_t*)w);
        case 'q': return ji(*(int64_t*)w);
        case '@': { id v = *(id*)w; return v ? "{\"ptr\":" + jp(v) + ",\"class\":" + js(cname(v)) + "}" : "null"; }
        default: {                                   // pointers / structs: raw bytes, hex
            std::string h = "\"";
            char b[4];
            int n = t[0] == '{' ? 16 : 4;
            for(int i = 0; i < n; i++){ snprintf(b, sizeof b, "%02x", (unsigned char)w[i]); h += b; }
            return "{\"type\":" + js(t) + ",\"hex\":" + h + "\"}";
        }
    }
}
// Raw 32-bit ivar value for int / BOOL / char / object ivars (BOOL & char are sign-extended bytes).
static bool ivar_word(id o, const char* name, int32_t* out){
    char* w; const char* t;
    if(!ivar_loc(o, name, &w, &t)) return false;
    if(t[0] == 'c' || t[0] == 'B' || t[0] == 'C') *out = *(int8_t*)w; else *out = *(int32_t*)w;
    return true;
}
static bool ivar_float(id o, const char* name, float* out){
    char* w; const char* t;
    if(!ivar_loc(o, name, &w, &t) || t[0] != 'f') return false;
    *out = *(float*)w; return true;
}

// ── events ───────────────────────────────────────────────────────────────────────────────────
static std::mutex g_ev_mu;
static std::deque<std::pair<long, std::string>> g_events;   // (seq, json)
static long g_ev_seq = 0;
static const size_t EV_CAP = 512;

void rv_event(const char* type, const char* fields){
    if(!g_ready) return;
    std::string body;
    {
        std::lock_guard<std::mutex> lk(g_ev_mu);
        long seq = ++g_ev_seq;
        body = "{\"seq\":" + ji(seq) + ",\"type\":" + js(type) + ",\"mono\":" + jf(now_mono());
        if(fields && *fields){ body += ","; body += fields; }
        body += "}";
        g_events.emplace_back(seq, body);
        while(g_events.size() > EV_CAP) g_events.pop_front();
    }
    BLOG("RVEVT %s", body.c_str());
}

// ── game state shared with mod.cpp ───────────────────────────────────────────────────────────
static std::mutex g_lid_mu;
static char g_lid[32] = "";
static std::atomic<bool> g_hidden(false);
static std::atomic<long> g_frame(0);
static bool  g_race_started = false;
static bool  g_in_level = false;          // set each frame by rv_bridge_frame (GL thread)
static float g_last_gt = -1.0f;

bool rv_overlay_hidden(){ return g_hidden.load(); }

void rv_note_level_file(const char* base){
    // level files only: <w>_<l>.dat or <w>_<u>_<l>.dat (ghosts are g_*.dat, configs are named)
    int a, b, c; char tail[8];
    bool lvl = sscanf(base, "%d_%d_%d.%7s", &a, &b, &c, tail) == 4 ||
               sscanf(base, "%d_%d.%7s", &a, &b, tail) == 3;
    if(!lvl || strcmp(tail, "dat")) return;
    std::lock_guard<std::mutex> lk(g_lid_mu);
    snprintf(g_lid, sizeof g_lid, "%.*s", (int)(strlen(base) - 4), base);
}
static std::string cur_lid(){ std::lock_guard<std::mutex> lk(g_lid_mu); return g_lid; }

void rv_on_menu_ready(id menu){
    rv_event("menu_ready", ("\"menu\":" + js(cname(menu)) + ",\"ptr\":" + jp(menu)).c_str());
}
void rv_on_level_finished(id arg){
    float gt = -1.0f;
    id phys = R.physics ? *R.physics : 0;
    if(phys) ivar_float(phys, "gameTime_", &gt);
    std::string f = "\"lid\":" + js(cur_lid().c_str()) + ",\"game_time\":" + jf(gt) +
                    ",\"arg_class\":" + js(cname(arg));
    rv_event("finish", f.c_str());
}

// Race tracking from the GL thread: race_start = gameTime_ first moves after a level load.
static void track_race(bool in_level){
    if(!in_level) return;
    id phys = R.physics ? *R.physics : 0;
    float gt;
    if(!phys || !ivar_float(phys, "gameTime_", &gt)) return;
    if(!g_race_started && g_last_gt >= 0.0f && gt > g_last_gt){
        g_race_started = true;
        rv_event("race_start", ("\"lid\":" + js(cur_lid().c_str()) + ",\"game_time\":" + jf(gt)).c_str());
    }
    g_last_gt = gt;
}

// ── Box2D / level postcondition helpers ──────────────────────────────────────────────────────
// b2Body layout (Box2D 2.1-era, matches the device-proven m_linearVelocity@0x44 / m_jointList@0x70):
// m_world@0x5c, m_prev@0x60, m_next@0x64. Bodies are prepended to the world list, so walking m_prev
// from Physics._groundBody (created first) and m_next back gives the world's body count.
static id manager(){ Class c = R.getClass("Manager"); return c ? m0((id)c, "sharedManager") : 0; }
static id cur_physics(){                                  // via the Manager: never a stale step pointer
    id mgr = manager(); char* w; const char* t;
    return mgr && ivar_loc(mgr, "physics_", &w, &t) ? *(id*)w : 0;
}
static int count_bodies(id phys){
    char* w; const char* t;
    if(!phys || !ivar_loc(phys, "_groundBody", &w, &t)) return -1;
    char* gb = *(char**)w;
    if(!gb) return 0;
    int n = 1;
    for(char* b = *(char**)(gb + 0x60); b && n < 20000; b = *(char**)(b + 0x60)) n++;
    for(char* b = *(char**)(gb + 0x64); b && n < 20000; b = *(char**)(b + 0x64)) n++;
    return n;
}

// ── goto_level job (multi-frame state machine on the GL thread) ──────────────────────────────
// Calls exactly what the buttons call (verified by disassembly + live):
//   map:    -[MainMenu loadLevelSelectionMenu]
//   dot:    -[LevelDot selectAndDisplayDetail]          (LevelDot._number == level number)
//   RACE:   -[LevelDetail race]  -> [_delegate performSelector:@selector(startLevelWithNumber:)
//                                     withObject:[NSNumber numberWithInt:_levelNumber]]
// Calling startLevelWithNumber: without a selected dot loads an EMPTY level (no level file read), so
// the postcondition is checked: lid matches, the bike was created, and the Box2D world has bodies.
// On failure: -[MotoXGame exitToMenu] and retry (max_attempts), then goto_failed.
static const int WORLD_SIZES[] = {30, 40, 45, 15};
enum GPhase { G_NAV, G_DETAIL, G_LOADING, G_VERIFY };
struct GotoJob {
    bool active = false; int w = 0, l = 0, n = 0, attempt = 1, max_attempts = 3;
    std::string want; GPhase ph = G_NAV;
    double t0 = 0, ph_t = 0, last_action = 0, loaded_t = 0;
    bool asked_map = false, selected = false, raced = false, loaded = false;
    int bike_gen0 = 0, bodies0 = 0;
};
static GotoJob g_goto;
static id g_game_obj = 0;                 // MotoXGame (self of levelLoaded:), for exitToMenu

static int level_number(int w, int l){
    int n = 0;
    for(int i = 1; i < w && i <= 4; i++) n += WORLD_SIZES[i - 1];
    return n + l;
}
static void goto_phase(GPhase p){ g_goto.ph = p; g_goto.ph_t = now_mono(); }
static id find_dot(int n){
    std::vector<NodeRef> all; walk(running_scene(), 0, 12, all, 4000);
    for(auto& r : all) if(!strcmp(cname(r.o), "LevelDot")){
        int32_t num = -1; if(ivar_word(r.o, "_number", &num) && num == n) return r.o;
    }
    return 0;
}
static void goto_retry(const char* why){
    char f[160]; snprintf(f, sizeof f, "\"reason\":\"%s\",\"attempt\":%d", why, g_goto.attempt);
    if(g_goto.attempt >= g_goto.max_attempts){
        g_goto.active = false;
        rv_event("goto_failed", (std::string(f) + ",\"scene\":" + js(cname(running_scene()))).c_str());
        return;
    }
    rv_event("goto_retry", f);
    if(g_goto.loaded && g_game_obj) ((void(*)(id,SEL))R.msgSend)(g_game_obj, sel("exitToMenu"));   // back out
    g_goto.attempt++; g_goto.asked_map = g_goto.selected = g_goto.raced = g_goto.loaded = false;
    g_goto.last_action = now_mono();
    goto_phase(G_NAV);
}
static void goto_tick(bool in_level){
    if(!g_goto.active) return;
    double t = now_mono();
    if(t - g_goto.last_action < 0.25) return;                  // pace actions; scene changes need frames
    if(t - g_goto.ph_t > 20.0){ goto_retry("phase_timeout"); return; }
    char f[200];
    switch(g_goto.ph){
    case G_NAV: {
        if(in_level && !g_goto.raced){                          // in some other level: back out first
            if(g_game_obj && t - g_goto.last_action > 2.0){
                g_goto.last_action = t;
                ((void(*)(id,SEL))R.msgSend)(g_game_obj, sel("exitToMenu"));
                rv_event("goto_progress", "\"step\":\"exitToMenu\"");
            }
            return;
        }
        id dot = find_dot(g_goto.n);
        if(dot){
            g_goto.last_action = t;
            ((void(*)(id,SEL))R.msgSend)(dot, sel("selectAndDisplayDetail"));
            snprintf(f, sizeof f, "\"step\":\"selectAndDisplayDetail\",\"dot\":%s", jp(dot).c_str());
            rv_event("goto_progress", f);
            goto_phase(G_DETAIL);
            return;
        }
        if(!g_goto.asked_map){
            for(const char* c : {"MainMenu", "MainLayer"}){
                id o = find_class(c);
                if(o && responds(o, "loadLevelSelectionMenu")){
                    g_goto.asked_map = true; g_goto.last_action = t;
                    ((void(*)(id,SEL))R.msgSend)(o, sel("loadLevelSelectionMenu"));
                    rv_event("goto_progress", ("\"step\":\"loadLevelSelectionMenu\",\"on\":" + js(c)).c_str());
                    return;
                }
            }
        }
        return;                                                 // wait for the map / its dots
    }
    case G_DETAIL: {
        id det = find_class("LevelDetail", 12);
        int32_t num = -1;
        if(!det || !ivar_word(det, "_levelNumber", &num) || num != g_goto.n) return;
        if(t - g_goto.ph_t < 0.6) return;                      // let the panel finish its open animation
        g_goto.bike_gen0 = R.bike_gen ? *R.bike_gen : 0;
        g_goto.bodies0 = count_bodies(cur_physics());
        { std::lock_guard<std::mutex> lk(g_lid_mu); g_lid[0] = 0; }   // stale lid must not pass the check
        g_goto.raced = true; g_goto.loaded = false; g_goto.last_action = t;
        ((void(*)(id,SEL))R.msgSend)(det, sel("race"));
        rv_event("goto_progress", "\"step\":\"race\"");
        goto_phase(G_LOADING);
        return;
    }
    case G_LOADING:
        if(g_goto.loaded) goto_phase(G_VERIFY);
        return;
    case G_VERIFY: {
        // (no in_level gate: the physics step doesn't run until the first throttle input)
        if(t - g_goto.loaded_t < 1.0) return;                   // let the level finish building
        std::string lid = cur_lid();
        int bodies = count_bodies(cur_physics());
        int added = bodies - g_goto.bodies0;                    // the world keeps bodies across loads
        bool bike = R.bike_gen && *R.bike_gen != g_goto.bike_gen0;
        bool ok = lid == g_goto.want && added > 1 && bike;
        snprintf(f, sizeof f, "\"lid\":%s,\"want\":%s,\"bodies\":%d,\"bodies_added\":%d,\"bike\":%s,\"attempt\":%d",
                 js(lid.c_str()).c_str(), js(g_goto.want.c_str()).c_str(), bodies, added, bike ? "true" : "false", g_goto.attempt);
        if(ok){ g_goto.active = false; rv_event("goto_done", f); return; }
        if(t - g_goto.loaded_t > 4.0){ rv_event("goto_check_failed", f); goto_retry("postcondition"); }
        return;
    }
    }
}

// ── level / layer lifecycle callbacks (from mod.cpp hooks) ──────────────────────────────────
void rv_on_level_loaded(id game){
    g_race_started = false; g_last_gt = -1.0f; g_game_obj = game;
    int bodies = count_bodies(cur_physics());
    std::string f = "\"lid\":" + js(cur_lid().c_str()) + ",\"bodies\":" + ji(bodies) +
                    ",\"bike_gen\":" + ji(R.bike_gen ? *R.bike_gen : -1);
    if(g_goto.active && g_goto.raced) f += ",\"bodies_added\":" + ji(bodies - g_goto.bodies0);
    rv_event("level_loaded", f.c_str());
    if(g_goto.active && g_goto.raced && !g_goto.loaded){ g_goto.loaded = true; g_goto.loaded_t = now_mono(); }
}
void rv_on_layer_enter(id layer){
    const char* c = cname(layer);
    if(!strncmp(c, "CC", 2)) return;                    // cocos2d internals: noise
    Class menu = R.getClass("CCMenu");                  // menus (MCMenuPassTouch, ...) are noise too
    if(menu && (((int(*)(id,SEL,Class))R.msgSend)(layer, sel("isKindOfClass:"), menu) & 0xff)) return;
    id sc = running_scene(); int depth = 0;              // only screens near the top: not level entities
    for(id p = layer; p && p != sc && depth < 8; p = m0(p, "parent")) depth++;
    if(depth > 3) return;
    rv_event("scene_ready", ("\"class\":" + js(c) + ",\"ptr\":" + jp(layer)).c_str());
}

// ── command execution ────────────────────────────────────────────────────────────────────────
static std::string scene_summary(){
    id sc = running_scene();
    std::string r = "{\"ptr\":" + jp(sc) + ",\"class\":" + js(cname(sc)) + ",\"children\":[";
    std::vector<id> ch; kids(sc, ch);
    for(size_t i = 0; i < ch.size() && i < 12; i++){ if(i) r += ","; r += js(cname(ch[i])); }
    return r + "]}";
}

static std::string cmd_state(){
    std::string r = "{\"frame\":" + ji(g_frame.load()) + ",\"mono\":" + jf(now_mono());
    r += ",\"scene\":" + scene_summary();
    r += ",\"lid\":" + js(cur_lid().c_str());
    r += ",\"overlay\":" + js(g_hidden.load() ? "hidden" : "open");
    id phys = cur_physics();                   // via the Manager (never the stale step pointer)
    r += ",\"physics\":" + (phys ? jp(phys) : std::string("null"));
    if(phys){
        // game_time and mono are sampled together on the GL thread in this frame: use THEM for
        // timer-rate checks (not host wall clock, which adds two adb round-trips of noise).
        r += ",\"game_time\":" + ivar_json(phys, "gameTime_");
        r += ",\"bodies\":" + ji(count_bodies(cur_physics()));
        r += ",\"time_step\":" + ivar_json(phys, "timeStep_");
        r += ",\"step\":" + ivar_json(phys, "step_");
    }
    r += ",\"race_started\":" + std::string(g_race_started ? "true" : "false");
    r += ",\"step_calls\":" + ji(R.step_calls ? *R.step_calls : -1);
    r += ",\"bike_gen\":" + ji(R.bike_gen ? *R.bike_gen : -1);
    r += ",\"in_level\":" + std::string(g_in_level ? "true" : "false");
    if(g_goto.active) r += ",\"goto\":{\"phase\":" + ji(g_goto.ph) + ",\"attempt\":" + ji(g_goto.attempt) + "}";
    return r + "}";
}

static std::string cmd_find(const JV& a){
    std::string cls = a.str("class");
    int maxd = (int)a.num("depth", 10), lim = (int)a.num("limit", 50);
    std::vector<NodeRef> all; walk(running_scene(), 0, maxd, all, 3000);
    std::string r = "{\"nodes\":["; int k = 0;
    for(auto& n : all){
        const char* c = cname(n.o);
        if(!cls.empty() && strcmp(c, cls.c_str())) continue;
        if(k >= lim) break;
        if(k++) r += ",";
        r += "{\"ptr\":" + jp(n.o) + ",\"class\":" + js(c) + ",\"depth\":" + ji(n.depth) + "}";
    }
    return r + "],\"scanned\":" + ji((long long)all.size()) + "}";
}

static std::string cmd_children(const JV& a){
    id o = a.has("ptr") ? resolve_target(a.str("ptr")) : running_scene();
    std::vector<id> ch; kids(o, ch);
    std::string r = "{\"ptr\":" + jp(o) + ",\"class\":" + js(cname(o)) + ",\"children\":[";
    for(size_t i = 0; i < ch.size(); i++){
        if(i) r += ",";
        r += "{\"ptr\":" + jp(ch[i]) + ",\"class\":" + js(cname(ch[i])) + "}";
    }
    return r + "]}";
}

static std::string cmd_ivar(const JV& a, std::string& err){
    id o = resolve_target(a.str("ptr"));
    if(!o){ err = "no object"; return ""; }
    const JV* names = a.get("names");
    std::string r = "{\"class\":" + js(cname(o));
    if(names && names->t == JV::ARR){
        for(auto& n : names->a) if(n.t == JV::STR) r += "," + js(n.s.c_str()) + ":" + ivar_json(o, n.s.c_str());
    } else {
        std::string n = a.str("name");
        r += ",\"value\":" + ivar_json(o, n.c_str());
    }
    return r + "}";
}

// call: up to 4 word-sized args (ints, pointers, or floats — softfp passes floats in core regs).
static std::string cmd_call(const JV& a, std::string& err){
    id o = resolve_target(a.str("target"));
    std::string s = a.str("sel"), ret = a.str("ret", "@");
    if(!o || s.empty()){ err = "need target + sel"; return ""; }
    uint32_t w[4] = {0, 0, 0, 0}; int n = 0;
    if(const JV* args = a.get("args")) if(args->t == JV::ARR)
        for(auto& x : args->a){
            if(n >= 4){ err = "max 4 args"; return ""; }
            if(x.t == JV::NUM) w[n++] = (uint32_t)(int32_t)x.n;
            else if(x.t == JV::BOOL) w[n++] = x.b;
            else if(x.t == JV::STR) w[n++] = (uint32_t)(uintptr_t)resolve_target(x.s);
            else if(x.t == JV::OBJ && x.get("f")){ float f = (float)x.num("f", 0); memcpy(&w[n++], &f, 4); }
            else if(x.t == JV::OBJ && x.get("nsnumber")){
                Class num = R.getClass("NSNumber");
                w[n++] = (uint32_t)(uintptr_t)m1((id)num, "numberWithInt:", (uint32_t)(int32_t)x.num("nsnumber", 0));
            }
            else w[n++] = 0;
        }
    uint32_t r = ((uint32_t(*)(id,SEL,uint32_t,uint32_t,uint32_t,uint32_t))R.msgSend)(o, sel(s.c_str()), w[0], w[1], w[2], w[3]);
    if(ret == "v") return "{}";
    if(ret == "f"){ float f; memcpy(&f, &r, 4); return "{\"value\":" + jf(f) + "}"; }
    if(ret == "i") return "{\"value\":" + ji((int32_t)r) + "}";
    if(ret == "B") return "{\"value\":" + ji(r & 0xff) + "}";
    id v = (id)(uintptr_t)r;
    return "{\"value\":" + (v ? "{\"ptr\":" + jp(v) + ",\"class\":" + js(cname(v)) + "}" : std::string("null")) + "}";
}

static std::string cmd_goto(const JV& a, std::string& err){
    int w = (int)a.num("w", 0), l = (int)a.num("l", 0);
    if(w < 1 || w > 4 || l < 1 || l > WORLD_SIZES[w - 1]){ err = "need 1<=w<=4, 1<=l<=world size"; return ""; }
    g_goto = GotoJob();
    g_goto.active = true; g_goto.w = w; g_goto.l = l;
    g_goto.n = a.has("n") ? (int)a.num("n", 0) : level_number(w, l);
    g_goto.max_attempts = (int)a.num("max_attempts", 3);
    g_goto.want = std::to_string(w) + "_" + std::to_string(l);
    g_goto.t0 = g_goto.ph_t = now_mono();
    char b[96]; snprintf(b, sizeof b, "{\"job\":\"goto_level\",\"n\":%d,\"scene\":", g_goto.n);
    return b + js(cname(running_scene())) + "}";
}

// ── GL-thread queue ──────────────────────────────────────────────────────────────────────────
struct Job {
    std::string cmd; JV args; std::string result, error; bool done = false;
};
static std::mutex g_q_mu;
static std::condition_variable g_q_cv;
static std::deque<std::shared_ptr<Job>> g_queue;

static void run_on_gl(Job& j){
    if(j.cmd == "state")         j.result = cmd_state();
    else if(j.cmd == "scene")    j.result = scene_summary();
    else if(j.cmd == "find")     j.result = cmd_find(j.args);
    else if(j.cmd == "children") j.result = cmd_children(j.args);
    else if(j.cmd == "ivar")     j.result = cmd_ivar(j.args, j.error);
    else if(j.cmd == "call")     j.result = cmd_call(j.args, j.error);
    else if(j.cmd == "goto_level") j.result = cmd_goto(j.args, j.error);
    else j.error = "unknown command: " + j.cmd;
}

void rv_bridge_frame(bool in_level){
    if(!g_ready) return;
    g_frame++;
    g_in_level = in_level;
    std::deque<std::shared_ptr<Job>> todo;
    { std::lock_guard<std::mutex> lk(g_q_mu); todo.swap(g_queue); }
    for(auto& j : todo){
        run_on_gl(*j);
        { std::lock_guard<std::mutex> lk(g_q_mu); j->done = true; }
    }
    if(!todo.empty()) g_q_cv.notify_all();
    goto_tick(in_level);
    track_race(in_level);
}

// ── socket side ──────────────────────────────────────────────────────────────────────────────
static std::string handle_line(const std::string& line){
    const char* p = line.c_str(); JV req;
    if(!jparse(p, p + line.size(), req) || req.t != JV::OBJ)
        return "{\"ok\":false,\"error\":\"bad json\"}";
    std::string idj = req.has("id") ? jf(req.num("id", 0)) : "null";
    std::string cmd = req.str("cmd");
    JV args; if(const JV* a = req.get("args")) args = *a; else args.t = JV::OBJ;
    auto ok  = [&](const std::string& r){ return "{\"id\":" + idj + ",\"ok\":true,\"result\":" + r + "}"; };
    auto bad = [&](const std::string& e){ return "{\"id\":" + idj + ",\"ok\":false,\"error\":" + js(e.c_str()) + "}"; };

    if(cmd == "ping")
        return ok("{\"pong\":true,\"frame\":" + ji(g_frame.load()) + ",\"mono\":" + jf(now_mono()) +
                  ",\"build\":" + js(__DATE__ " " __TIME__) + "}");
    if(cmd == "events_since"){
        long since = (long)args.num("seq", 0);
        std::lock_guard<std::mutex> lk(g_ev_mu);
        std::string r = "{\"events\":[";
        bool first = true; long oldest = g_events.empty() ? g_ev_seq + 1 : g_events.front().first;
        for(auto& e : g_events) if(e.first > since){ if(!first) r += ","; r += e.second; first = false; }
        r += "],\"next\":" + ji(g_ev_seq) + ",\"dropped\":" + std::string(since + 1 < oldest ? "true" : "false") + "}";
        return ok(r);
    }
    if(cmd == "overlay"){
        std::string m = args.str("mode", "toggle");
        if(m == "hidden") g_hidden = true; else if(m == "open") g_hidden = false; else g_hidden = !g_hidden.load();
        return ok("{\"overlay\":" + js(g_hidden.load() ? "hidden" : "open") + "}");
    }
    if(cmd == "log"){                       // marker line for log/bridge time alignment
        BLOG("RVMARK %s", args.str("msg").c_str());
        return ok("{\"mono\":" + jf(now_mono()) + "}");
    }

    auto j = std::make_shared<Job>(); j->cmd = cmd; j->args = args;
    int timeout_ms = (int)args.num("timeout_ms", 3000);
    if(timeout_ms < 50) timeout_ms = 50; if(timeout_ms > 20000) timeout_ms = 20000;
    std::unique_lock<std::mutex> lk(g_q_mu);
    g_queue.push_back(j);
    if(!g_q_cv.wait_for(lk, std::chrono::milliseconds(timeout_ms), [&]{ return j->done; }))
        return bad("gl_timeout (no frame rendered — app paused or in background?)");
    return j->error.empty() ? ok(j->result.empty() ? "{}" : j->result) : bad(j->error);
}

static void* client_thread(void* arg){
    int fd = (int)(intptr_t)arg;
    std::string buf; char chunk[4096];
    for(;;){
        ssize_t n = read(fd, chunk, sizeof chunk);
        if(n <= 0) break;
        buf.append(chunk, n);
        size_t nl;
        while((nl = buf.find('\n')) != std::string::npos){
            std::string line = buf.substr(0, nl); buf.erase(0, nl + 1);
            if(line.empty()) continue;
            std::string reply = handle_line(line) + "\n";
            if(write(fd, reply.data(), reply.size()) < 0) break;
        }
        if(buf.size() > (1 << 20)) break;     // runaway line: drop the client
    }
    close(fd);
    return 0;
}

static void* listen_thread(void*){
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    if(s < 0){ BERR("bridge: socket() failed"); return 0; }
    struct sockaddr_un addr; memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    const char* name = "revenant";                   // abstract: sun_path[0] = '\0'
    memcpy(addr.sun_path + 1, name, strlen(name));
    socklen_t len = offsetof(struct sockaddr_un, sun_path) + 1 + strlen(name);
    if(bind(s, (struct sockaddr*)&addr, len) < 0 || listen(s, 4) < 0){
        BERR("bridge: bind/listen @revenant failed"); close(s); return 0;
    }
    BLOG("bridge listening on @revenant");
    for(;;){
        int c = accept(s, 0, 0);
        if(c < 0){ usleep(100000); continue; }
        // Abstract sockets have no filesystem permissions: any local app could connect and use `call`
        // (arbitrary method execution). Only accept root (0) and shell (2000, adbd's forward).
        struct ucred cred; socklen_t cl = sizeof cred;
        if(getsockopt(c, SOL_SOCKET, SO_PEERCRED, &cred, &cl) < 0 || (cred.uid != 0 && cred.uid != 2000)){
            BERR("bridge: rejected peer uid=%d pid=%d", (int)cred.uid, (int)cred.pid); close(c); continue;
        }
        pthread_t th; pthread_create(&th, 0, client_thread, (void*)(intptr_t)c); pthread_detach(th);
    }
    return 0;
}

void rv_bridge_init(const RvRuntime* rt, bool listen){
    R = *rt;
    void* h = dlopen("libgame.so", RTLD_NOW | RTLD_NOLOAD);
    if(h){
        p_className  = (const char*(*)(id))dlsym(h, "object_getClassName");
        p_getIvar    = (void*(*)(id,const char*,void**))dlsym(h, "object_getInstanceVariable");
        p_ivarOffset = (ptrdiff_t(*)(void*))dlsym(h, "ivar_getOffset");
        p_ivarType   = (const char*(*)(void*))dlsym(h, "ivar_getTypeEncoding");
    }
    BLOG("bridge runtime: className=%p getIvar=%p ivarOffset=%p ivarType=%p",
         (void*)p_className, (void*)p_getIvar, (void*)p_ivarOffset, (void*)p_ivarType);
    g_ready = true;
    if(listen){ pthread_t th; pthread_create(&th, 0, listen_thread, 0); pthread_detach(th); }
}
