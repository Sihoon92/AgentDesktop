"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const TOKEN_KEY = "agent-viewer-token";
const STATUS_LABELS = {
  running: "실행 중",
  verified: "검증됨",
  confirmed: "사람이 확인함",
  claimed: "주장만 함",
  gave_up: "포기",
  stopped: "중단",
  failed: "실패",
  interrupted: "중단됨",
  unreadable: "읽을 수 없음",
  legacy: "옛 로그",
};
const STARTED_BY_LABELS = { "run.py": "run.py", "run.py --view": "run.py --view", viewer: "뷰어", baseline: "baseline" };
const KEEP_FILTER = "keep";
const KNOWN_EXPORT = 1;
const EXPORT_WARNING = "이 파일에는 앱 화면과 UI 텍스트가 들어 있습니다. 공유하기 전에 내용을 확인하세요.";
const FILTER_STATUSES = ["verified", "confirmed", "claimed", "gave_up", "stopped", "running", "interrupted", "unreadable", "legacy"];
const SEARCH_DELAY_MS = 250;
const SUMMARY_EVERY_IDLE_POLLS = 5;
const CHANGE_LINES = 30;
const VERDICT_METHODS = {
  verified: "하니스가 확인",
  confirmed: "사람이 결과를 확인",
  claimed: "모델 주장만, 미검증",
  gave_up: "포기",
  stopped: "중단",
  interrupted: "중단됨",
  running: "실행 중",
  unreadable: "run.json을 읽을 수 없음",
  legacy: "옛 로그",
};

const state = {
  token: null,
  runs: [],
  runsNext: null,
  listSeq: 0,
  filters: { q: "", status: "", since: "", keep: false },
  exportMode: false,
  exported: null,
  run: null,
  runId: null,
  runKind: "run",
  entries: [],
  tokensByStep: new Map(),
  index: 0,
  view: "before",
  boxes: "all",
  texts: true,
  blobs: new Map(),
  renderSeq: 0,
  events: [],
  next: 0,
  finished: false,
  polling: false,
  idlePolls: 0,
  status: null,
  drafts: new Map(),
  pin: null,
  sentAnswers: new Set(),
  answerDrafts: new Map(),
  registrationDrafts: new Map(),
  composerKey: null,
  chatShown: null,
  jumpedQuestion: null,
  baseTitle: "",
};
const POLL_MS = 1000;
const LEVEL_LABELS = ["0 · 커서 안 씀", "1 · 값 입력·키", "2 · 마우스·타이핑", "3 · 전체"];

const $ = (id) => document.getElementById(id);

function readToken() {
  const params = new URLSearchParams(location.search);
  let token = params.get("t");
  if (token) {
    try { sessionStorage.setItem(TOKEN_KEY, token); } catch (_) { /* storage may be blocked */ }
    history.replaceState(null, "", location.pathname + location.hash);
  } else {
    try { token = sessionStorage.getItem(TOKEN_KEY); } catch (_) { token = null; }
  }
  return token;
}

async function api(path) {
  const response = await fetch(path, { headers: { "X-Agent-Token": state.token || "" }, cache: "no-store" });
  if (response.status === 403) throw new Error("서버가 요청을 거부했습니다. 터미널에 출력된 주소로 다시 여세요.");
  if (!response.ok) throw new Error(`요청 실패 (${response.status}): ${path}`);
  return response;
}

async function frameUrl(runId, name) {
  if (state.exportMode) {
    const uri = (state.exported.frames || {})[name];
    if (typeof uri !== "string" || !uri.startsWith("data:image/png;base64,")) throw new Error(`내보낸 파일에 ${name} 프레임이 없습니다.`);
    return uri;
  }
  const key = `${runId}/${name}`;
  if (!state.blobs.has(key)) {
    const response = await api(`/api/runs/${encodeURIComponent(runId)}/frames/${encodeURIComponent(name)}`);
    state.blobs.set(key, URL.createObjectURL(await response.blob()));
  }
  return state.blobs.get(key);
}

function releaseFrames() {
  for (const url of state.blobs.values()) URL.revokeObjectURL(url);
  state.blobs.clear();
}

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined && options.text !== null) node.textContent = String(options.text);
  for (const [name, value] of Object.entries(options.attrs || {})) node.setAttribute(name, value);
  for (const child of children) if (child) node.append(child);
  return node;
}

