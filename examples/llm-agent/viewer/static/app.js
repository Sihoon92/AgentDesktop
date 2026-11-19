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
const STARTED_BY_LABELS = { "run.py": "run.py", "run.py --view": "run.py --view", viewer: "뷰어", baseline: "baseline", workflow: "workflow 재실행" };
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
  revealedQuestion: null,
  focusedQuestion: null,
  baseTitle: "",
  summaryLines: null,
  collapsed: { runs: true, chat: false, details: false, settings: true },
  stageFocus: null,
  stageDetailOpen: false,
  stageCache: null,
  panelPrefs: {},
  stopRequested: null,
  pendingConfirm: null,
  closing: null,
  timers: {},
  statusPolling: false,
  closePolling: false,
};
const PANEL_KEY = "agent-viewer-panels";
const FOLDABLE = ["runs", "chat", "details", "settings"];
const PANEL_NAMES = { runs: "실행 목록", chat: "대화", details: "스텝 정보" };
const POLL_MS = 1000;
const MANUAL_CHOICE = "*manual*";
const READY_CODE = /^[a-z_]{1,40}$/;
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
  if (state.closing) return;
  const banner = $("banner");
  const lines = messages.filter(Boolean);
  banner.replaceChildren(...lines.map((line) => el("div", { text: line })));
  banner.hidden = lines.length === 0;
}

/* ---------- folding panels ---------- */

function readPanelPrefs() {
  let data = null;
  try {
    data = JSON.parse(localStorage.getItem(PANEL_KEY) || "null");
  } catch (_) {
    data = null;
  }
  const prefs = {};
  if (data && typeof data === "object") {
    for (const name of FOLDABLE) if (typeof data[name] === "boolean") prefs[name] = data[name];
  }
  return prefs;
}

function writePanelPrefs() {
  try {
    localStorage.setItem(PANEL_KEY, JSON.stringify(state.panelPrefs));
  } catch (_) { /* storage may be blocked; the panels still fold on this page */ }
}

function setCollapsed(name, collapsed, remember = false) {
  state.collapsed[name] = collapsed;
  if (remember) {
    state.panelPrefs[name] = collapsed;
    writePanelPrefs();
  }
  applyCollapsed(name);
}

function applyCollapsed(name) {
  const collapsed = state.collapsed[name];
  if (name === "settings") {
    $("start-settings").hidden = collapsed;
    $("toggle-settings").setAttribute("aria-expanded", String(!collapsed));
    return;
  }
  const button = $(`toggle-${name}`);
  $(button.getAttribute("aria-controls")).hidden = collapsed;
  button.closest(".panel").classList.toggle("collapsed", collapsed);
  $("layout").classList.toggle(`${name}-collapsed`, collapsed);
  button.setAttribute("aria-expanded", String(!collapsed));
  button.title = `${PANEL_NAMES[name]} ${collapsed ? "펴기" : "접기"}`;
  button.querySelector(".visually-hidden").textContent = button.title;
  if (name === "chat") {
    renderPendingBadge();
    if (!collapsed) $("chat-log").scrollTop = $("chat-log").scrollHeight;
  }
}

function initPanels() {
  state.panelPrefs = readPanelPrefs();
  for (const name of FOLDABLE) {
    if (state.panelPrefs[name] !== undefined) state.collapsed[name] = state.panelPrefs[name];
    applyCollapsed(name);
    $(`toggle-${name}`).addEventListener("click", () => setCollapsed(name, !state.collapsed[name], true));
  }
}

