/* Pure functions over a run's progress records (agent/progress.py): which items are open, how
   long each took, how repeated reads fold, and which tool an events.jsonl line came from. No DOM
   here; app.js draws. Loaded as a browser script (window.AgentProgress) and by Node tests. */
(function (root) {
  "use strict";

  function buildProgress(records) {
    const items = new Map();
    const roots = [];
    let lastMs = 0;
    for (const record of records || []) {
      if (typeof record.t_ms === "number") lastMs = Math.max(lastMs, record.t_ms);
      if (record.phase === "start") {
        const item = {
          id: record.id, kind: record.kind, name: record.name || null, step: record.step ?? null,
          intent: record.intent || null, readOnly: record.read_only === true, parent: record.parent ?? null,
          startMs: record.t_ms, endMs: null, ok: null, code: null, mismatch: false, tokens: null,
          seenStart: record.events_seen ?? null, seenEnd: null, children: [],
        };
        items.set(item.id, item);
        const parent = item.parent === null ? null : items.get(item.parent);
        if (parent) parent.children.push(item.id); else roots.push(item.id);
      } else if (record.phase === "end") {
        const item = items.get(record.id);
        if (!item) continue;
        item.endMs = record.t_ms;
        item.ok = record.ok === true;
        item.code = record.code || null;
        item.mismatch = record.mismatch === true;
        item.tokens = record.tokens ?? null;
        item.seenEnd = record.events_seen ?? null;
      }
    }
    return { items, roots, lastMs };
  }

  function groupable(item) {
    return item.kind === "cli" || (item.kind === "tool" && item.readOnly);
  }

  /* Folds finished reads (CLI commands, read-only tools) that follow one another by name, so
     alternating reads still give one group per name; anything else ends the run of reads. */
  function groupSiblings(progress, ids) {
    const groups = [];
    let folding = new Map();
    for (const id of ids) {
      const item = progress.items.get(id);
      if (!item) continue;
      const done = item.endMs !== null;
      const key = `${item.kind}\u0000${item.name}`;
      const same = done && groupable(item) ? folding.get(key) : null;
      if (same) {
        same.count += 1;
        same.ms += item.endMs - item.startMs;
        if (!item.ok) same.failures += 1;
        same.items.push(item);
        continue;
      }
      if (!(done && groupable(item))) folding = new Map();
      const group = {
        kind: item.kind, name: item.name, count: 1, ms: done ? item.endMs - item.startMs : null,
        failures: done && !item.ok ? 1 : 0, items: [item], item,
      };
      groups.push(group);
      if (done && groupable(item)) folding.set(key, group);
    }
    return groups;
  }

  function innermostOpen(progress) {
    let found = null;
    for (const item of progress.items.values()) {
      if (item.endMs === null && (found === null || item.id > found.id)) found = item;
    }
    return found;
  }

  function openChain(progress) {
    const chain = [];
    let item = innermostOpen(progress);
    while (item) {
      chain.unshift(item);
      item = item.parent === null ? null : progress.items.get(item.parent);
    }
    return chain;
  }

  function recentDone(progress, n) {
    const open = innermostOpen(progress);
    const ids = open ? open.children : progress.roots;
    const done = ids.filter((id) => progress.items.get(id).endMs !== null);
    return groupSiblings(progress, done).slice(-n);
  }

  function depth(progress, item) {
    let count = 0;
    let parent = item.parent === null ? null : progress.items.get(item.parent);
    while (parent) {
      count += 1;
      parent = parent.parent === null ? null : progress.items.get(parent.parent);
    }
    return count;
  }

  function toolForEventLine(progress, line) {
    let best = null;
    let bestDepth = -1;
    for (const item of progress.items.values()) {
      if (item.kind !== "tool" || item.seenStart === null || item.seenEnd === null) continue;
      if (!(item.seenStart < line && line <= item.seenEnd)) continue;
      const d = depth(progress, item);
      if (d > bestDepth) { best = item; bestDepth = d; }
    }
    return best;
  }

  function itemLabel(item) {
    if (item.kind === "model") return item.name ? `모델(${item.name}) 생각 중` : "모델 생각 중";
    if (item.kind === "ready") return "대상 앱 준비 중";
    if (item.kind === "tool") return item.intent ? `${item.name} · ${item.intent}` : String(item.name);
    return String(item.name);
  }

  function formatSeconds(ms) {
    return `${(ms / 1000).toFixed(1)}초`;
  }

  const api = { buildProgress, groupSiblings, openChain, recentDone, toolForEventLine, itemLabel, formatSeconds };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AgentProgress = api;
})(this);