function svg(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

function statusBadge(status) {
  const label = STATUS_LABELS[status] || status || "알 수 없음";
  return el("span", { className: `badge ${status || ""}`, text: label });
}

function formatTime(text) {
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text || "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function showBanner(messages) {
  const banner = $("banner");
  const lines = messages.filter(Boolean);
  banner.replaceChildren(...lines.map((line) => el("div", { text: line })));
  banner.hidden = lines.length === 0;
}

function buildEntries(events) {
  const observations = new Map();
  const beforeIds = new Set();
  const tokensByStep = new Map();
  const knowledgeUsed = new Map();
  const knowledgeFailed = new Map();
  const byStep = (map, event) => {
    if (!map.has(event.step)) map.set(event.step, []);
    map.get(event.step).push(event);
  };
  for (const event of events) {
    if (event.type === "observation") observations.set(event.id, event);
    if (event.type === "action") beforeIds.add(event.before);
    if (event.type === "model") tokensByStep.set(event.step, event.tokens);
    if (event.type === "knowledge_used") byStep(knowledgeUsed, event);
    if (event.type === "knowledge_failed") byStep(knowledgeFailed, event);
  }
  const entries = [];
  const proposals = new Map();
  const questions = new Map();
  let awaitingAfter = null;
  let awaitingNext = null;
  let lastObservation = null;
  let approved = null;
  for (const event of events) {
    if (event.type === "proposal") {
      const entry = { kind: "proposal", proposal: event, decision: null, before: lastObservation, absorbed: false };
      proposals.set(event.id, entry);
      entries.push(entry);
    } else if (event.type === "decision") {
      const entry = proposals.get(event.id);
      if (entry) {
        entry.decision = event;
        approved = event.approved ? entry : null;
      }
    } else if (event.type === "question") {
      const observation = observations.get(event.observation) || lastObservation;
      const entry = { kind: "question", question: event, answer: null, observation };
      questions.set(event.id, entry);
      entries.push(entry);
    } else if (event.type === "answer") {
      const entry = questions.get(event.id);
      if (entry) entry.answer = event;
    } else if (event.type === "observation") {
      lastObservation = event;
      if (awaitingNext) {
        awaitingNext.next = event;
        awaitingNext = null;
      }
      if (event.source === "wait_for_text") {
        entries.push({ kind: "wait", observation: event });
      } else if (awaitingAfter && showsActionResult(event)) {
        awaitingAfter.after = event;
        awaitingAfter = null;
      } else if (!beforeIds.has(event.id) && event.source !== "question") {
        entries.push({ kind: "observation", observation: event });
      }
    } else if (event.type === "action") {
      awaitingAfter = {
        kind: "action", action: event, before: observations.get(event.before) || null, after: null, next: null,
        knowledgeUsed: knowledgeUsed.get(event.step) || [], knowledgeFailed: knowledgeFailed.get(event.step) || [],
      };
      awaitingNext = awaitingAfter;
      if (approved && approved.proposal.tool === event.tool) {
        awaitingAfter.approval = approved.decision;
        approved.absorbed = true;
      }
      approved = null;
      entries.push(awaitingAfter);
    } else if (event.type === "finish") {
      entries.push({ kind: "finish", finish: event, observation: lastObservation });
    }
  }
  return { entries: entries.filter((entry) => !entry.absorbed), tokensByStep };
}

function elementKey(item) {
  return JSON.stringify([elementKind(item), item.role || "", item.name || "", item.description || ""]);
}

function describeElement(item) {
  const label = item.name || item.description || "";
  return label ? `${item.role || "요소"} "${label}"` : item.role || "요소";
}

function recordsStates(observation) {
  const elements = observation.elements || [];
  return elements.length > 0 && elements.every((item) => Array.isArray(item.states));
}

function diffObservations(before, after) {
  const waiting = new Map();
  for (const item of before.elements || []) {
    const key = elementKey(item);
    if (!waiting.has(key)) waiting.set(key, []);
    waiting.get(key).push(item);
  }
  const pairs = [];
  const appeared = [];
  for (const item of after.elements || []) {
    const queue = waiting.get(elementKey(item));
    if (queue && queue.length > 0) pairs.push([queue.shift(), item]);
    else appeared.push(item);
  }
  const paired = new Set(pairs.map(([old]) => old));
  const disappeared = (before.elements || []).filter((item) => !paired.has(item));
  const statesKnown = recordsStates(before) && recordsStates(after);
  const changed = [];
  if (statesKnown) {
    for (const [old, now] of pairs) {
      const on = now.states.filter((name) => !old.states.includes(name));
      const off = old.states.filter((name) => !now.states.includes(name));
      const oldValue = old.value === undefined ? null : old.value;
      const newValue = now.value === undefined ? null : now.value;
      const value = oldValue !== newValue ? { from: oldValue, to: newValue } : null;
      if (on.length || off.length || value) changed.push({ before: old, after: now, on, off, value });
    }
  }
  return { appeared, disappeared, changed, statesKnown };
}

function changeLines(diff, limit = CHANGE_LINES) {
  const lines = [];
  for (const item of diff.appeared) lines.push({ text: `생김 · ${describeElement(item)}`, tone: "appeared" });
  for (const item of diff.disappeared) lines.push({ text: `사라짐 · ${describeElement(item)}`, tone: "disappeared" });
  for (const change of diff.changed) {
    for (const name of change.on) lines.push({ text: `${describeElement(change.after)}: ${name} 켜짐`, tone: "on" });
    for (const name of change.off) lines.push({ text: `${describeElement(change.after)}: ${name} 꺼짐`, tone: "off" });
  }
  const quote = (value) => (value === null ? "없음" : `"${value}"`);
  for (const change of diff.changed) {
    if (change.value) lines.push({ text: `${describeElement(change.after)}: 값 ${quote(change.value.from)} → ${quote(change.value.to)}`, tone: "value" });
  }
  if (lines.length <= limit) return lines;
  return lines.slice(0, limit).concat({ text: `외 ${lines.length - limit}개`, tone: "more" });
}

function comparedObservation(entry) {
  return entry.after || entry.next || null;
}

function entryDiff(entry) {
  const after = comparedObservation(entry);
  if (!entry.before || !after) return null;
  if (!entry.diff || entry.diff.after !== after) entry.diff = { after, result: diffObservations(entry.before, after) };
  return entry.diff.result;
}

function changeMarks(entry, observation) {
  const marks = { highlight: [], gone: [] };
  if (!entry || entry.kind !== "action" || !observation) return marks;
  const diff = entryDiff(entry);
  if (!diff) return marks;
  if (observation === comparedObservation(entry)) {
    marks.highlight = diff.appeared.concat(diff.changed.map((change) => change.after)).filter((item) => item.rect);
  } else if (observation === entry.before) {
    marks.gone = diff.disappeared.filter((item) => item.rect);
  }
  return marks;
}

function changesSection(entry) {
  const nodes = [el("h3", { text: "바뀐 점" })];
  const after = comparedObservation(entry);
  if (!entry.before || !after) {
    nodes.push(el("p", { className: "hint", text: entry.before ? "동작 뒤 관찰 없음" : "동작 전 관찰이 없어 비교할 수 없습니다." }));
    return nodes;
  }
  const diff = entryDiff(entry);
  const lines = changeLines(diff);
  if (!diff.statesKnown) nodes.push(el("p", { className: "hint", text: "상태 정보 없는 옛 기록입니다. 생김·사라짐만 보입니다." }));
  if (after !== entry.after) nodes.push(el("p", { className: "hint", text: `동작 뒤 첫 관찰(관찰 ${after.id})과 비교합니다.` }));
  if (lines.length === 0) nodes.push(el("p", { className: "hint", text: "바뀐 요소가 없습니다." }));
  else nodes.push(el("ul", { className: "changes" }, lines.map((line) => el("li", { className: `change ${line.tone}`, text: line.text }))));
  return nodes;
}

function approvalCounts(events) {
  const proposals = events.filter((event) => event.type === "proposal");
  if (proposals.length === 0) return null;
  const decisions = events.filter((event) => event.type === "decision");
  const approved = decisions.filter((event) => event.approved).length;
  const rejected = decisions.length - approved;
  return { approved, rejected, unanswered: Math.max(0, proposals.length - decisions.length) };
}

function runSummary(run, events) {
  if (!run) return null;
  const models = events.filter((event) => event.type === "model");
  const lastModel = models[models.length - 1];
  const steps = typeof run.steps === "number" && run.steps > 0 ? run.steps : lastModel ? lastModel.step : run.steps;
  const tokens = typeof run.tokens === "number" && run.tokens > 0 ? run.tokens : lastModel ? lastModel.tokens : run.tokens;
  const approvals = approvalCounts(events);
  const parts = [
    formatDuration(run.duration_s),
    typeof steps === "number" ? `스텝 ${steps}` : null,
    typeof tokens === "number" ? `토큰 ${tokens.toLocaleString("en-US")}` : null,
    approvals ? `승인 ${approvals.approved} · 거부 ${approvals.rejected}${approvals.unanswered ? ` · 답 없음 ${approvals.unanswered}` : ""}` : null,
    run.started_by ? `시작 ${STARTED_BY_LABELS[run.started_by] || run.started_by}` : null,
    run.exe_name || null,
  ].filter(Boolean);
  return {
    goal: run.kind === "legacy" ? `옛 로그 ${run.run_id}` : run.goal || "(지시 없음)",
    status: run.status,
    method: VERDICT_METHODS[run.status] || run.status || "",
    parts,
  };
}

function renderRunSummary() {
  const box = $("run-summary");
  const summary = runSummary(state.run, state.events);
  box.hidden = !summary;
  if (!summary) return;
  box.replaceChildren(...[
    el("span", { className: "summary-goal", text: summary.goal }),
    statusBadge(summary.status),
    el("span", { className: "summary-method", text: summary.method }),
    el("span", { className: "summary-parts", text: summary.parts.join(" · ") }),
    keepToggle(state.run),
  ].filter(Boolean));
}

function keepToggle(run) {
  if (state.exportMode || !run || run.kind !== "run") return null;
  const kept = Boolean(run.keep);
  const button = el("button", {
    className: "keep-toggle",
    text: kept ? "★" : "☆",
    attrs: {
      type: "button", "aria-pressed": String(kept),
      title: kept ? "보관 해제 (정리할 때 지울 수 있게)" : "보관 (정리할 때 지우지 않음)",
      "aria-label": kept ? `${run.run_id} 보관 해제` : `${run.run_id} 보관`,
    },
  });
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    setKeep(run.run_id, !kept);
  });
  return button;
}

