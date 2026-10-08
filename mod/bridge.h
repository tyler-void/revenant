// Revenant agent bridge — JSON lines over the abstract unix socket "revenant".
// Host side: `adb forward tcp:7777 localabstract:revenant`, then one JSON object per line:
//   -> {"id":1,"cmd":"state","args":{}}      <- {"id":1,"ok":true,"result":{...}}
// Commands that touch game objects are queued and run on the GL thread from the swapBuffers hook
// (rv_bridge_frame); nothing here ever runs inside the physics step (gameTime_ stays untouched).
// Every event is also logged as one logcat line "RVEVT {json}" (tag RVMOD), so log watchers work
// even when the socket doesn't.
#pragma once
#include <stdint.h>

typedef void* id;
typedef void* SEL;
typedef void* Class;

struct RvRuntime {
    uintptr_t base;                       // libgame load base
    id    (*msgSend)(id, SEL, ...);       // libgame's objc_msgSend
    SEL   (*selReg)(const char*);         // sel_registerName
    Class (*getClass)(const char*);       // objc_getClass
    volatile int* step_calls;             // advances once per -[Physics step:] (in-level detector)
    id volatile*  physics;                // last -[Physics step:] self (owns gameTime_)
    id volatile*  bike;                   // current bike (captured by the bike-spec setter hooks)
};

// Resolve the runtime helpers (dlsym into libgame) and, when `listen` is true, start the socket thread.
void rv_bridge_init(const RvRuntime* rt, bool listen);
// GL thread, once per frame (from hook_swap): run queued commands + goto_level job + race tracking.
void rv_bridge_frame(bool in_level);
// Thread-safe event emit. `fields` is a JSON object body without braces ("\"lid\":\"1_24\"") or "".
void rv_event(const char* type, const char* fields);
// Level file seen by the encrypted-file reader (basename like "1_24.dat"); sets the current lid.
void rv_note_level_file(const char* basename);
// level_loaded / finish helpers for the MotoXGame hooks.
void rv_on_level_loaded();
void rv_on_level_finished(id arg);
// -[LevelSelectionMenu didFinishLoading] fired: the career map is built and safe to start a level from.
void rv_on_menu_ready(id menu);
// True while the agent asked to hide the ImGui overlay (overlay hidden).
bool rv_overlay_hidden();
