import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const P = createRequire(import.meta.url)("../../viewer/static/progress.js");

const s = (id, kind, t, f = {}) => ({ id, phase: "start", kind, t_ms: t, ...f });
const e = (id, kind, t, f = {}) => ({ id, phase: "end", kind, t_ms: t, ok: true, ...f });

test("buildProgress links children and keeps open items open", () => {
  const p = P.buildProgress([s(1, "tool", 0, { name: "click_part", intent: "연다" }), s(2, "cli", 5, { parent: 1, name: "mouse-click" }), e(2, "cli", 50)]);
  assert.deepEqual(p.roots, [1]);
  assert.deepEqual(p.items.get(1).children, [2]);
  assert.equal(p.items.get(1).endMs, null);
  assert.equal(p.items.get(2).endMs, 50);
  assert.equal(p.lastMs, 50);
});

test("openChain lists open items outermost first", () => {
  const p = P.buildProgress([s(1, "tool", 0, { name: "click_part" }), s(2, "cli", 5, { parent: 1, name: "mouse-click" })]);
  assert.deepEqual(P.openChain(p).map((i) => i.id), [1, 2]);
});

test("openChain is empty when nothing is open", () => {
  assert.deepEqual(P.openChain(P.buildProgress([s(1, "model", 0), e(1, "model", 10)])), []);
});

test("groupSiblings folds repeated reads and counts failures", () => {
  const records = [s(1, "ready", 0)];
  for (let i = 0; i < 4; i++) records.push(s(2 + i, "cli", i * 500, { parent: 1, name: "snapshot" }), e(2 + i, "cli", i * 500 + 500, { ok: i !== 0 }));
  const p = P.buildProgress(records);
  const groups = P.groupSiblings(p, p.items.get(1).children);
  assert.equal(groups.length, 1);
  assert.deepEqual([groups[0].name, groups[0].count, groups[0].ms, groups[0].failures], ["snapshot", 4, 2000, 1]);
});

test("groupSiblings folds read-only tools but not acting ones", () => {
  const p = P.buildProgress([
    s(1, "tool", 0, { name: "observe", read_only: true }), e(1, "tool", 100),
    s(2, "tool", 100, { name: "observe", read_only: true }), e(2, "tool", 200),
    s(3, "tool", 200, { name: "click_part" }), e(3, "tool", 300),
    s(4, "tool", 300, { name: "click_part" }), e(4, "tool", 400)]);
  assert.deepEqual(P.groupSiblings(p, p.roots).map((g) => [g.name, g.count]), [["observe", 2], ["click_part", 1], ["click_part", 1]]);
});

test("toolForEventLine picks the innermost tool around the line", () => {
  const p = P.buildProgress([
    s(1, "tool", 0, { name: "click", events_seen: 3 }), e(1, "tool", 100, { events_seen: 5 }),
    s(2, "tool", 200, { name: "click", events_seen: 5 }), e(2, "tool", 300, { events_seen: 7 })]);
  assert.equal(P.toolForEventLine(p, 5).id, 1);
  assert.equal(P.toolForEventLine(p, 6).id, 2);
  assert.equal(P.toolForEventLine(p, 9), null);
});

test("toolForEventLine prefers a nested tool", () => {
  const p = P.buildProgress([
    s(1, "tool", 0, { name: "click", events_seen: 1 }),
    s(2, "tool", 10, { name: "observe", parent: 1, events_seen: 2 }), e(2, "tool", 20, { events_seen: 3 }),
    e(1, "tool", 30, { events_seen: 4 })]);
  assert.equal(P.toolForEventLine(p, 3).id, 2);
});

test("recentDone keeps the last n finished siblings of the innermost open item", () => {
  const records = [s(1, "tool", 0, { name: "click" })];
  for (let i = 0; i < 7; i++) records.push(s(10 + i, "cli", i * 10, { parent: 1, name: `c${i}` }), e(10 + i, "cli", i * 10 + 5));
  assert.deepEqual(P.recentDone(P.buildProgress(records), 5).map((g) => g.name), ["c2", "c3", "c4", "c5", "c6"]);
});

test("recentDone falls back to the top level when nothing is open", () => {
  const p = P.buildProgress([s(1, "model", 0), e(1, "model", 10), s(2, "tool", 10, { name: "finish" }), e(2, "tool", 20)]);
  assert.deepEqual(P.recentDone(p, 5).map((g) => g.item.kind), ["model", "tool"]);
});

test("itemLabel names each kind", () => {
  const p = P.buildProgress([s(1, "model", 0, { step: 3 }), s(2, "tool", 0, { name: "click_part", intent: "연다" }), s(3, "ready", 0), s(4, "cli", 0, { name: "snapshot" }), s(5, "model", 0, { name: "vision" })]);
  assert.deepEqual([1, 2, 3, 4, 5].map((id) => P.itemLabel(p.items.get(id))), ["모델 생각 중", "click_part · 연다", "대상 앱 준비 중", "snapshot", "모델(vision) 생각 중"]);
});

test("formatSeconds", () => {
  assert.equal(P.formatSeconds(2800), "2.8초");
  assert.equal(P.formatSeconds(0), "0.0초");
});

test("groupSiblings folds alternating reads by name", () => {
  const records = [s(1, "ready", 0)];
  for (let i = 0; i < 3; i++) {
    records.push(s(10 + 2 * i, "cli", i * 500, { parent: 1, name: "list-windows" }), e(10 + 2 * i, "cli", i * 500 + 100));
    records.push(s(11 + 2 * i, "cli", i * 500 + 100, { parent: 1, name: "snapshot" }), e(11 + 2 * i, "cli", i * 500 + 400));
  }
  const p = P.buildProgress(records);
  assert.deepEqual(P.groupSiblings(p, p.items.get(1).children).map((g) => [g.name, g.count, g.ms]), [["list-windows", 3, 300], ["snapshot", 3, 900]]);
});

test("an acting tool between reads starts a new fold", () => {
  const p = P.buildProgress([
    s(1, "tool", 0, { name: "observe", read_only: true }), e(1, "tool", 10),
    s(2, "tool", 10, { name: "click_part" }), e(2, "tool", 20),
    s(3, "tool", 20, { name: "observe", read_only: true }), e(3, "tool", 30)]);
  assert.deepEqual(P.groupSiblings(p, p.roots).map((g) => [g.name, g.count]), [["observe", 1], ["click_part", 1], ["observe", 1]]);
});