async function setKeep(runId, keep) {
  let response;
  try {
    response = await fetch(`/api/runs/${encodeURIComponent(runId)}/keep`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Agent-Token": state.token || "" },
      body: JSON.stringify({ keep }),
      cache: "no-store",
    });
  } catch (error) {
    showBanner([`보관 표시를 바꾸지 못했습니다: ${error.message}`]);
    return;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    showBanner([body.error || `보관 표시를 바꾸지 못했습니다 (${response.status})`]);
    return;
  }
  for (const run of state.runs) if (run.kind === "run" && run.run_id === runId) run.keep = body.keep;
  if (state.run && state.run.kind === "run" && state.run.run_id === runId) state.run = { ...state.run, keep: body.keep };
  if (state.filters.keep && !body.keep) await reloadRuns();
  else renderRunList();
  renderRunSummary();
}

function showsActionResult(observation) {
  return observation.source === undefined || observation.source === "action" || observation.source === "launch";
}

function pendingProposal() {
  if (!isLive()) return null;
  return state.entries.find((entry) => entry.kind === "proposal" && !entry.decision) || null;
}

function shownObservation(entry) {
  if (!entry) return null;
  if (entry.kind === "action") return state.view === "after" ? entry.after : entry.before;
  if (entry.kind === "proposal") return entry.before;
  return entry.observation;
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function localDate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function sinceForServer(day) {
  if (!day) return "";
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date).toISOString().slice(0, 10);
}

function runsQuery(filters, before, limit) {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.status) params.set("status", filters.status);
  if (filters.keep) params.set("keep", "1");
  if (filters.since) params.set("since", sinceForServer(filters.since));
  if (before) params.set("before", before);
  if (limit) params.set("limit", String(limit));
  const text = params.toString();
  return text ? `?${text}` : "";
}

function runDay(run) {
  const started = new Date(run.started_at || run.sort_at);
  if (!Number.isNaN(started.getTime())) return localDate(started);
  return run.date || "";
}

function keepSince(runs, since) {
  return since ? runs.filter((run) => runDay(run) >= since) : runs;
}

function groupRunsByDay(runs) {
  const groups = [];
  for (const run of runs) {
    const day = runDay(run);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.runs.push(run);
    else groups.push({ day, runs: [run] });
  }
  return groups;
}

function dayLabel(day, today) {
  if (!day) return "날짜 모름";
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (day === localDate(today)) return `오늘 · ${day}`;
  if (day === localDate(yesterday)) return `어제 · ${day}`;
  return day;
}

function formatDuration(seconds) {
  if (typeof seconds !== "number" || seconds < 0) return null;
  if (seconds < 60) return `${seconds}초`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
  return `${Math.floor(seconds / 3600)}시간 ${Math.floor((seconds % 3600) / 60)}분`;
}

function runMetaParts(run) {
  if (run.kind === "legacy") {
    return ["스크린샷 없음", typeof run.steps === "number" ? `스텝 ${run.steps}` : null,
      run.result ? `결과 ${STATUS_LABELS[run.result] || run.result}` : null].filter(Boolean);
  }
  return [
    typeof run.steps === "number" ? `스텝 ${run.steps}` : null,
    typeof run.tokens === "number" ? `토큰 ${run.tokens.toLocaleString("en-US")}` : null,
    formatDuration(run.duration_s),
    run.started_by ? STARTED_BY_LABELS[run.started_by] || run.started_by : null,
    run.exe_name || null,
  ].filter(Boolean);
}

function runTitle(run) {
  if (run.kind === "legacy") return `옛 로그 ${run.run_id}`;
  if (run.status === "unreadable") return `${run.run_id} (run.json을 읽을 수 없음)`;
  return run.goal || "(지시 없음)";
}

function runNotices(run) {
  if (!run) return [];
  if (run.kind === "legacy") return ["옛 로그입니다. 스크린샷 없이 단계마다 도구·인자·결과만 보입니다."];
  const notices = [];
  if (run.status === "interrupted") {
    notices.push(`이 실행은 중단됐습니다${run.interrupted_reason ? ` (${run.interrupted_reason})` : ""}. 기록이 여기서 끊겼습니다.`);
  }
  if (state.exportMode && run.status === "running") notices.push("내보낼 때 실행 중이던 기록입니다. 그 뒤의 단계는 들어 있지 않습니다.");
  if (run.status === "unreadable") notices.push("run.json을 읽을 수 없습니다(깨졌거나 쓰는 중). 기록된 단계만 보입니다.");
  if (run.newer_format) notices.push(`새 형식${run.format ? `(format ${run.format})` : ""}입니다. 이 뷰어가 아는 부분만 표시합니다.`);
  return notices;
}

function filtersActive() {
  return Boolean(state.filters.q || state.filters.status || state.filters.since || state.filters.keep);
}

async function fetchRuns(append = false) {
  const seq = ++state.listSeq;
  const before = append ? state.runsNext : null;
  const response = await api(`/api/runs${runsQuery(state.filters, before)}`);
  const page = await response.json();
  if (seq !== state.listSeq) return;
  const fresh = keepSince(page.runs || [], state.filters.since);
  state.runs = append ? state.runs.concat(fresh) : fresh;
  state.runsNext = page.next_before || null;
  const listed = state.runs.find((run) => run.kind === state.runKind && run.run_id === state.runId);
  if (listed) state.run = listed;
  renderRunList();
}

async function loadMoreRuns() {
  if (!state.runsNext) return;
  try {
    await fetchRuns(true);
  } catch (error) {
    showBanner([error.message]);
  }
}

async function reloadRuns() {
  try {
    await fetchRuns();
  } catch (error) {
    showBanner([error.message]);
  }
}

async function loadRuns() {
  try {
    await fetchRuns();
  } catch (error) {
    showBanner([error.message]);
    return;
  }
  if (state.runId) return selectRun(state.runId, state.runKind);
  const hash = new URLSearchParams(location.hash.slice(1));
  if (hash.get("run")) return selectRun(hash.get("run"), "run");
  if (hash.get("legacy")) return selectRun(hash.get("legacy"), "legacy");
  if (state.runs[0]) await selectRun(state.runs[0].run_id, state.runs[0].kind);
}

function isLive() {
  if (state.exportMode) return false;
  const run = state.run;
  return Boolean(run && run.kind === "run" && run.run_id === state.runId && run.status === "running" && !state.finished);
}

function renderRunList() {
  const list = $("run-list");
  const today = new Date();
  const items = [];
  for (const group of groupRunsByDay(state.runs)) {
    items.push(el("li", { className: "run-day", text: dayLabel(group.day, today), attrs: { role: "presentation" } }));
    for (const run of group.runs) {
      const current = run.run_id === state.runId && run.kind === state.runKind;
      const time = formatTime(run.started_at || run.sort_at);
      const button = el("button", { className: `run-item ${run.kind}`, attrs: { type: "button", "aria-current": String(current) } }, [
        el("span", { className: "run-goal", text: runTitle(run) }),
        el("span", { className: "run-meta" }, [statusBadge(run.status), el("span", { text: time })]),
        el("span", { className: "run-facts", text: runMetaParts(run).join(" · ") }),
      ]);
      button.addEventListener("click", () => selectRun(run.run_id, run.kind));
      items.push(el("li", { className: "run-row" }, [button, keepToggle(run)]));
    }
  }
  list.replaceChildren(...items);
  const empty = $("run-empty");
  empty.hidden = state.runs.length > 0;
  empty.textContent = filtersActive() ? "조건에 맞는 실행이 없습니다." : "기록된 실행이 없습니다.";
  $("run-more").hidden = !state.runsNext;
}

async function fetchRunSummary(runId) {
  const response = await api(`/api/runs/${encodeURIComponent(runId)}`);
  return (await response.json()).run;
}

