/* Revenant level editor — pure level operations (no DOM). Loaded by index.html as a classic script
 * (window.LevelOps) and by the Node tests via require(). Every structural edit goes through here so
 * entity index references stay valid.
 *
 * Index references (VERIFIED on decoded levels 1_1 / 1_25 / 5_1 — targets checked by type, -1 = none):
 *   list refs  Properties.refobjectList   group → its children (sprites, bodies, triggers)
 *              Properties.mountedSprites  terrain body → the EditorSprites drawn on it
 *              Properties.objects         trigger → the joints / bodies / barrels it acts on
 *   single     Properties.refentity       trigger → one joint / body
 *              Properties.reffollowEntity camera → followed entity
 *              Properties.refsprite1      terrain → sprite (always -1 in the corpus so far)
 *   anchors    Anchors[].object_id / object2_id   joint → EditorPhysicsObject body (-1 = world)
 * "Owned" refs (refobjectList, mountedSprites) define the parent→child closure used by delete/move/
 * duplicate; the other refs are links that get remapped but never pull entities along.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.LevelOps = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const REF_LIST_KEYS = ['refobjectList', 'mountedSprites', 'objects'];
  const OWNED_LIST_KEYS = ['refobjectList', 'mountedSprites'];
  const REF_ONE_KEYS = ['refentity', 'reffollowEntity', 'refsprite1'];
  const ANCHOR_REF_KEYS = ['object_id', 'object2_id'];

  const clone = o => JSON.parse(JSON.stringify(o));
  const props = e => e.Properties || {};
  const isJoint = e => !!(e.Anchors && e.Anchors.some(a => ANCHOR_REF_KEYS.some(k => k in a)));

  // ── reference walking ────────────────────────────────────────────────────────────────────
  function ownedChildren(e) {
    const P = props(e), out = [];
    OWNED_LIST_KEYS.forEach(k => { if (Array.isArray(P[k])) P[k].forEach(i => { if (Number.isInteger(i) && i >= 0) out.push(i); }); });
    return out;
  }
  function anchorBodies(e) {
    const out = [];
    (e.Anchors || []).forEach(a => ANCHOR_REF_KEYS.forEach(k => { if (Number.isInteger(a[k]) && a[k] >= 0) out.push(a[k]); }));
    return out;
  }
  /** sel + every owned child, recursively (cycle-safe). */
  function ownedClosure(ents, sel) {
    const out = new Set(), stack = [...sel];
    while (stack.length) {
      const i = stack.pop();
      if (out.has(i) || i < 0 || i >= ents.length) continue;
      out.add(i);
      ownedChildren(ents[i]).forEach(c => stack.push(c));
    }
    return out;
  }
  /** Joints with at least one anchor body in `set`. */
  function jointsTouching(ents, set) {
    const out = new Set();
    ents.forEach((e, i) => { if (isJoint(e) && anchorBodies(e).some(b => set.has(b))) out.add(i); });
    return out;
  }
  /** A joint's two body slots, in order (VERIFIED shapes): revolute/weld = ONE anchor carrying
   *  object_id + object2_id (object2_id absent → the world); distance = TWO anchors with one object_id
   *  each. Fewer than two slots, or -1, means that side is bound to the world. */
  function jointSlots(e) {
    const slots = [];
    (e.Anchors || []).forEach(a => ANCHOR_REF_KEYS.forEach(k => { if (k in a) slots.push(a[k]); }));
    while (slots.length < 2) slots.push(-1);
    return slots;
  }
  /** Joints whose body slots are ALL bodies in `set` (a world side means "not only"). */
  function jointsBoundOnlyTo(ents, set) {
    const out = new Set();
    ents.forEach((e, i) => {
      if (!isJoint(e)) return;
      if (jointSlots(e).every(b => Number.isInteger(b) && b >= 0 && set.has(b))) out.add(i);
    });
    return out;
  }

  /** Rewrite every index ref in an entity through `map(oldIdx) -> newIdx | -1`. */
  function remapEntityRefs(e, map) {
    const P = e.Properties;
    if (P) {
      REF_LIST_KEYS.forEach(k => {
        if (Array.isArray(P[k])) P[k] = P[k].map(i => (Number.isInteger(i) && i >= 0) ? map(i) : i).filter(i => i !== -1);
      });
      REF_ONE_KEYS.forEach(k => { if (Number.isInteger(P[k]) && P[k] >= 0) P[k] = map(P[k]); });
    }
    (e.Anchors || []).forEach(a => ANCHOR_REF_KEYS.forEach(k => {
      if (Number.isInteger(a[k]) && a[k] >= 0) a[k] = map(a[k]);
    }));
    return e;
  }

  // ── delete ───────────────────────────────────────────────────────────────────────────────
  /** What deleting `sel` removes: the selection, its owned children (recursively), and every joint
   *  that touches a removed body. Triggers/cameras that merely link to removed entities stay. */
  function deletePlan(ents, sel) {
    const remove = ownedClosure(ents, sel);
    let grew = true;
    while (grew) {                                   // joints on removed bodies, and their children
      grew = false;
      jointsTouching(ents, remove).forEach(j => { if (!remove.has(j)) { remove.add(j); grew = true; } });
      ownedClosure(ents, [...remove]).forEach(i => { if (!remove.has(i)) { remove.add(i); grew = true; } });
    }
    const byType = {};
    remove.forEach(i => { const t = ents[i].Type; byType[t] = (byType[t] || 0) + 1; });
    return { remove, byType, count: remove.size };
  }
  /** Remove `removeSet` and remap every remaining ref. Returns {ents, map} (map: old → new | -1). */
  function applyDelete(ents, removeSet) {
    const map = new Array(ents.length).fill(-1);
    const kept = [];
    ents.forEach((e, i) => { if (!removeSet.has(i)) { map[i] = kept.length; kept.push(e); } });
    const out = kept.map(e => remapEntityRefs(e, i => (i < map.length ? map[i] : -1)));
    return { ents: out, map };
  }

  // ── move ─────────────────────────────────────────────────────────────────────────────────
  function translateEntity(e, dx, dy) {
    (e.Vertexes || []).forEach(v => { v.x += dx; v.y += dy; });
    const P = props(e);
    if (Array.isArray(P.position) && P.position.length >= 2) { P.position[0] += dx; P.position[1] += dy; }
    (e.Anchors || []).forEach(a => { if (typeof a.x === 'number') a.x += dx; if (typeof a.y === 'number') a.y += dy; });
    return e;
  }
  /** Everything a rigid move of `sel` must carry: owned closure + joints bound only to moved bodies. */
  function moveSet(ents, sel) {
    const set = ownedClosure(ents, sel);
    jointsBoundOnlyTo(ents, set).forEach(j => set.add(j));
    return set;
  }
  function moveSelection(ents, sel, dx, dy) {
    const set = moveSet(ents, sel);
    set.forEach(i => translateEntity(ents[i], dx, dy));
    return set;
  }

  // ── duplicate / copy / paste ─────────────────────────────────────────────────────────────
  /** The set a copy/duplicate carries: owned closure + joints bound only to carried bodies. */
  function carrySet(ents, sel) { return moveSet(ents, sel); }

  /** Duplicate inside the same level: copies are appended; refs between copied entities point at the
   *  copies, refs to entities outside the set keep pointing at the originals. */
  function duplicate(ents, sel, dx = 40, dy = 0) {
    const set = [...carrySet(ents, sel)].sort((a, b) => a - b);
    const base = ents.length, local = new Map();
    set.forEach((oldIdx, k) => local.set(oldIdx, base + k));
    const copies = set.map(i => {
      const c = clone(ents[i]);
      remapEntityRefs(c, j => (local.has(j) ? local.get(j) : j));
      return translateEntity(c, dx, dy);
    });
    copies.forEach(c => ents.push(c));
    return { added: copies.map((_, k) => base + k), map: local };
  }

  /** A self-contained clipboard: refs inside the copied set become clipboard-relative; links to
   *  outside entities are dropped (they would be meaningless in another level). */
  function copyToClipboard(ents, sel, srcLid) {
    const set = [...carrySet(ents, sel)].sort((a, b) => a - b);
    const local = new Map(); set.forEach((oldIdx, k) => local.set(oldIdx, k));
    let dropped = 0;
    const items = set.map(i => {
      const c = clone(ents[i]);
      remapEntityRefs(c, j => { if (local.has(j)) return local.get(j); dropped++; return -1; });
      return c;
    });
    return { v: 1, srcLid: srcLid || null, entities: items, dropped };
  }
  function paste(ents, clip, dx = 0, dy = 0) {
    const base = ents.length;
    const added = clip.entities.map((src, k) => {
      const c = clone(src);
      remapEntityRefs(c, j => base + j);
      translateEntity(c, dx, dy);
      ents.push(c);
      return base + k;
    });
    return { added };
  }

  // ── geometry ─────────────────────────────────────────────────────────────────────────────
  function signedArea(vs) {
    let a = 0;
    for (let i = 0; i < vs.length; i++) { const p = vs[i], q = vs[(i + 1) % vs.length]; a += p.x * q.y - q.x * p.y; }
    return a / 2;
  }
  function isConvex(vs) {
    let sign = 0;
    for (let i = 0; i < vs.length; i++) {
      const a = vs[i], b = vs[(i + 1) % vs.length], c = vs[(i + 2) % vs.length];
      const cr = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
      if (Math.abs(cr) < 1e-9) continue;
      const s = cr > 0 ? 1 : -1;
      if (!sign) sign = s; else if (s !== sign) return false;
    }
    return true;
  }
  /** Nearest control-polygon edge to point p (world coords): {edge: i (between i and i+1), t, x, y, d}. */
  function nearestEdge(vs, p) {
    let best = null;
    for (let i = 0; i < vs.length; i++) {
      const a = vs[i], b = vs[(i + 1) % vs.length];
      const vx = b.x - a.x, vy = b.y - a.y, L2 = vx * vx + vy * vy || 1e-12;
      const t = Math.max(0, Math.min(1, ((p.x - a.x) * vx + (p.y - a.y) * vy) / L2));
      const x = a.x + t * vx, y = a.y + t * vy, d = Math.hypot(p.x - x, p.y - y);
      if (!best || d < best.d) best = { edge: i, t, x, y, d };
    }
    return best;
  }
  /** Insert a vertex after index `edge`, inheriting `segments` from its neighbour (keeps its type). */
  function insertVertex(e, edge, x, y) {
    const vs = e.Vertexes, src = vs[edge];
    const v = Object.assign({}, src, { x, y });
    vs.splice(edge + 1, 0, v);
    return edge + 1;
  }
  function deleteVertex(e, i, minVerts = 3) {
    if (!e.Vertexes || e.Vertexes.length <= minVerts) return false;
    e.Vertexes.splice(i, 1);
    return true;
  }
  function reverseWinding(e) { if (e.Vertexes) e.Vertexes.reverse(); return e; }

  /** A new spline terrain object: Properties cloned from `template` (a terrain object of the same
   *  world, so textures/physics match), refs cleared, wound CCW, position = centroid. */
  function makeTerrain(template, points, name) {
    const e = clone(template);
    const P = e.Properties = e.Properties || {};
    const seg = (template.Vertexes && template.Vertexes[0] && template.Vertexes[0].segments) || 4;
    let vs = points.map(p => ({ x: p.x, y: p.y, segments: seg }));
    if (signedArea(vs) < 0) vs = vs.reverse();
    e.Vertexes = vs;
    P.spline = true;
    if (Array.isArray(P.mountedSprites)) P.mountedSprites = [];
    if ('refsprite1' in P) P.refsprite1 = -1;
    if (name) P.name = name;
    const cx = vs.reduce((s, v) => s + v.x, 0) / vs.length, cy = vs.reduce((s, v) => s + v.y, 0) / vs.length;
    if (Array.isArray(P.position)) P.position = [cx, cy];
    if ('rotation' in P) P.rotation = 0;
    return e;
  }
  /** Best template for new terrain: a static, physical, filled spline object; else any filled one. */
  function terrainTemplate(ents) {
    const T = ents.filter(e => e.Type === 'EditorPhysicsObject' && (e.Vertexes || []).length >= 3);
    const score = e => { const P = props(e); return (P.spline ? 4 : 0) + (P.Static ? 2 : 0) + (P.physics ? 2 : 0) + (P.textureFill ? 1 : 0); };
    return T.sort((a, b) => score(b) - score(a))[0] || null;
  }

  // ── lint ─────────────────────────────────────────────────────────────────────────────────
  /** Issues: {sev: 'error'|'warn'|'info', code, msg, idx?}. errors should block an export. */
  function lint(level, targetLid) {
    const ents = level.Entities || [], n = ents.length, out = [];
    const add = (sev, code, msg, idx) => out.push(idx === undefined ? { sev, code, msg } : { sev, code, msg, idx });
    if (targetLid && level.lid !== targetLid) add('error', 'lid', `lid "${level.lid}" ≠ target slot "${targetLid}" (the game rejects a mismatched level)`);
    const count = t => ents.filter(e => e.Type === t).length;
    [['Moto', 'start (Moto)'], ['TriggerWin', 'finish (TriggerWin)']].forEach(([t, what]) => {
      const c = count(t); if (c !== 1) add('error', 'count_' + t, `exactly one ${what} required, found ${c}`);
    });
    if (!Array.isArray(level.times) || level.times.length !== 3 || level.times.some(t => typeof t !== 'number'))
      add('error', 'times', 'times must be 3 numbers [1★, 2★, 3★]');
    else if (!(level.times[0] >= level.times[1] && level.times[1] >= level.times[2]))
      add('warn', 'times_order', `medal times should be descending (1★ ≥ 2★ ≥ 3★): ${level.times.join(', ')}`);
    ents.forEach((e, i) => {
      const P = props(e);
      REF_LIST_KEYS.forEach(k => (Array.isArray(P[k]) ? P[k] : []).forEach(j => {
        if (!Number.isInteger(j) || j >= n || j < -1) add('error', 'ref', `${e.Type} #${i}: ${k} → ${j} (out of range 0..${n - 1})`, i);
        else if (j === i) add('error', 'ref', `${e.Type} #${i}: ${k} references itself`, i);
      }));
      REF_ONE_KEYS.forEach(k => { if (k in P && (!Number.isInteger(P[k]) || P[k] >= n || P[k] < -1)) add('error', 'ref', `${e.Type} #${i}: ${k} → ${P[k]} (out of range)`, i); });
      (e.Anchors || []).forEach(a => ANCHOR_REF_KEYS.forEach(k => {
        if (!(k in a)) return;
        const j = a[k];
        if (!Number.isInteger(j) || j >= n || j < -1) add('error', 'ref', `${e.Type} #${i}: anchor ${k} → ${j} (out of range)`, i);
        else if (j >= 0 && ents[j].Type !== 'EditorPhysicsObject') add('warn', 'ref_type', `${e.Type} #${i}: anchor ${k} → #${j} is a ${ents[j].Type}, not a body`, i);
      }));
      if (e.Type === 'EditorPhysicsObject' && (e.Vertexes || []).length >= 3) {
        const vs = e.Vertexes;
        if (signedArea(vs) < 0) add('info', 'cw', `#${i} ${P.name || ''}: clockwise winding (real levels ship CW polygons — 1_25 has 58 of 76 — so this is informational)`, i);
        if (!P.spline && P.physics !== false) {
          if (vs.length > 8) add('warn', 'poly_verts', `#${i} ${P.name || ''}: ${vs.length} vertices > 8 (Box2D polygon limit; mapping unconfirmed)`, i);
          if (!isConvex(vs)) add('warn', 'poly_concave', `#${i} ${P.name || ''}: concave polygon (Box2D polygons must be convex; mapping unconfirmed)`, i);
        }
      }
    });
    return out;
  }
  /** Stable key for "this issue already existed in the shipped level" tagging. */
  function issueKey(level, iss) {
    if (iss.idx === undefined) return iss.code;
    const e = level.Entities[iss.idx];
    return iss.code + '|' + e.Type + '|' + JSON.stringify(e.Vertexes || props(e).position || e.Anchors || null);
  }

  return {
    REF_LIST_KEYS, OWNED_LIST_KEYS, REF_ONE_KEYS, ANCHOR_REF_KEYS,
    ownedChildren, anchorBodies, jointSlots, ownedClosure, jointsTouching, jointsBoundOnlyTo, remapEntityRefs, isJoint,
    deletePlan, applyDelete, translateEntity, moveSet, moveSelection,
    carrySet, duplicate, copyToClipboard, paste,
    signedArea, isConvex, nearestEdge, insertVertex, deleteVertex, reverseWinding, makeTerrain, terrainTemplate,
    lint, issueKey, clone,
  };
});