function openRunsWhenNothingChosen() {
  if (state.exportMode || state.panelPrefs.runs !== undefined) return;
  setCollapsed("runs", false);
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
  for (let at = 0; at < events.length; at += 1) {
    const event = events[at];
    if (event.type === "proposal") {
      const entry = { kind: "proposal", proposal: event, decision: null, before: lastObservation, absorbed: false, at };
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
      const entry = { kind: "question", question: event, answer: null, observation, at };
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
        entries.push({ kind: "wait", observation: event, at });
      } else if (awaitingAfter && showsActionResult(event)) {
        awaitingAfter.after = event;
        awaitingAfter = null;
      } else if (!beforeIds.has(event.id) && event.source !== "question") {
        entries.push({ kind: "observation", observation: event, at });
      }
    } else if (event.type === "action") {
      awaitingAfter = {
        kind: "action", action: event, before: observations.get(event.before) || null, after: null, next: null, at,
        knowledgeUsed: knowledgeUsed.get(event.step) || [], knowledgeFailed: knowledgeFailed.get(event.step) || [],
      };
      awaitingNext = awaitingAfter;
      if (approved && approved.proposal.tool === event.tool) {
        awaitingAfter.approval = approved.decision;
        approved.absorbed = true;
      }
      approved = null;
      entries.push(awaitingAfter);
    } else if (event.type === "capture" && event.reason === "look") {
      entries.push({ kind: "look", capture: event, observation: observations.get(event.observation) || lastObservation, at });
    } else if (event.type === "finish") {
      entries.push({ kind: "finish", finish: event, observation: lastObservation, at });
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
  const visionReads = events.filter((event) => event.type === "vision").length;
  const parts = [
    formatDuration(run.duration_s),
    typeof steps === "number" ? `스텝 ${steps}` : null,
    typeof tokens === "number" ? `토큰 ${tokens.toLocaleString("en-US")}` : null,
    visionReads ? `비전 ${visionReads}회` : null,
    approvals ? `승인 ${approvals.approved} · 거부 ${approvals.rejected}${approvals.unanswered ? ` · 답 없음 ${approvals.unanswered}` : ""}` : null,
    run.started_by ? `시작 ${STARTED_BY_LABELS[run.started_by] || run.started_by}` : null,
    workflowSummaryPart(run, events),
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
  if (run.kind === "legacy") return ["옛 로그입니다. 스크린샷 없이 스텝마다 도구·인자·결과만 보입니다."];
  const notices = [];
  if (run.status === "interrupted") {
    notices.push(`이 실행은 중단됐습니다${run.interrupted_reason ? ` (${run.interrupted_reason})` : ""}. 기록이 여기서 끊겼습니다.`);
  }
  if (state.exportMode && run.status === "running") notices.push("내보낼 때 실행 중이던 기록입니다. 그 뒤의 스텝은 들어 있지 않습니다.");
  if (run.status === "unreadable") notices.push("run.json을 읽을 수 없습니다(깨졌거나 쓰는 중). 기록된 스텝만 보입니다.");
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
  openRunsWhenNothingChosen();
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
  const changed = runId !== state.runId || kind !== state.runKind;
  if (changed) {
    releaseFrames();
    state.pin = null;
    state.chatShown = null;
    state.composerKey = null;
    state.summaryLines = null;
    state.stageFocus = null;
    state.stageCache = null;
    $("copy-status").textContent = "";
  }
  state.runId = runId;
  state.runKind = kind;
  renderSummaryBox();
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
  loadRunSummaryText(runId);
}

async function loadRunSummaryText(runId) {
  if (state.exportMode || state.runKind !== "run" || !runId) return;
  let lines = null;
  try {
    const data = await (await api(`/api/runs/${encodeURIComponent(runId)}/summary`)).json();
    lines = Array.isArray(data.lines) ? data.lines.filter((line) => typeof line === "string") : null;
  } catch (_) {
    lines = null;
  }
  if (runId !== state.runId || state.runKind !== "run") return;
  state.summaryLines = lines;
  renderSummaryBox();
}

function renderSummaryBox() {
  const lines = !state.exportMode && state.runKind === "run" ? state.summaryLines : null;
  const box = $("summary-box");
  box.hidden = !lines || lines.length === 0;
  if (box.hidden) return;
  $("summary-text").textContent = lines.join("\n");
}

function toggleSummary() {
  const text = $("summary-text");
  text.hidden = !text.hidden;
  const button = $("toggle-summary");
  button.setAttribute("aria-expanded", String(!text.hidden));
  button.textContent = text.hidden ? "실행 요약 보기" : "실행 요약 접기";
}

async function copySummary() {
  const lines = state.summaryLines || [];
  if (lines.length === 0) return;
  const text = lines.join("\n");
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch (_) {
    copied = copyBySelection(text);
  }
  $("copy-status").textContent = copied ? "복사했습니다" : "복사하지 못했습니다. 요약을 펼쳐 직접 선택해 복사하세요";
}

function copyBySelection(text) {
  const area = el("textarea", { className: "offscreen-copy", attrs: { readonly: "", "aria-hidden": "true", tabindex: "-1" } });
  area.value = text;
  document.body.append(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_) {
    copied = false;
  }
  area.remove();
  return copied;
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
  await loadRunSummaryText(runId);
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
  if (state.closing || state.polling || !isLive()) return;
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
  state.stageFocus = null;
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

/* "대상 앱 준비 중 · N초" while the harness waits for the target's ready signal (a ready start
   event with no end yet in a live run), counted from the run's start because events carry no
   time; the run starts the wait before its first model call. A wait that ended without the
   signal shows its status as READY_<STATUS>. Text goes in through textContent only. */
function renderReadyBanner() {
  const banner = $("ready-banner");
  const text = readyBannerText();
  banner.textContent = text || "";
  banner.hidden = !text;
}

function readyBannerText() {
  if (state.runKind !== "run" || !state.runId) return null;
  const readies = (state.events || []).filter((event) => event.type === "ready");
  if (readies.length === 0) return null;
  const last = readies[readies.length - 1];
  if (last.phase === "end") {
    if (last.status === "ready" || typeof last.status !== "string" || !READY_CODE.test(last.status)) return null;
    return `대상 앱 준비 실패 · READY_${last.status.toUpperCase()}`;
  }
  if (last.phase !== "start" || !isLive()) return null;
  return `대상 앱 준비 중 · ${readySeconds()}초`;
}

function readySeconds() {
  const started = Date.parse((state.run && state.run.started_at) || "");
  if (!Number.isNaN(started)) return Math.max(0, Math.floor((Date.now() - started) / 1000));
  if (!state.readySeen || state.readySeen.runId !== state.runId) state.readySeen = { runId: state.runId, at: Date.now() };
  return Math.floor((Date.now() - state.readySeen.at) / 1000);
}

function render() {
  renderReadyBanner();
  renderRunSummary();
  renderTimeline();
  renderToggles();
  renderStageBar();
  renderDetails();
  renderStage();
  renderChat();
  updateTitle();
}

function timelineLabel(entry) {
  if (entry.kind === "action") return workflowTag(entry) + entry.action.tool;
  if (entry.kind === "proposal") return `제안 ${entry.proposal.tool}${proposalState(entry).suffix}`;
  if (entry.kind === "question") return questionTimelineLabel(entry);
  if (entry.kind === "finish") return STATUS_LABELS[entry.finish.status] || entry.finish.status;
  if (entry.kind === "wait") return waitLabel(entry.observation.wait);
  if (entry.kind === "legacy") return entry.step.tool;
  if (entry.kind === "look") return "look";
  return "관찰";
}

const WAIT_LABEL_CHARS = 18;
const SOURCE_LABELS = { observe: "observe 관찰", action: "동작 뒤 화면", launch: "실행 직후", wait_for_text: "wait_for_text 대기", question: "질문 직전 화면", ready: "준비된 창" };

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
      || (entry.kind === "action" && actionOutcome(entry.action).expected_met === false)
      || (entry.kind === "question" && questionState(entry).bad)
      || (entry.kind === "legacy" && entry.step.ok === false) ? " bad" : "";
    const current = index === state.index;
    const unconfirmed = entry.kind === "action" && isUnconfirmed(actionOutcome(entry.action));
    const button = el("button", { attrs: { type: "button", ...(current ? { "aria-current": "step" } : {}) } }, [
      el("span", { className: "num", text: index + 1 }),
      el("span", { className: `dot ${entry.kind}${bad}${unconfirmed ? " unconfirmed" : ""}`, attrs: { "aria-hidden": "true" } }),
      el("span", { text: timelineLabel(entry) }),
      visionBadge(entry),
      unconfirmed ? el("span", { className: "visually-hidden", text: " 확인 못 함" }) : null,
    ]);
    if (unconfirmed) button.title = "확인 못 함: 글은 그대로이고 누른 곳 주변 그림만 바뀜";
    button.addEventListener("click", () => select(index));
    const tag = state.runKind === "run" ? timelineStageTag(index) : null;
    return el("li", tag ? { className: "stage-start" } : {}, [tag, button]);
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
    info.replaceChildren(el("p", { className: "empty", text: state.runId ? "이 실행에는 기록된 스텝이 없습니다." : "" }));
    renderObservationText(null);
    return;
  }
  if (entry.kind === "action") info.replaceChildren(...actionDetails(entry));
  else if (entry.kind === "proposal") info.replaceChildren(...proposalDetails(entry));
  else if (entry.kind === "question") info.replaceChildren(...questionDetails(entry));
  else if (entry.kind === "finish") info.replaceChildren(...finishDetails(entry.finish));
  else if (entry.kind === "wait") info.replaceChildren(...waitDetails(entry.observation));
  else if (entry.kind === "legacy") info.replaceChildren(...legacyDetails(entry.step));
  else if (entry.kind === "look") info.replaceChildren(...lookDetails(entry));
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
    el("div", { className: "step-head" }, [
      el("h2", { text: `#${state.index + 1} ${action.tool}` }),
      el("span", { className: "kind", text: "동작" }),
      visionBadge(entry),
    ]),
    action.intent ? el("p", { className: "intent", text: action.intent }) : null,
    factList([
      ["모델 스텝", action.step],
      ["workflow 단계", workflowStepText(entry)],
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
    ...outcomeNotes(action),
    ...knowledgeNotes(entry),
    ...visionSection(entry),
    el("h3", { text: "인자" }),
    el("pre", { text: JSON.stringify(args, null, 2) }),
  ];
  if (result.post_state) nodes.push(el("h3", { text: "동작 뒤 상태" }), el("pre", { text: JSON.stringify(result.post_state, null, 2) }));
  nodes.push(...changesSection(entry));
  return nodes.filter(Boolean);
}

/* ---------- vision answers ---------- */

const VISION_ASKERS = { look: "look 질문", mismatch: "동작 뒤 불일치", unconfirmed: "동작 뒤 확인 못 함" };
const CAPTURE_ID = /^c\d+$/;

async function captureUrl(runId, captureId) {
  const name = `${captureId}.png`;
  if (state.exportMode) {
    const uri = (state.exported.captures || {})[name];
    if (typeof uri !== "string" || !uri.startsWith("data:image/png;base64,")) throw new Error(`내보낸 파일에 ${name} 캡처가 없습니다.`);
    return uri;
  }
  const key = `${runId}/captures/${name}`;
  if (!state.blobs.has(key)) {
    const response = await api(`/api/runs/${encodeURIComponent(runId)}/captures/${encodeURIComponent(name)}`);
    state.blobs.set(key, URL.createObjectURL(await response.blob()));
  }
  return state.blobs.get(key);
}

/* The vision reads of a step: an action owns the read of the capture taken after it (a mismatch
   or an unconfirmed result), and a look step the read of its own capture. */
function visionEventsFor(entry) {
  if (!entry) return [];
  let capture = null;
  if (entry.kind === "action") capture = entry.action.result ? entry.action.result.capture : null;
  else if (entry.kind === "look") capture = entry.capture.id;
  if (typeof capture !== "string" || !CAPTURE_ID.test(capture)) return [];
  return state.events.filter((event) => event.type === "vision" && event.capture === capture);
}

/* A look the text model called. With a vision model the step shows its question and answer; in a
   one-model run it only says a capture was taken, which the model saw itself. */
function lookDetails(entry) {
  const capture = entry.capture;
  return [
    el("div", { className: "step-head" }, [
      el("h2", { text: `#${state.index + 1} look` }),
      el("span", { className: "kind", text: "캡처 보기" }),
      visionBadge(entry),
    ]),
    factList([
      ["모델 스텝", capture.step],
      ["캡처", capture.id],
      ["표시 번호", capture.mark === null || capture.mark === undefined ? "창 전체" : `[${capture.mark}] 주변`],
    ]),
    ...visionSection(entry),
  ];
}

/* Which askers used the vision model at this step: "look 질문", "동작 뒤 불일치", ... */
function visionAskers(entry) {
  return [...new Set(visionEventsFor(entry).map((event) => VISION_ASKERS[event.asked_by] || "비전"))];
}

/* A small "비전" tag on a step where the vision model read a capture, so a person sees at a glance
   where the run leaned on the weaker model. Its title names who asked. */
function visionBadge(entry) {
  const askers = visionAskers(entry);
  if (askers.length === 0) return null;
  return el("span", { className: "vision-badge", text: "비전", attrs: { title: `비전 모델 사용: ${askers.join(", ")}` } });
}

/* The next step after the current one where the vision model was used, wrapping to the start; -1 when none. */
function nextVisionStep() {
  const count = state.entries.length;
  for (let offset = 1; offset <= count; offset += 1) {
    const index = (state.index + offset) % count;
    if (visionEventsFor(state.entries[index]).length > 0) return index;
  }
  return -1;
}

function visionSection(entry) {
  const events = visionEventsFor(entry);
  if (events.length === 0) return [];
  return [el("h3", { text: "비전 모델의 답" }), ...events.map(visionCard)];
}

function visionCard(event) {
  const points = Array.isArray(event.points) ? event.points.filter(isVisionPoint) : [];
  const marks = Array.isArray(event.marks) ? event.marks.filter(Number.isInteger) : [];
  const asker = VISION_ASKERS[event.asked_by] || event.asked_by || "알 수 없음";
  const figure = el("figure", { className: "vision-figure" }, [el("p", { className: "hint", text: "캡처를 불러오는 중…" })]);
  const card = el("section", { className: event.error ? "vision-card failed" : "vision-card", attrs: { "aria-label": `비전 답 · ${asker}` } }, [
    el("div", { className: "vision-head" }, [
      el("span", { className: "kind vision-kind", text: asker }),
      el("span", { className: "vision-capture", text: `캡처 ${event.capture}` }),
    ]),
    factList([
      ["질문", event.question],
      ["답", event.answer],
      ["확실성", event.unsure ? "모름(unsure)" : event.error ? null : "답함", event.unsure ? "warn-text" : ""],
      ["오류", event.error, "bad-text"],
      ["번호", marks.length ? marks.join(", ") : null],
      ["점", points.length ? points.map((point) => `${point.label || "(이름 없음)"} (${point.x}, ${point.y})`).join(" · ") : null],
      ["버린 항목", event.dropped ? `${event.dropped}개` : null, "warn-text"],
      ["비전 토큰", Number.isInteger(event.tokens) && event.tokens > 0 ? event.tokens : null],
      ["걸린 시간", typeof event.seconds === "number" ? `${event.seconds.toFixed(1)}초` : null],
    ]),
    figure,
  ]);
  fillVisionFigure(figure, event.capture, points);
  return card;
}

function isVisionPoint(point) {
  return point && typeof point === "object" && Number.isFinite(point.x) && Number.isFinite(point.y);
}

async function fillVisionFigure(figure, captureId, points) {
  const runId = state.runId;
  let url;
  try {
    url = await captureUrl(runId, captureId);
  } catch (error) {
    figure.replaceChildren(el("p", { className: "hint", text: `캡처를 보여 줄 수 없습니다: ${error.message}` }));
    return;
  }
  if (runId !== state.runId) return;
  const img = el("img", { attrs: { alt: `캡처 ${captureId}` } });
  img.src = url;
  await img.decode().catch(() => {});
  if (runId !== state.runId) return;
  const width = img.naturalWidth;
  const height = img.naturalHeight;
  if (!width || !height) {
    figure.replaceChildren(el("p", { className: "hint", text: "캡처 그림을 읽을 수 없습니다." }));
    return;
  }
  const markers = el("div", { className: "vision-markers", attrs: { "aria-hidden": "true" } });
  for (const point of points) {
    const left = `${(point.x / width) * 100}%`;
    const top = `${(point.y / height) * 100}%`;
    const dot = el("span", { className: "vision-dot" });
    const label = el("span", { className: point.x / width > 0.6 ? "vision-label flip" : "vision-label", text: point.label || "(이름 없음)" });
    for (const node of [dot, label]) {
      node.style.left = left;
      node.style.top = top;
    }
    markers.append(dot, label);
  }
  figure.replaceChildren(el("div", { className: "vision-frame" }, [img, markers]));
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
      ["로그 스텝", step.step],
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
      ["스텝 수", finish.steps],
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
  if (pending && pending !== entry) notices.unshift("동작 승인을 기다리고 있습니다. 타임라인의 노란 '대기' 스텝에서 승인하거나 거부하세요.");
  const question = pendingQuestion();
  if (question && !canAnswerHere()) notices.unshift("에이전트가 질문하고 답을 기다립니다. 이 실행은 터미널에서 답합니다.");
  else if (question && question !== entry) notices.unshift("에이전트가 질문하고 답을 기다립니다. 타임라인의 질문 스텝을 보면서 대화 칸에서 답하세요.");
  return notices;
}

function emptyStageText(entry, observation) {
  if (!state.runId) return "왼쪽에서 실행을 고르세요.";
  if (!entry) return "이 실행에는 기록된 스텝이 없습니다.";
  if (entry.kind === "legacy") return "옛 로그에는 스크린샷이 없습니다. 오른쪽에서 도구·인자·결과를 보세요.";
  if (observation) return "표시할 스크린샷이 없습니다.";
  if (entry.kind === "action") return state.view === "after" ? "이 동작 뒤에 기록된 관찰이 없습니다." : "이 동작 전에는 기록된 관찰이 없습니다.";
  return "이 스텝에는 연결된 관찰이 없습니다.";
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
  if (state.closing) return;
  try {
    const response = await api("/api/status");
    state.status = await response.json();
  } catch (error) {
    showBanner([error.message]);
    return;
  }
  if (state.closing) return;
  renderStartForm();
  renderViewerControls();
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
  $("system").placeholder = status.default_system ? `비우면 ${status.default_system} (설정 파일)` : "비우면 실행 파일 이름";
  renderSystemChoice(status);
  $("stage-call-limit").placeholder = Number.isInteger(status.default_stage_call_limit) ? `비우면 ${status.default_stage_call_limit}` : "비우면 기본값";
  renderSettingsSummary();
  const busy = Boolean(status.active);
  $("start-run").disabled = busy;
  if (busy) $("start-status").textContent = `실행 중: ${status.active}`;
  else if ($("start-status").dataset.kind !== "error") $("start-status").textContent = "";
}

/* The settings file's systems as a list in front of the exe box. The server sends names only,
   never their exe paths. Picking a name hides the exe and system boxes and the start request
   sends just that name; "직접 입력" brings the boxes back as they were. The options are built
   again only when the names change, so a poll keeps what the person picked. */
function renderSystemChoice(status) {
  const names = Array.isArray(status.systems) ? status.systems.filter((name) => typeof name === "string") : [];
  const select = $("system-choice");
  const key = JSON.stringify(names);
  if (state.systemNames !== key) {
    state.systemNames = key;
    select.replaceChildren(
      ...names.map((name) => el("option", { text: name, attrs: { value: name } })),
      el("option", { text: "직접 입력", attrs: { value: MANUAL_CHOICE } }),
    );
    select.value = names.includes(status.default_choice) ? status.default_choice : names[0] || MANUAL_CHOICE;
  }
  $("system-choice-field").hidden = names.length === 0;
  applySystemChoice();
}

function chosenSystem() {
  if ($("system-choice-field").hidden) return null;
  const value = $("system-choice").value;
  return value && value !== MANUAL_CHOICE ? value : null;
}

function applySystemChoice() {
  const chosen = chosenSystem();
  $("exe-field").hidden = Boolean(chosen);
  $("system-field").hidden = Boolean(chosen);
  $("exe").required = !chosen && !(state.status && state.status.default_exe);
}

function renderSettingsSummary() {
  const chosen = chosenSystem();
  const exe = $("exe").value.trim() || (state.status && state.status.default_exe) || "";
  const system = chosen ? null : $("system").value.trim();
  const parts = [
    chosen ? `시스템 ${chosen}` : exe ? exe.split(/[\\/]/).pop() : "실행 파일을 정하세요",
    system ? `시스템 ${system}` : null,
    `권한 ${$("level").value}`,
    stageLimitSummary(),
    $("approve").checked ? "동작 전 승인" : null,
  ];
  $("settings-summary").textContent = parts.filter(Boolean).join(" · ");
}

/* The form's stage call limit as the start request sends it: absent when empty, a number when it
   reads as a whole number (the server refuses 0 and negatives), otherwise the text itself, which
   the server refuses with its message like any other bad value. */
function stageLimitValue() {
  const text = $("stage-call-limit").value.trim();
  if (!text) return undefined;
  return /^-?\d+$/.test(text) ? Number(text) : text;
}

function stageLimitSummary() {
  const value = stageLimitValue();
  if (value !== undefined) return `단계 한도 ${value}`;
  const fallback = state.status && state.status.default_stage_call_limit;
  return Number.isInteger(fallback) ? `단계 한도 ${fallback}(기본)` : null;
}

function watchSettings() {
  for (const id of ["exe", "system", "stage-call-limit"]) $(id).addEventListener("input", renderSettingsSummary);
  for (const id of ["level", "approve"]) $(id).addEventListener("change", renderSettingsSummary);
  $("system-choice").addEventListener("change", () => {
    applySystemChoice();
    renderSettingsSummary();
  });
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
  const chosen = chosenSystem();
  const exe = chosen ? "" : $("exe").value.trim();
  if ($("exe").required && !exe) {
    setCollapsed("settings", false);
    setStartMessage("실행 파일의 절대 경로를 입력해 주세요.", "error");
    $("exe").focus();
    return;
  }
  const payload = { goal, level: Number($("level").value), approve: $("approve").checked };
  if (exe) payload.exe = exe;
  const system = chosen || $("system").value.trim();
  if (system) payload.system = system;
  const stageLimit = stageLimitValue();
  if (stageLimit !== undefined) payload.stage_call_limit = stageLimit;
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

/* ---------- stopping a run and closing the viewer ---------- */

/* Two buttons on the top bar. "실행 멈춤" shows wherever runs can start and is enabled only while
   one runs; the server stops that run at its next safe point and leaves the target app open, so
   the button reads "멈추는 중…" until the status says the run ended. "뷰어 종료" shows only when
   the server offers it (view.py, not run.py --view). Both confirm in a strip on the page, never in
   a browser dialog. Closing first asks without stopping the run; a 409 means a run is active, and
   the strip asks before sending stop_run. Once the server agreed to close, every poll stops and a
   cover over the page says how to open the viewer again. */
const STOP_CONFIRM = "실행을 멈출까요? 대상 앱은 그대로 둡니다.";
const SHUTDOWN_CONFIRM = "실행 중인 작업을 멈추고 뷰어를 종료할까요?";
const CLOSING_TEXT = "종료하는 중…";
const CLOSING_RUN_TEXT = "종료하는 중… 실행을 멈추고 기록을 마무리하고 있습니다.";
const CLOSED_TEXT = "뷰어를 종료했습니다. 다시 열려면 터미널에서 python view.py";

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Agent-Token": state.token || "" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  const body = await response.json().catch(() => null);
  return { status: response.status, body: body && typeof body === "object" ? body : {} };
}

function runStopping() {
  const status = state.status;
  if (!status || !status.active) return false;
  return status.stopping === true || state.stopRequested === status.active;
}

function renderViewerControls() {
  const status = state.status;
  if (state.stopRequested && (!status || status.active !== state.stopRequested)) state.stopRequested = null;
  const stopping = runStopping();
  const stop = $("stop-run");
  stop.hidden = !status || !status.can_start;
  stop.disabled = Boolean(state.closing) || !status || !status.active || stopping;
  stop.textContent = stopping ? "멈추는 중…" : "실행 멈춤";
  const shutdown = $("shutdown-viewer");
  shutdown.hidden = !status || status.can_shutdown !== true;
  shutdown.disabled = Boolean(state.closing);
  const pending = state.pendingConfirm;
  if (pending && pending.kind === "stop" && (stopping || !status || status.active !== pending.runId)) hideConfirm();
  if (stopping) watchStopping();
}

function setControlMessage(text) {
  $("control-status").textContent = text || "";
}

function showConfirm(kind, text, yesLabel, onYes, runId = null) {
  state.pendingConfirm = { kind, onYes, runId };
  $("confirm-text").textContent = text;
  $("confirm-yes").textContent = yesLabel;
  $("confirm-strip").hidden = false;
  $("confirm-no").focus();
}

function hideConfirm() {
  state.pendingConfirm = null;
  $("confirm-strip").hidden = true;
}

function acceptConfirm() {
  const pending = state.pendingConfirm;
  hideConfirm();
  if (pending) pending.onYes();
}

function askStop() {
  const runId = state.status && state.status.active;
  if (state.closing || !runId || runStopping()) return;
  setControlMessage("");
  showConfirm("stop", STOP_CONFIRM, "멈춤", () => sendStop(runId), runId);
}

async function sendStop(runId) {
  let result;
  try {
    result = await postJson("/api/runs/" + encodeURIComponent(runId) + "/stop", {});
  } catch (error) {
    setControlMessage(`서버에 연결하지 못했습니다: ${error.message}`);
    return;
  }
  if (result.status === 200) {
    state.stopRequested = runId;
    renderViewerControls();
  } else {
    setControlMessage(result.body.error || `실행을 멈추지 못했습니다 (${result.status})`);
  }
  await loadStatus();
}

/* While a stop is pending the status is read every second, so the button turns back once the
   run has written its record; runs that are not stopping keep the status reads they had. */
function watchStopping() {
  if (state.timers.stopping || state.closing) return;
  state.timers.stopping = setInterval(pollStopping, POLL_MS);
}

async function pollStopping() {
  if (state.closing || state.statusPolling) return;
  state.statusPolling = true;
  try {
    await loadStatus();
  } finally {
    state.statusPolling = false;
  }
  if (state.closing || runStopping()) return;
  clearInterval(state.timers.stopping);
  state.timers.stopping = null;
  try {
    await fetchRuns();
  } catch (error) {
    showBanner([error.message]);
  }
}

async function askShutdown() {
  if (state.closing) return;
  hideConfirm();
  setControlMessage("");
  await sendShutdown(false);
}

async function sendShutdown(stopRun) {
  $("shutdown-viewer").disabled = true;
  let result;
  try {
    result = await postJson("/api/shutdown", { stop_run: stopRun });
  } catch (error) {
    setControlMessage(`서버에 연결하지 못했습니다: ${error.message}`);
    renderViewerControls();
    return;
  }
  if (result.status === 202) {
    beginClosing(result.body.run_stopping === true);
    return;
  }
  if (result.status === 409 && !stopRun) {
    renderViewerControls();
    showConfirm("shutdown", SHUTDOWN_CONFIRM, "멈추고 종료", () => sendShutdown(true));
    return;
  }
  if (result.status === 404) {
    if (state.status) state.status.can_shutdown = false;
    setControlMessage("이 뷰어는 화면에서 끌 수 없습니다. 띄운 터미널에서 끄세요.");
  } else {
    setControlMessage(result.body.error || `뷰어를 종료하지 못했습니다 (${result.status})`);
  }
  renderViewerControls();
}

function stopPolling() {
  for (const name of Object.keys(state.timers)) {
    if (state.timers[name]) clearInterval(state.timers[name]);
    state.timers[name] = null;
  }
}

function showCover(text) {
  $("closed-text").textContent = text;
  const cover = $("closed-cover");
  if (cover.hidden) {
    for (const node of Array.from(document.body.children)) if (node !== cover) node.inert = true;
    cover.hidden = false;
  }
  $("closed-text").focus();
}

function beginClosing(runWillStop) {
  state.closing = "closing";
  hideConfirm();
  stopPolling();
  renderViewerControls();
  showCover(runWillStop ? CLOSING_RUN_TEXT : CLOSING_TEXT);
  state.timers.closed = setInterval(pollClosed, POLL_MS);
}

/* The server answers until it has closed, which can take up to two minutes while a stopped run
   writes its record. A refused connection, or an answer from some other server that does not
   know this token, means this viewer is gone. */
async function pollClosed() {
  if (state.closing !== "closing" || state.closePolling) return;
  state.closePolling = true;
  let gone = false;
  try {
    const response = await fetch("/api/status", { headers: { "X-Agent-Token": state.token || "" }, cache: "no-store" });
    gone = !response.ok;
  } catch (_) {
    gone = true;
  } finally {
    state.closePolling = false;
  }
  if (gone && state.closing === "closing") finishClosing();
}

function finishClosing() {
  state.closing = "closed";
  stopPolling();
  showCover(CLOSED_TEXT);
}

function watchViewerControls() {
  $("stop-run").addEventListener("click", askStop);
  $("shutdown-viewer").addEventListener("click", askShutdown);
  $("confirm-yes").addEventListener("click", acceptConfirm);
  $("confirm-no").addEventListener("click", hideConfirm);
  $("confirm-strip").addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    hideConfirm();
  });
}