async function selectRun(runId, kind = "run") {
  if (runId !== state.runId || kind !== state.runKind) {
    releaseFrames();
    state.pin = null;
    state.chatShown = null;
    state.composerKey = null;
  }
  state.runId = runId;
  state.runKind = kind;
  if (!document.body.classList.contains("knowledge-mode")) {
    history.replaceState(null, "", `${location.pathname}#${kind === "legacy" ? "legacy" : "run"}=${encodeURIComponent(runId)}`);
  }
  renderRunList();
  if (kind === "legacy") return openLegacy(runId);
  let page;
  let summary;
  try {
    const [events, run] = await Promise.all([
      api(`/api/runs/${encodeURIComponent(runId)}/events?since=0`).then((response) => response.json()),
      fetchRunSummary(runId),
    ]);
    page = events;
    summary = run;
  } catch (error) {
    if (runId === state.runId) showBanner([`실행 ${runId}을(를) 열지 못했습니다. ${error.message}`]);
    return;
  }
  if (runId !== state.runId || kind !== state.runKind) return;
  state.run = summary;
  state.events = page.events || [];
  state.next = page.next || 0;
  state.finished = state.events.some((event) => event.type === "finish");
  rebuildEntries();
  state.view = "before";
  if (!isLive()) select(0);
  else if (!followPendingQuestion()) followLatest();
}

async function openLegacy(runId) {
  let opened;
  try {
    opened = await (await api(`/api/legacy/${encodeURIComponent(runId)}`)).json();
  } catch (error) {
    if (runId === state.runId) showBanner([`옛 로그 ${runId}을(를) 열지 못했습니다. ${error.message}`]);
    return;
  }
  if (runId !== state.runId || state.runKind !== "legacy") return;
  state.run = opened.run;
  state.events = [];
  state.next = 0;
  state.finished = true;
  state.entries = legacyEntries(opened.steps || []);
  state.tokensByStep = new Map();
  state.view = "before";
  select(0);
}

function legacyEntries(steps) {
  return steps.map((step) => ({ kind: "legacy", step }));
}

async function refreshSelectedRun() {
  if (state.runKind !== "run" || !state.runId) return;
  const runId = state.runId;
  const summary = await fetchRunSummary(runId);
  if (runId === state.runId && state.runKind === "run") state.run = summary;
}

function rebuildEntries() {
  const built = buildEntries(state.events);
  state.entries = built.entries;
  state.tokensByStep = built.tokensByStep;
}

function followLatest() {
  const last = state.entries[state.entries.length - 1];
  state.view = last && last.kind === "action" && last.after ? "after" : "before";
  select(state.entries.length - 1);
}

async function pollLive() {
  if (state.polling || !isLive()) return;
  state.polling = true;
  const runId = state.runId;
  try {
    const response = await api(`/api/runs/${encodeURIComponent(runId)}/events?since=${state.next}`);
    const page = await response.json();
    if (runId !== state.runId) return;
    const fresh = page.events || [];
    state.next = page.next;
    if (fresh.length === 0 && !state.finished && ++state.idlePolls >= SUMMARY_EVERY_IDLE_POLLS) {
      state.idlePolls = 0;
      await refreshSelectedRun();
      if (runId === state.runId && !isLive()) {
        render();
        await fetchRuns();
        await loadStatus();
      }
      return;
    }
    if (fresh.length > 0) {
      state.idlePolls = 0;
      const atEnd = state.index >= state.entries.length - 1;
      state.events = state.events.concat(fresh);
      state.finished = state.events.some((event) => event.type === "finish");
      rebuildEntries();
      if (followPendingQuestion()) return;
      if (atEnd) followLatest(); else render();
    }
    if (state.finished) {
      await refreshSelectedRun();
      if (runId === state.runId) renderRunSummary();
      await fetchRuns();
      await loadStatus();
    }
  } catch (error) {
    showBanner([error.message]);
  } finally {
    state.polling = false;
  }
}

function select(index) {
  if (state.entries.length === 0) {
    state.index = 0;
    render();
    return;
  }
  state.index = Math.max(0, Math.min(index, state.entries.length - 1));
  const entry = state.entries[state.index];
  if (entry.kind === "action") {
    if (state.view === "before" && !entry.before && entry.after) state.view = "after";
    else if (state.view === "after" && !entry.after) state.view = "before";
  }
  render();
}

function render() {
  renderRunSummary();
  renderTimeline();
  renderToggles();
  renderDetails();
  renderStage();
  renderChat();
  updateTitle();
}

function timelineLabel(entry) {
  if (entry.kind === "action") return entry.action.tool;
  if (entry.kind === "proposal") return `제안 ${entry.proposal.tool}${proposalState(entry).suffix}`;
  if (entry.kind === "question") return questionTimelineLabel(entry);
  if (entry.kind === "finish") return STATUS_LABELS[entry.finish.status] || entry.finish.status;
  if (entry.kind === "wait") return waitLabel(entry.observation.wait);
  if (entry.kind === "legacy") return entry.step.tool;
  return "관찰";
}

const WAIT_LABEL_CHARS = 18;
const SOURCE_LABELS = { observe: "observe 관찰", action: "동작 뒤 화면", launch: "실행 직후", wait_for_text: "wait_for_text 대기", question: "질문 직전 화면" };

function waitLabel(wait) {
  if (!wait) return "대기";
  const text = String(wait.text || "");
  const shown = text.length > WAIT_LABEL_CHARS ? `${text.slice(0, WAIT_LABEL_CHARS)}…` : text;
  return `대기 "${shown}" · ${wait.found ? "찾음" : "못 찾음"}`;
}

