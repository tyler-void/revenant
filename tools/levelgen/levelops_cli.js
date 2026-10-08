#!/usr/bin/env node
// The level editor's ref semantics (tools/level-editor/levelops.js) as a JSON stdin/stdout CLI, so the
// Python generator calls the SAME copy/paste/lint code instead of a fork.
//   {"op":"copy",  "ents":[…], "sel":[i…], "srcLid":"1_5"}        -> clipboard {v, srcLid, entities, dropped}
//   {"op":"paste", "ents":[…], "clip":{…}, "dx":0, "dy":0}        -> {ents, added}
//   {"op":"carry", "ents":[…], "sel":[i…]}                        -> {set} (what copy would carry)
//   {"op":"lint",  "level":{…}, "targetLid":"1_24"}               -> {issues}
'use strict';
const path = require('path');
const L = require(path.join(__dirname, '..', 'level-editor', 'levelops.js'));

let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => { buf += d; });
process.stdin.on('end', () => {
  const req = JSON.parse(buf);
  let out;
  switch (req.op) {
    case 'copy': out = L.copyToClipboard(req.ents, req.sel, req.srcLid); break;
    case 'paste': { const ents = req.ents; const r = L.paste(ents, req.clip, req.dx || 0, req.dy || 0); out = { ents, added: r.added }; break; }
    case 'carry': out = { set: [...L.carrySet(req.ents, req.sel)].sort((a, b) => a - b) }; break;
    case 'lint': out = { issues: L.lint(req.level, req.targetLid) }; break;
    default: process.stderr.write('unknown op ' + req.op + '\n'); process.exit(2);
  }
  process.stdout.write(JSON.stringify(out));
});