function fillLevels() {
  const select = $("level");
  select.replaceChildren(...LEVEL_LABELS.map((label, level) => el("option", { text: label, attrs: { value: String(level) } })));
  select.value = "3";
}

/* The rows of a box's tooltip: kind, role, name, description, ref, and for an element with a
   native id its id and the name an administrator registered for it (alias). A record from before
   those keys simply has no such rows. Every value is shown as text. */
function tooltipRows(item) {
  const kind = item.kind === "question" ? "질문 표시"
    : item.kind === "id" ? "ID만 있음 (ref·이름 없음)"
      : elementKind(item) === "text" ? "텍스트 (ref 없음)" : "컨트롤";
  const native = item.native_id && typeof item.native_id === "object" ? item.native_id.value : null;
  const rows = [["종류", kind], ["역할", item.role], ["이름", item.name], ["설명", item.description], ["ref", item.ref],
    ["ID", native], ["등록 이름", item.alias]];
  return rows.filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([term, value]) => [term, String(value)]);
}

function showTooltip(item, event) {
  const tooltip = $("tooltip");
  const dl = el("dl");
  for (const [term, value] of tooltipRows(item)) dl.append(el("dt", { text: term }), el("dd", { text: value }));
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
  if (state.closing) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (document.body.classList.contains("knowledge-mode")) return;
  const tag = (event.target.tagName || "").toLowerCase();
  if (["input", "textarea", "select"].includes(tag) || event.target.isContentEditable) return;
  if (event.key === "ArrowLeft") select(state.index - 1);
  else if (event.key === "ArrowRight") select(state.index + 1);
  else if (event.key === "b" || event.key === "B") setView(state.view === "before" ? "after" : "before");
  else if (event.key === "a" || event.key === "A") setBoxes(state.boxes === "all" ? "target" : "all");
  else if (event.key === "t" || event.key === "T") toggleTexts();
  else if (event.key === "v" || event.key === "V") {
    const next = nextVisionStep();
    if (next >= 0) select(next);
  }
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
  $("toggle-summary").addEventListener("click", toggleSummary);
  $("copy-summary").addEventListener("click", copySummary);
  $("stage-detail-toggle").addEventListener("click", toggleStageDetail);
  document.addEventListener("keydown", onKey);
  new ResizeObserver(positionLabels).observe($("stage"));
  fillLevels();
  watchSettings();
  initPanels();
  if (exported) {
    startExport(exported);
    return;
  }
  if (!state.token) {
    showBanner(["접속 토큰이 없습니다. view.py가 터미널에 출력한 주소로 여세요."]);
    return;
  }
  watchViewerControls();
  initKnowledge();
  loadStatus().then(initWorkflows).then(initProfiles);
  loadRuns();
  state.timers.live = setInterval(pollLive, POLL_MS);
  state.timers.ready = setInterval(renderReadyBanner, POLL_MS);
}

start();