function formatWaited(ms) {
  if (typeof ms !== "number") return null;
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}초`;
}

function renderTimeline() {
  const list = $("timeline");
  list.replaceChildren(...state.entries.map((entry, index) => {
    const bad = (entry.kind === "finish" && entry.finish.status !== "verified")
      || (entry.kind === "proposal" && entry.decision && !entry.decision.approved)
      || (entry.kind === "wait" && !(entry.observation.wait && entry.observation.wait.found))
      || (entry.kind === "action" && entry.action.expected_met === false)
      || (entry.kind === "question" && questionState(entry).bad)
      || (entry.kind === "legacy" && entry.step.ok === false) ? " bad" : "";
    const current = index === state.index;
    const button = el("button", { attrs: { type: "button", ...(current ? { "aria-current": "step" } : {}) } }, [
      el("span", { className: "num", text: index + 1 }),
      el("span", { className: `dot ${entry.kind}${bad}`, attrs: { "aria-hidden": "true" } }),
      el("span", { text: timelineLabel(entry) }),
    ]);
    button.addEventListener("click", () => select(index));
    return el("li", {}, [button]);
  }));
  const current = list.children[state.index];
  if (current) current.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function renderToggles() {
  const entry = state.entries[state.index];
  const isAction = entry && entry.kind === "action";
  setPressed("show-before", state.view === "before");
  setPressed("show-after", state.view === "after");
  $("show-before").disabled = !isAction || !entry.before;
  $("show-after").disabled = !isAction || !entry.after;
  setPressed("show-all", state.boxes === "all");
  setPressed("show-target", state.boxes === "target");
  setPressed("show-texts", state.texts && state.boxes === "all");
  $("show-texts").disabled = state.boxes !== "all";
}

function setPressed(id, pressed) {
  $(id).setAttribute("aria-pressed", String(pressed));
}

function describeTarget(target) {
  if (!target) return "없음";
  if (target.ref) return `${target.role || ""} "${target.name || ""}" ${target.ref}`.trim();
  const points = (target.points || []).map((p) => `(${p[0]}, ${p[1]})`).join(" → ");
  return points ? `${target.window_id || "창"} ${points}` : `${target.window_id || "창"} (좌표 없음)`;
}

function factList(rows) {
  const dl = el("dl", { className: "facts" });
  for (const [term, value, className] of rows) {
    if (value === undefined || value === null || value === "") continue;
    dl.append(el("dt", { text: term }));
    const dd = el("dd", { className: className || "" });
    if (value instanceof Node) dd.append(value); else dd.textContent = String(value);
    dl.append(dd);
  }
  return dl;
}

function renderDetails() {
  const info = $("step-info");
  const entry = state.entries[state.index];
  if (!entry) {
    info.replaceChildren(el("p", { className: "empty", text: state.runId ? "이 실행에는 기록된 단계가 없습니다." : "" }));
    renderObservationText(null);
    return;
  }
  if (entry.kind === "action") info.replaceChildren(...actionDetails(entry));
  else if (entry.kind === "proposal") info.replaceChildren(...proposalDetails(entry));
  else if (entry.kind === "question") info.replaceChildren(...questionDetails(entry));
  else if (entry.kind === "finish") info.replaceChildren(...finishDetails(entry.finish));
  else if (entry.kind === "wait") info.replaceChildren(...waitDetails(entry.observation));
  else if (entry.kind === "legacy") info.replaceChildren(...legacyDetails(entry.step));
  else info.replaceChildren(...observationDetails(entry.observation));
  renderObservationText(entry.kind === "legacy" ? legacyObservation(entry.step) : shownObservation(entry));
}

function actionDetails(entry) {
  const { action } = entry;
  const result = action.result || {};
  const disposition = result.disposition
    ? [result.disposition.delivery, result.disposition.retry && `재시도 ${result.disposition.retry}`].filter(Boolean).join(" · ")
    : null;
  const error = result.code ? `${result.code}: ${result.message || ""}` : result.message;
  const args = { ...(action.arguments || {}) };
  delete args.intent;
  const nodes = [
    el("div", { className: "step-head" }, [el("h2", { text: `#${state.index + 1} ${action.tool}` }), el("span", { className: "kind", text: "동작" })]),
    action.intent ? el("p", { className: "intent", text: action.intent }) : null,
    factList([
      ["모델 스텝", action.step],
      ["승인", entry.approval ? `승인함${entry.approval.comment ? ` · ${entry.approval.comment}` : ""}` : null, "ok-text"],
      ["대상", describeTarget(action.target)],
      ["결과", result.ok ? "성공" : "실패", result.ok ? "ok-text" : "bad-text"],
      ["전달", disposition],
      ["오류", error, "bad-text"],
      ["거부", result.refused, "bad-text"],
      ...expectationRows(action),
      ["모델 토큰", state.tokensByStep.get(action.step)],
      ["보는 화면", state.view === "after" ? "동작 후" : "동작 전"],
    ]),
    ...knowledgeNotes(entry),
    el("h3", { text: "인자" }),
    el("pre", { text: JSON.stringify(args, null, 2) }),
  ];
  if (result.post_state) nodes.push(el("h3", { text: "동작 뒤 상태" }), el("pre", { text: JSON.stringify(result.post_state, null, 2) }));
  nodes.push(...changesSection(entry));
  return nodes.filter(Boolean);
}

function proposalState(entry) {
  if (!entry.decision) {
    return isLive() ? { text: "승인 대기 중", className: "warn-text", suffix: " · 대기" } : { text: "답 없음", className: "", suffix: "" };
  }
  if (entry.decision.approved) return { text: "승인함", className: "ok-text", suffix: " · 승인" };
  return { text: "거부함", className: "bad-text", suffix: " · 거부" };
}

function proposalDetails(entry) {
  const { proposal, decision } = entry;
  const stateInfo = proposalState(entry);
  const args = { ...(proposal.arguments || {}) };
  delete args.intent;
  const nodes = [
    el("div", { className: "step-head" }, [el("h2", { text: `#${state.index + 1} ${proposal.tool}` }), el("span", { className: "kind proposal-kind", text: "제안" })]),
    proposal.intent ? el("p", { className: "intent proposal-intent", text: proposal.intent }) : null,
    factList([
      ["상태", stateInfo.text, stateInfo.className],
      ["의견", decision && decision.comment],
      ["모델 스텝", proposal.step],
      ["대상", describeTarget(proposal.target)],
    ]),
  ];
  if (!decision && isLive()) nodes.push(approvalPanel(proposal));
  nodes.push(el("h3", { text: "인자" }), el("pre", { text: JSON.stringify(args, null, 2) }));
  return nodes.filter(Boolean);
}

function approvalPanel(proposal) {
  const comment = el("textarea", { attrs: { id: "decision-comment", rows: "2", maxlength: "2000", placeholder: "의견 (선택, 거부할 때 이유를 적으면 모델이 읽습니다)" } });
  comment.value = state.drafts.get(proposal.id) || "";
  comment.addEventListener("input", () => state.drafts.set(proposal.id, comment.value));
  const message = el("p", { className: "decision-message", attrs: { role: "status", "aria-live": "polite" } });
  const approve = el("button", { className: "approve", text: "승인", attrs: { type: "button" } });
  const reject = el("button", { className: "reject", text: "거부", attrs: { type: "button" } });
  const send = async (approved) => {
    approve.disabled = true;
    reject.disabled = true;
    message.textContent = "보내는 중…";
    const outcome = await sendDecision(proposal.id, approved, comment.value.trim());
    message.textContent = outcome.message;
    message.dataset.kind = outcome.ok ? "" : "error";
    if (outcome.ok) state.drafts.delete(proposal.id);
    else {
      approve.disabled = false;
      reject.disabled = false;
    }
  };
  approve.addEventListener("click", () => send(true));
  reject.addEventListener("click", () => send(false));
  return el("div", { className: "approval", attrs: { role: "group", "aria-label": "동작 승인" } }, [
    el("p", { className: "approval-title", text: "이 동작을 앱에 보낼까요?" }),
    el("label", { className: "field" }, [el("span", { text: "의견" }), comment]),
    el("div", { className: "approval-buttons" }, [approve, reject]),
    message,
  ]);
}

