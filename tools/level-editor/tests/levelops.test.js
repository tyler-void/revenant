// Node tests for levelops.js with SYNTHETIC entities (no game data).
//   node --test tools/level-editor/tests/
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const L = require('../levelops.js');

// A tiny level: a group (#0) owning a body (#1) whose mounted sprite is #2, a second body (#3),
// a joint (#4) between #1 and #3, a world joint (#5) on #3, a trigger (#6) linking the joint #4 and
// body #1, a camera (#7) following #1, plus Moto (#8) and TriggerWin (#9) owning a finish sprite (#10).
function body(x, y, name) {
  return { Type: 'EditorPhysicsObject', Properties: { name, position: [x, y], mountedSprites: [], refsprite1: -1, spline: false, physics: true },
           Vertexes: [{ x: x - 10, y: y - 10, segments: 1 }, { x: x + 10, y: y - 10, segments: 1 }, { x: x + 10, y: y + 10, segments: 1 }, { x: x - 10, y: y + 10, segments: 1 }] };
}
function level() {
  const E = [];
  E[0] = { Type: 'EditorPhysicsEntity', Properties: { name: 'Group', refobjectList: [1] } };
  E[1] = body(0, 0, 'b1'); E[1].Properties.mountedSprites = [2];
  E[2] = { Type: 'EditorSprite', Properties: { name: 's2', position: [0, 0] } };
  E[3] = body(100, 0, 'b3');
  E[4] = { Type: 'EditorPhysicsRevoluteJoint', Properties: { name: 'j4' }, Anchors: [{ x: 50, y: 0, object_id: 1, object2_id: 3 }] };
  E[5] = { Type: 'EditorPhysicsRevoluteJoint', Properties: { name: 'j5' }, Anchors: [{ x: 100, y: 0, object_id: 3 }] };
  E[6] = { Type: 'EditorTrigger', Properties: { name: 't6', position: [5, 5], objects: [4, 1], refentity: 4 } };
  E[7] = { Type: 'EditorCamera', Properties: { name: 'c7', position: [0, 0], reffollowEntity: 1 } };
  E[8] = { Type: 'Moto', Properties: { name: 'moto', position: [-50, 0] } };
  E[9] = { Type: 'TriggerWin', Properties: { name: 'win', refobjectList: [10] } };
  E[10] = { Type: 'EditorSprite', Properties: { name: 'flag', position: [300, 0] } };
  return { lid: '1_5', type: 0, times: [30.0, 25.0, 20.0], Entities: E };
}
const names = ents => ents.map(e => e.Properties.name);

test('ownedClosure follows refobjectList + mountedSprites recursively', () => {
  const E = level().Entities;
  assert.deepEqual([...L.ownedClosure(E, [0])].sort((a, b) => a - b), [0, 1, 2]);
  assert.deepEqual([...L.ownedClosure(E, [9])].sort((a, b) => a - b), [9, 10]);
});

test('delete a group removes its children, mounted sprites and joints on removed bodies', () => {
  const lv = level(), E = lv.Entities;
  const plan = L.deletePlan(E, [0]);
  // group #0, body #1, its sprite #2, joint #4 (touches #1). #3 and world joint #5 survive.
  assert.deepEqual([...plan.remove].sort((a, b) => a - b), [0, 1, 2, 4]);
  const { ents, map } = L.applyDelete(E, plan.remove);
  assert.deepEqual(names(ents), ['b3', 'j5', 't6', 'c7', 'moto', 'win', 'flag']);
  assert.equal(map[3], 0); assert.equal(map[1], -1);
  // world joint now points at b3's new index
  assert.equal(ents[1].Anchors[0].object_id, 0);
  // trigger: removed joint + removed body dropped from objects; single ref -> -1
  assert.deepEqual(ents[2].Properties.objects, []);
  assert.equal(ents[2].Properties.refentity, -1);
  // camera followed the removed body -> -1
  assert.equal(ents[3].Properties.reffollowEntity, -1);
  // finish group child remapped 10 -> 6
  assert.deepEqual(ents[5].Properties.refobjectList, [6]);
  assert.deepEqual(L.lint({ ...lv, Entities: ents }, '1_5').filter(i => i.sev === 'error'), []);
});

test('deleting one body removes every joint touching it, including world joints', () => {
  const E = level().Entities;
  const plan = L.deletePlan(E, [3]);
  assert.deepEqual([...plan.remove].sort((a, b) => a - b), [3, 4, 5]);
});

test('move carries children and joints bound only to moved bodies; world joints stay', () => {
  const E = level().Entities;
  const set = L.moveSelection(E, [0], 10, 5);
  assert.deepEqual([...set].sort((a, b) => a - b), [0, 1, 2]);            // j4 also binds b3 (not moved)
  assert.deepEqual(E[1].Properties.position, [10, 5]);
  assert.deepEqual(E[2].Properties.position, [10, 5]);
  assert.equal(E[1].Vertexes[0].x, 0);                                     // -10 + 10
  assert.equal(E[4].Anchors[0].x, 50);                                     // untouched
  const E2 = level().Entities;
  const set2 = L.moveSelection(E2, [0, 3], 1, 0);                          // both bodies -> j4 moves
  assert.ok(set2.has(4)); assert.ok(!set2.has(5));                         // j5 is bound to the world
  assert.equal(E2[4].Anchors[0].x, 51);
});