async function sendDecision(proposalId, approved, comment) {
  let response;
  try {
    response = await fetch(`/api/runs/${encodeURIComponent(state.runId)}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Agent-Token": state.token || "" },
      body: JSON.stringify({ proposal_id: proposalId, approved, comment }),
      cache: "no-store",
    });
  } catch (error) {
    return { ok: false, message: `서버에 연결하지 못했습니다: ${error.message}` };
  }
  const body = await response.json().catch(() => ({}));
  if (response.ok) return { ok: true, message: approved ? "승인했습니다. 실행이 이어집니다." : "거부했습니다. 모델에 이유를 전합니다." };
  return { ok: false, message: body.error || `보내지 못했습니다 (${response.status})` };
}

function formatArguments(args) {
  if (args === null || args === undefined) return "";
  if (typeof args !== "object") return String(args);
  const pairs = Object.entries(args).map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`);
  return pairs.length ? pairs.join(", ") : "(없음)";
}

function legacyObservation(step) {
  return step.observation ? { text: step.observation } : null;
}

function legacyDetails(step) {
  const failed = step.ok === false;
  return [
    el("div", { className: "step-head" }, [
      el("h2", { text: `#${state.index + 1} ${step.tool}` }),
      el("span", { className: "kind legacy-kind", text: "옛 로그" }),
    ]),
    factList([
      ["로그 단계", step.step],
      ["도구", step.tool],
      ["인자", formatArguments(step.arguments)],
      ["결과", step.summary, failed ? "warn-text" : ""],
    ]),
    el("p", { className: "hint", text: "옛 로그에는 스크린샷과 요소 박스가 없습니다." }),
  ];
}

function finishDetails(finish) {
  return [
    el("div", { className: "step-head" }, [el("h2", { text: "종료" }), el("span", { className: "kind", text: "판정" })]),
    factList([
      ["판정", statusBadge(finish.status)],
      ["이유", finish.reason],
      ["단계 수", finish.steps],
      ["토큰", finish.tokens],
    ]),
  ];
}

function observationFacts(observation) {
  const elements = observation.elements || [];
  const drawn = elements.filter((item) => item.rect).length;
  const texts = elements.filter((item) => elementKind(item) === "text").length;
  const image = observation.image ? `${observation.image.width} × ${observation.image.height}` : "없음";
  return [
    ["창", observation.window && observation.window.title],
    ["요소", `컨트롤 ${elements.length - texts}개 · 텍스트 ${texts}개${observation.text_truncated ? " (잘림)" : ""} · 박스 있음 ${drawn}개`],
    ["스크린샷", image],
  ];
}

function observationDetails(observation) {
  const source = observation.source ? SOURCE_LABELS[observation.source] || observation.source : null;
  return [
    el("div", { className: "step-head" }, [el("h2", { text: `관찰 ${observation.id}` }), el("span", { className: "kind", text: "관찰" })]),
    factList([["관찰 출처", source], ...observationFacts(observation)]),
  ];
}

function waitDetails(observation) {
  const wait = observation.wait || {};
  const found = Boolean(wait.found);
  return [
    el("div", { className: "step-head" }, [el("h2", { text: `#${state.index + 1} 대기` }), el("span", { className: "kind wait-kind", text: "wait_for_text" })]),
    el("p", { className: found ? "wait-result found" : "wait-result missed", text: found ? `"${wait.text || ""}"을(를) 찾았습니다` : `"${wait.text || ""}"을(를) 찾지 못했습니다` }),
    factList([
      ["기다린 글자", wait.text],
      ["결과", found ? "찾음" : "못 찾음", found ? "ok-text" : "warn-text"],
      ["폴링 횟수", typeof wait.polls === "number" ? `${wait.polls}번` : null],
      ["기다린 시간", formatWaited(wait.waited_ms)],
      ...observationFacts(observation),
    ]),
  ];
}

function renderObservationText(observation) {
  const box = $("observation-box");
  box.hidden = !observation || !observation.text;
  $("observation-text").textContent = observation && observation.text ? observation.text : "";
}

function observationWarnings(observation) {
  if (!observation) return [];
  const messages = [];
  if (observation.geometry_stale) messages.push("스크린샷과 요소 위치를 읽는 사이에 창이 움직였습니다. 박스가 어긋날 수 있습니다.");
  if (!observation.frame) messages.push(`스크린샷이 없습니다${observation.warning ? `: ${observation.warning}` : "."}`);
  else if (observation.warning) messages.push(observation.warning);
  return messages;
}

function stageNotices(entry, observation) {
  const notices = runNotices(state.run).concat(observationWarnings(observation));
  const pending = pendingProposal();
  if (pending && pending !== entry) notices.unshift("동작 승인을 기다리고 있습니다. 타임라인의 노란 '대기' 단계에서 승인하거나 거부하세요.");
  const question = pendingQuestion();
  if (question && !canAnswerHere()) notices.unshift("에이전트가 질문하고 답을 기다립니다. 이 실행은 터미널에서 답합니다.");
  else if (question && question !== entry) notices.unshift("에이전트가 질문하고 답을 기다립니다. 타임라인의 질문 단계를 보면서 오른쪽 대화 칸에서 답하세요.");
  return notices;
}

function emptyStageText(entry, observation) {
  if (!state.runId) return "왼쪽에서 실행을 고르세요.";
  if (!entry) return "이 실행에는 기록된 단계가 없습니다.";
  if (entry.kind === "legacy") return "옛 로그에는 스크린샷이 없습니다. 오른쪽에서 도구·인자·결과를 보세요.";
  if (observation) return "표시할 스크린샷이 없습니다.";
  if (entry.kind === "action") return state.view === "after" ? "이 동작 뒤에 기록된 관찰이 없습니다." : "이 동작 전에는 기록된 관찰이 없습니다.";
  return "이 단계에는 연결된 관찰이 없습니다.";
}

async function renderStage() {
  const seq = ++state.renderSeq;
  const entry = state.entries[state.index];
  const observation = shownObservation(entry);
  showBanner(stageNotices(entry, observation));
  const stage = $("stage");
  const empty = $("stage-empty");
  hideTooltip();
  if (!observation || !observation.frame) {
    stage.hidden = true;
    empty.hidden = false;
    empty.textContent = emptyStageText(entry, observation);
    $("frame-label").textContent = "";
    renderTruncatedNote(observation);
    return;
  }
  let url;
  try {
    url = await frameUrl(state.runId, observation.frame);
  } catch (error) {
    if (seq === state.renderSeq) showBanner([error.message]);
    return;
  }
  if (seq !== state.renderSeq) return;
  const img = $("frame");
  img.alt = `관찰 ${observation.id} 스크린샷`;
  if (img.src !== url) {
    img.src = url;
    await img.decode().catch(() => {});
    if (seq !== state.renderSeq) return;
  }
  const width = (observation.image && observation.image.width) || img.naturalWidth;
  const height = (observation.image && observation.image.height) || img.naturalHeight;
  stage.hidden = false;
  empty.hidden = true;
  $("frame-label").textContent = `관찰 ${observation.id} · ${observation.frame} · ${width}×${height}`;
  renderTruncatedNote(observation);
  drawOverlay(entry, observation, width, height);
}

function renderTruncatedNote(observation) {
  const note = $("truncated-note");
  if (!observation || !observation.text_truncated) {
    note.hidden = true;
    note.textContent = "";
    return;
  }
  const count = (observation.elements || []).filter((item) => elementKind(item) === "text").length;
  note.textContent = `텍스트 요소는 앞의 ${count}개까지만 기록됨`;
  note.hidden = false;
}

function clearOverlay() {
  const overlay = $("overlay");
  for (const child of [...overlay.children]) if (child.tagName.toLowerCase() !== "defs") child.remove();
  $("labels").replaceChildren();
}

const SHAPE_STYLES = {
  text: { className: "text-box", halo: 2.5, tone: "" },
  control: { className: "element", halo: 3.5, tone: "" },
  changed: { className: "changed", halo: 4, tone: "" },
  gone: { className: "gone", halo: 4, tone: "" },
  target: { className: "target", halo: 6, tone: "" },
  proposal: { className: "proposal", halo: 6, tone: "proposal" },
  qbasis: { className: "q-basis", halo: 0, tone: "" },
  qcandidate: { className: "q-candidate", halo: 5, tone: "q-candidate" },
  qtarget: { className: "q-target", halo: 6, tone: "q-target" },
  pin: { className: "pin", halo: 0, tone: "pin" },
};
const overlayItems = new WeakMap();

function elementKind(item) {
  return item && item.kind === "text" ? "text" : "control";
}

function overlayPlan(entry, observation, options) {
  const shapes = [];
  if (options.boxes === "all") {
    const elements = (observation && observation.elements) || [];
    if (options.texts) {
      for (const item of elements) if (item.rect && elementKind(item) === "text") shapes.push({ layer: "text", rect: item.rect, item });
    }
    for (const item of elements) if (item.rect && elementKind(item) === "control") shapes.push({ layer: "control", rect: item.rect, item });
  }
  const changes = options.changes || { highlight: [], gone: [] };
  for (const item of changes.highlight) shapes.push({ layer: "changed", rect: item.rect, item });
  for (const item of changes.gone) shapes.push({ layer: "gone", rect: item.rect, item });
  const mark = (target, layer, label, ghost) => {
    if (!target) return;
    if (target.rect) {
      shapes.push({ layer, rect: target.rect, item: target, label, ghost });
      return;
    }
    const points = (target.image_points || []).filter(Boolean);
    if (points.length > 0) shapes.push({ layer, points, item: target, label, ghost });
  };
  if (entry && entry.kind === "action") mark(entry.action.target, "target", `${options.index + 1} ${entry.action.tool}`, options.view === "after");
  if (entry && entry.kind === "proposal") mark(entry.proposal.target, "proposal", `${options.index + 1} 제안 ${entry.proposal.tool}`, false);
  shapes.push(...questionShapes(entry, observation));
  return shapes;
}

function drawOverlay(entry, observation, width, height) {
  clearOverlay();
  const overlay = $("overlay");
  overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
  overlay.setAttribute("preserveAspectRatio", "none");
  const plan = overlayPlan(entry, observation, {
    boxes: state.boxes, texts: state.texts, view: state.view, index: state.index, changes: changeMarks(entry, observation),
  });
  const layer = document.createDocumentFragment();
  for (const shape of plan) {
    if (shape.rect) appendRect(layer, shape);
    else if (shape.cross) appendCross(layer, shape);
    else appendPoints(layer, shape);
  }
  overlay.append(layer);
  $("stage").classList.toggle("pinnable", pinnable());
  for (const shape of plan) {
    if (!shape.label) continue;
    const [x, y] = shape.rect ? [shape.rect.x, shape.rect.y] : shape.cross || shape.points[0];
    const placement = shape.layer === "pin" ? "left" : shape.cross ? "beside" : shape.layer.startsWith("q") ? "inside" : "above";
    addLabel(shape.label, x, y, width, height, SHAPE_STYLES[shape.layer].tone, placement);
  }
}

function appendRect(parent, shape) {
  const style = SHAPE_STYLES[shape.layer];
  const box = { x: shape.rect.x, y: shape.rect.y, width: shape.rect.width, height: shape.rect.height };
  parent.append(svg("rect", { ...box, class: "halo", "stroke-width": style.halo }));
  const node = svg("rect", { ...box, class: shape.ghost ? `${style.className} ghost` : style.className });
  overlayItems.set(node, shape.item);
  parent.append(node);
}

function appendPoints(parent, shape) {
  const tone = SHAPE_STYLES[shape.layer].tone;
  const suffix = tone ? ` ${tone}` : "";
  const marker = tone ? "url(#arrow-proposal)" : "url(#arrow)";
  const { points } = shape;
  for (let i = 1; i < points.length; i += 1) {
    const [x1, y1] = points[i - 1];
    const [x2, y2] = points[i];
    parent.append(svg("line", { x1, y1, x2, y2, class: "halo", "stroke-width": 6 }));
    parent.append(svg("line", { x1, y1, x2, y2, class: `path${suffix}`, "marker-end": marker }));
  }
  for (const [cx, cy] of points) {
    parent.append(svg("circle", { cx, cy, r: 7, class: `point${suffix}` }));
  }
}

function onOverlayOver(event) {
  const item = overlayItems.get(event.target);
  if (item) showTooltip(item, event);
}

function onOverlayMove(event) {
  if (overlayItems.has(event.target) && !$("tooltip").hidden) moveTooltip(event);
}

function onOverlayOut(event) {
  if (overlayItems.has(event.target)) hideTooltip();
}

function addLabel(text, x, y, width, height, tone, placement = "above") {
  const label = el("span", { className: tone ? `label ${tone}` : "label", text });
  label.dataset.placement = placement;
  label.dataset.x = String(x / width);
  label.dataset.y = String(y / height);
  $("labels").append(label);
  positionLabels();
}

function positionLabels() {
  const stage = $("stage");
  const stageWidth = stage.clientWidth;
  const stageHeight = stage.clientHeight;
  if (!stageWidth || !stageHeight) return;
  const gap = 3;
  for (const label of $("labels").children) {
    const anchorX = Number(label.dataset.x) * stageWidth;
    const anchorY = Number(label.dataset.y) * stageHeight;
    const width = label.offsetWidth;
    const height = label.offsetHeight;
    const placement = label.dataset.placement;
    const shiftX = placement === "beside" ? 10 : placement === "left" ? -width - 10 : placement === "inside" ? 1 : -2;
    const left = Math.max(0, Math.min(anchorX + shiftX, stageWidth - width));
    const above = anchorY - height - gap;
    let top = above >= 0 ? above : Math.min(anchorY + gap, Math.max(0, stageHeight - height));
    if (placement === "inside") top = Math.max(0, Math.min(anchorY + 1, stageHeight - height));
    if (placement === "beside" || placement === "left") top = Math.max(0, Math.min(anchorY - height / 2, stageHeight - height));
    label.style.left = `${left}px`;
    label.style.top = `${top}px`;
  }
}

async function loadStatus() {
  try {
    const response = await api("/api/status");
    state.status = await response.json();
  } catch (error) {
    showBanner([error.message]);
    return;
  }
  renderStartForm();
  if (state.runId) {
    renderChat();
    renderToggles();
    if (!$("stage").hidden) $("stage").classList.toggle("pinnable", pinnable());
  }
}

function renderStartForm() {
  const status = state.status;
  const form = $("start-form");
  form.hidden = !status || !status.can_start;
  if (form.hidden) return;
  $("exe").placeholder = status.default_exe || "실행 파일의 절대 경로";
  $("exe").required = !status.default_exe;
  const busy = Boolean(status.active);
  $("start-run").disabled = busy;
  if (busy) $("start-status").textContent = `실행 중: ${status.active}`;
  else if ($("start-status").dataset.kind !== "error") $("start-status").textContent = "";
}

function setStartMessage(text, kind) {
  const node = $("start-status");
  node.textContent = text;
  node.dataset.kind = kind || "";
}

async function submitRun(event) {
  event.preventDefault();
  const goal = $("goal").value.trim();
  if (!goal) {
    setStartMessage("지시를 입력해 주세요.", "error");
    $("goal").focus();
    return;
  }
  const payload = { goal, level: Number($("level").value), approve: $("approve").checked };
  const exe = $("exe").value.trim();
  if (exe) payload.exe = exe;
  const system = $("system").value.trim();
  if (system) payload.system = system;
  $("start-run").disabled = true;
  setStartMessage("시작하는 중…");
  let response;
  try {
    response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Agent-Token": state.token || "" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
  } catch (error) {
    setStartMessage(`서버에 연결하지 못했습니다: ${error.message}`, "error");
    $("start-run").disabled = false;
    return;
  }
  const body = await response.json().catch(() => ({}));
  if (response.status !== 201) {
    setStartMessage(body.error || `시작하지 못했습니다 (${response.status})`, "error");
    await loadStatus();
    return;
  }
  setStartMessage("");
  $("goal").value = "";
  await loadStatus();
  try {
    await fetchRuns();
  } catch (error) {
    showBanner([error.message]);
    return;
  }
  await selectRun(body.run_id, "run");
}

function fillLevels() {
  const select = $("level");
  select.replaceChildren(...LEVEL_LABELS.map((label, level) => el("option", { text: label, attrs: { value: String(level) } })));
  select.value = "3";
}

function showTooltip(item, event) {
  const tooltip = $("tooltip");
  const dl = el("dl");
  const kind = item.kind === "question" ? "질문 표시" : elementKind(item) === "text" ? "텍스트 (ref 없음)" : "컨트롤";
  for (const [term, value] of [["종류", kind], ["역할", item.role], ["이름", item.name], ["설명", item.description], ["ref", item.ref]]) {
    if (!value) continue;
    dl.append(el("dt", { text: term }), el("dd", { text: value }));
  }
  tooltip.replaceChildren(dl);
  tooltip.hidden = false;
  moveTooltip(event);
}

function moveTooltip(event) {
  const tooltip = $("tooltip");
  const margin = 14;
  const x = Math.min(event.clientX + margin, window.innerWidth - tooltip.offsetWidth - 4);
  const y = Math.min(event.clientY + margin, window.innerHeight - tooltip.offsetHeight - 4);
  tooltip.style.left = `${Math.max(4, x)}px`;
  tooltip.style.top = `${Math.max(4, y)}px`;
}

function hideTooltip() {
  $("tooltip").hidden = true;
}

function setView(view) {
  const entry = state.entries[state.index];
  if (!entry || entry.kind !== "action") return;
  if (view === "after" && !entry.after) return;
  if (view === "before" && !entry.before) return;
  state.view = view;
  render();
}

function setBoxes(boxes) {
  state.boxes = boxes;
  render();
}

function toggleTexts() {
  if (state.boxes !== "all") return;
  state.texts = !state.texts;
  render();
}

function onKey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (document.body.classList.contains("knowledge-mode")) return;
  const tag = (event.target.tagName || "").toLowerCase();
  if (["input", "textarea", "select"].includes(tag) || event.target.isContentEditable) return;
  if (event.key === "ArrowLeft") select(state.index - 1);
  else if (event.key === "ArrowRight") select(state.index + 1);
  else if (event.key === "b" || event.key === "B") setView(state.view === "before" ? "after" : "before");
  else if (event.key === "a" || event.key === "A") setBoxes(state.boxes === "all" ? "target" : "all");
  else if (event.key === "t" || event.key === "T") toggleTexts();
  else return;
  event.preventDefault();
}

function fillStatusFilter() {
  const select = $("run-status");
  select.replaceChildren(
    el("option", { text: "모든 상태", attrs: { value: "" } }),
    el("option", { text: "★ 보관", attrs: { value: KEEP_FILTER } }),
    ...FILTER_STATUSES.map((status) => el("option", { text: STATUS_LABELS[status] || status, attrs: { value: status } })),
  );
}

function watchFilters() {
  let timer = null;
  $("run-search").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      state.filters.q = $("run-search").value.trim();
      reloadRuns();
    }, SEARCH_DELAY_MS);
  });
  $("run-status").addEventListener("change", () => {
    const value = $("run-status").value;
    state.filters.keep = value === KEEP_FILTER;
    state.filters.status = value === KEEP_FILTER ? "" : value;
    reloadRuns();
  });
  $("run-since").addEventListener("change", () => {
    state.filters.since = $("run-since").value;
    reloadRuns();
  });
  $("run-more").addEventListener("click", loadMoreRuns);
}

function readExport() {
  const node = document.getElementById("run-export");
  if (!node) return null;
  try {
    const data = JSON.parse(node.textContent);
    return data && typeof data === "object" && data.run && typeof data.run === "object" ? data : { invalid: true };
  } catch (_) {
    return { invalid: true };
  }
}