test('duplicate copies the closure with refs pointing at the copies', () => {
  const lv = level(), E = lv.Entities;
  const { added } = L.duplicate(E, [0, 3], 200, 0);                        // group(+b1,s2) + b3 + j4
  assert.equal(added.length, 5);
  const byName = n => E.findIndex((e, i) => i >= 11 && e.Properties.name === n);
  const g = byName('Group'), b1 = byName('b1'), s2 = byName('s2'), b3 = byName('b3'), j4 = byName('j4');
  assert.deepEqual(E[g].Properties.refobjectList, [b1]);
  assert.deepEqual(E[b1].Properties.mountedSprites, [s2]);
  assert.deepEqual([E[j4].Anchors[0].object_id, E[j4].Anchors[0].object2_id], [b1, b3]);
  assert.equal(E[b3].Properties.position[0], 300);
  assert.deepEqual(L.lint(lv, '1_5').filter(i => i.sev === 'error'), []);
});

test('copy/paste across levels remaps to clipboard-relative refs and drops outside links', () => {
  const src = level().Entities;
  const clip = L.copyToClipboard(src, [6], '1_5');                         // trigger alone: its links point outside
  assert.equal(clip.entities.length, 1);
  assert.deepEqual(clip.entities[0].Properties.objects, []);
  assert.equal(clip.entities[0].Properties.refentity, -1);
  assert.equal(clip.dropped, 3);
  const clip2 = L.copyToClipboard(src, [0, 3], '1_5');
  const dst = [{ Type: 'Moto', Properties: { name: 'm' } }, { Type: 'TriggerWin', Properties: { name: 'w', refobjectList: [] } }];
  const { added } = L.paste(dst, clip2, 0, 0);
  assert.deepEqual(added, [2, 3, 4, 5, 6]);
  const g = dst.findIndex(e => e.Properties.name === 'Group'), b1 = dst.findIndex(e => e.Properties.name === 'b1');
  assert.deepEqual(dst[g].Properties.refobjectList, [b1]);
  assert.deepEqual(L.lint({ lid: '2_1', times: [3, 2, 1], Entities: dst }, '2_1').filter(i => i.sev === 'error'), []);
});

test('lint: lid, counts, refs, medal order, winding (info), polygon limits (warn)', () => {
  const lv = level();
  lv.Entities.push({ Type: 'Moto', Properties: {} });                       // 2 starts
  lv.Entities[6].Properties.objects = [99];                                 // dangling
  lv.times = [20.0, 25.0, 30.0];                                            // ascending
  lv.Entities[3].Vertexes.reverse();                                        // CW
  lv.Entities[1].Vertexes = Array.from({ length: 9 }, (_, k) => ({ x: Math.cos(k), y: Math.sin(k) * (k % 2 ? 0.3 : 1), segments: 1 }));
  const issues = L.lint(lv, '1_6');
  const codes = issues.map(i => `${i.sev}:${i.code}`);
  for (const c of ['error:lid', 'error:count_Moto', 'error:ref', 'warn:times_order', 'info:cw', 'warn:poly_verts', 'warn:poly_concave'])
    assert.ok(codes.includes(c), `missing ${c} in ${codes}`);
  assert.ok(!codes.includes('error:count_TriggerWin'));
});

test('vertex insert/delete and winding helpers', () => {
  const b = body(0, 0, 'b');
  const near = L.nearestEdge(b.Vertexes, { x: 0, y: -12 });
  assert.equal(near.edge, 0); assert.ok(Math.abs(near.y + 10) < 1e-9);
  const k = L.insertVertex(b, near.edge, near.x, near.y);
  assert.equal(k, 1); assert.equal(b.Vertexes.length, 5); assert.equal(b.Vertexes[1].segments, 1);
  assert.ok(L.deleteVertex(b, 1)); assert.equal(b.Vertexes.length, 4);
  const tri = { Vertexes: [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 0, y: 1 }] };
  assert.ok(!L.deleteVertex(tri, 0));                                       // keep ≥ 3
  assert.ok(L.signedArea(b.Vertexes) > 0);
  L.reverseWinding(b); assert.ok(L.signedArea(b.Vertexes) < 0);
});

test('makeTerrain clones a template, winds CCW, clears refs', () => {
  const tpl = body(0, 0, 'tpl'); tpl.Properties.spline = false; tpl.Properties.mountedSprites = [2];
  const cw = [{ x: 0, y: 0 }, { x: 0, y: 10 }, { x: 10, y: 10 }, { x: 10, y: 0 }];
  const t = L.makeTerrain(tpl, cw, 'new');
  assert.ok(L.signedArea(t.Vertexes) > 0);
  assert.equal(t.Properties.spline, true);
  assert.deepEqual(t.Properties.mountedSprites, []);
  assert.deepEqual(t.Properties.position, [5, 5]);
  assert.equal(tpl.Properties.name, 'tpl');                                 // template untouched
});