function exportedSummary(run) {
  const started = Date.parse(run.started_at);
  const finished = Date.parse(run.finished_at);
  const exe = typeof run.exe === "string" ? run.exe : null;
  const format = run.format === undefined ? 1 : run.format;
  const count = (value) => (Number.isInteger(value) ? value : null);
  return {
    run_id: typeof run.run_id === "string" ? run.run_id : "",
    kind: "run",
    status: typeof run.status === "string" ? run.status : "unreadable",
    goal: typeof run.goal === "string" ? run.goal : null,
    reason: typeof run.reason === "string" ? run.reason : null,
    exe,
    exe_name: exe ? exe.split(/[\\/]/).pop() : null,
    started_by: typeof run.started_by === "string" ? run.started_by : null,
    started_at: typeof run.started_at === "string" ? run.started_at : null,
    finished_at: typeof run.finished_at === "string" ? run.finished_at : null,
    duration_s: Number.isNaN(started) || Number.isNaN(finished) ? null : Math.max(0, Math.round((finished - started) / 1000)),
    steps: count(run.steps),
    tokens: count(run.tokens),
    format: Number.isInteger(format) ? format : null,
    newer_format: !(Number.isInteger(format) && format <= 2),
    keep: false,
  };
}

function exportNotices(data) {
  const lines = [EXPORT_WARNING];
  if (data.exported_at) lines.push(`내보낸 시각: ${formatTime(data.exported_at)} · 서버 없이 이 실행 하나만 봅니다.`);
  if (!(Number.isInteger(data.export) && data.export <= KNOWN_EXPORT)) lines.push(`새 내보내기 형식(export ${data.export})입니다. 이 뷰어가 아는 부분만 표시합니다.`);
  return lines;
}

function startExport(data) {
  state.exportMode = true;
  document.body.classList.add("export-mode");
  const warning = $("export-warning");
  warning.hidden = false;
  if (data.invalid) {
    warning.replaceChildren(el("div", { text: "내보낸 기록을 읽을 수 없습니다. 파일이 잘렸거나 바뀌었을 수 있습니다." }));
    return;
  }
  warning.replaceChildren(...exportNotices(data).map((line) => el("div", { text: line })));
  state.exported = data;
  state.run = exportedSummary(data.run);
  state.runId = state.run.run_id;
  state.runKind = "run";
  state.events = Array.isArray(data.events) ? data.events : [];
  state.next = state.events.length;
  state.finished = true;
  document.title = `${state.run.goal || state.run.run_id} · 내보낸 실행`;
  state.baseTitle = document.title;
  rebuildEntries();
  state.view = "before";
  select(0);
}

function start() {
  const exported = readExport();
  if (!exported) state.token = readToken();
  fillStatusFilter();
  watchFilters();
  $("show-before").addEventListener("click", () => setView("before"));
  $("show-after").addEventListener("click", () => setView("after"));
  $("show-all").addEventListener("click", () => setBoxes("all"));
  $("show-target").addEventListener("click", () => setBoxes("target"));
  $("show-texts").addEventListener("click", toggleTexts);
  $("overlay").addEventListener("pointerover", onOverlayOver);
  $("overlay").addEventListener("pointermove", onOverlayMove);
  $("overlay").addEventListener("pointerout", onOverlayOut);
  $("stage").addEventListener("click", onStageClick);
  state.baseTitle = document.title;
  $("refresh-runs").addEventListener("click", () => { loadRuns(); loadStatus(); });
  $("start-form").addEventListener("submit", submitRun);
  document.addEventListener("keydown", onKey);
  new ResizeObserver(positionLabels).observe($("stage"));
  fillLevels();
  if (exported) {
    startExport(exported);
    return;
  }
  if (!state.token) {
    showBanner(["접속 토큰이 없습니다. view.py가 터미널에 출력한 주소로 여세요."]);
    return;
  }
  initKnowledge();
  loadStatus();
  loadRuns();
  setInterval(pollLive, POLL_MS);
}

start();
