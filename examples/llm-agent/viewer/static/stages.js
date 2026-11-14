"use strict";

/* The stages of a planned run: the stage bar over the capture, the stage boundaries in the timeline
   and the state of every stage at the step being shown. Everything comes from the plan, stage,
   model, question and answer events of the record; nothing is stored in the browser. app.js owns
   the state, the DOM helpers and rendering and calls into the functions here. */

const STAGE_STATUS = {
  pending: { label: "대기", icon: "○" },
  active: { label: "진행 중", icon: "▶" },
  question: { label: "질문 중", icon: "?" },
  done: { label: "완료", icon: "✓" },
  stopped: { label: "중단", icon: "■" },
};
const STAGE_EVENT_STATUS = { started: "active", limit: "active", done: "done", stopped: "stopped" };
const STAGE_KIND_LABELS = { text: "관찰 글의 표시 번호", picture: "캡처" };
const LIMIT_QUESTION_KINDS = ["stage_limit", "plan_limit"];

function isLimitQuestion(question) {
  return Boolean(question) && LIMIT_QUESTION_KINDS.includes(question.kind);
}

function planStatus(status) {
  return STAGE_STATUS[status] && status !== "question" ? status : "pending";
}

function hasPlan(events) {
  return events.some((event) => event.type === "plan");
}

/* The allowance a stage starts with: no event says it before the first stage event of the run. */
function firstLimit(events) {
  const started = events.find((event) => event.type === "stage" && event.status === "started" && Number.isInteger(event.limit));
  return started ? started.limit : null;
}

/* The stages as they stood after events[0..end). */
function foldStages(events, end, defaultLimit = null) {
  const folded = { revision: null, planStep: null, stages: [], asking: null };
  const byN = new Map();
  for (let at = 0; at < Math.min(end, events.length); at += 1) {
    const event = events[at];
    if (event.type === "plan" && Array.isArray(event.stages)) {
      folded.revision = Number.isInteger(event.revision) ? event.revision : folded.revision;
      folded.planStep = event.step;
      folded.stages = event.stages.map((stage) => {
        const old = byN.get(stage.n);
        const same = old && old.goal === stage.goal;
        return {
          n: stage.n,
          goal: stage.goal || "",
          done_when: stage.done_when || "",
          status: planStatus(stage.status),
          calls: old ? old.calls : 0,
          limit: old ? old.limit : defaultLimit,
          kind: same ? old.kind : null,
          note: same ? old.note : null,
        };
      });
      byN.clear();
      for (const stage of folded.stages) byN.set(stage.n, stage);
    } else if (event.type === "stage") {
      const stage = byN.get(event.n);
      if (!stage) continue;
      stage.status = STAGE_EVENT_STATUS[event.status] || stage.status;
      if (Number.isInteger(event.calls)) stage.calls = event.calls;
      if (Number.isInteger(event.limit)) stage.limit = event.limit;
      if (event.status === "done") {
        stage.kind = event.kind || null;
        stage.note = event.note || null;
      }
    } else if (event.type === "model" && Number.isInteger(event.stage_calls)) {
      const stage = byN.get(event.stage);
      if (stage) stage.calls = event.stage_calls;
      if (stage && Number.isInteger(event.stage_limit)) stage.limit = event.stage_limit;
    } else if (event.type === "question" && isLimitQuestion(event)) {
      folded.asking = { id: event.id, stage: event.stage };
    } else if (event.type === "answer" && folded.asking && folded.asking.id === event.id) {
      folded.asking = null;
    }
  }
  for (const stage of folded.stages) {
    if (folded.asking && folded.asking.stage === stage.n && stage.status === "active") stage.status = "question";
  }
  return folded;
}

/* The number of the stage in progress after each event (null before a plan and after the last stage),
   and the event at which each stage first became the one in progress. */
function activeStageAfterEach(events) {
  const active = [];
  const startedAt = new Map();
  let current = null;
  let statuses = new Map();
  for (const event of events) {
    if (event.type === "plan" && Array.isArray(event.stages)) {
      statuses = new Map(event.stages.map((stage) => [stage.n, planStatus(stage.status)]));
    } else if (event.type === "stage" && statuses.has(event.n)) {
      statuses.set(event.n, STAGE_EVENT_STATUS[event.status] || statuses.get(event.n));
    }
    if (event.type === "plan" || event.type === "stage") {
      current = null;
      for (const [n, status] of statuses) if (status === "active") current = n;
      if (current !== null && !startedAt.has(current)) startedAt.set(current, active.length);
    }
    active.push(current);
  }
  return { active, startedAt };
}

/* Which stage each timeline entry belongs to (the stage in progress when its event was written), and
   where each stage starts in the timeline: its first entry, or for a stage that has no entry of its
   own (only model turns, such as a stage closed by stage_done alone) the first entry after it began. */
function stageLayout() {
  const key = `${state.runId}|${state.events.length}|${state.entries.length}`;
  if (state.stageCache && state.stageCache.key === key) return state.stageCache;
  const { active, startedAt } = activeStageAfterEach(state.events);
  const stages = state.entries.map((entry) => (Number.isInteger(entry.at) ? active[entry.at] : null));
  const starts = new Map();
  const empty = new Set();
  for (const [n, at] of startedAt) {
    let index = stages.indexOf(n);
    if (index < 0) {
      empty.add(n);
      index = state.entries.findIndex((entry) => Number.isInteger(entry.at) && entry.at > at);
    }
    if (index >= 0) starts.set(n, index);
  }
  state.stageCache = { key, stages, starts, empty };
  return state.stageCache;
}

function entryStages() {
  return stageLayout().stages;
}

/* Events that make up the moment shown with entry `index`: the last entry shows everything; a question
   shows the moment it was asked; any other entry runs up to the next entry. */
function stageMomentEnd(index) {
  const { entries, events } = state;
  const entry = entries[index];
  if (!entry || index >= entries.length - 1 || !Number.isInteger(entry.at)) return events.length;
  if (entry.kind === "question") return entry.at + 1;
  const next = entries[index + 1];
  return Number.isInteger(next.at) ? Math.max(entry.at + 1, next.at) : events.length;
}

function stageCalls(stage) {
  return Number.isInteger(stage.limit) ? `${stage.calls}/${stage.limit}` : `${stage.calls}회`;
}

function firstEntryOfStage(n) {
  const index = stageLayout().starts.get(n);
  return index === undefined ? -1 : index;
}

function renderStageBar() {
  const bar = $("stage-bar");
  const planned = state.runKind === "run" && hasPlan(state.events);
  bar.hidden = !planned;
  if (!planned) {
    $("stage-list").replaceChildren();
    $("stage-detail").replaceChildren();
    return;
  }
  const folded = foldStages(state.events, stageMomentEnd(state.index), firstLimit(state.events));
  const list = $("stage-list");
  if (folded.stages.length === 0) {
    list.replaceChildren(el("li", { className: "stage-none", text: "아직 계획을 세우기 전입니다." }));
    $("stage-detail").replaceChildren();
    $("stage-detail-toggle").hidden = true;
    return;
  }
  const current = folded.stages.find((stage) => stage.status === "active" || stage.status === "question");
  const focusN = state.stageFocus !== null && folded.stages.some((stage) => stage.n === state.stageFocus) ? state.stageFocus
    : current ? current.n
      : (folded.stages.find((stage) => stage.status === "stopped") || folded.stages.filter((stage) => stage.status === "done").pop()
        || folded.stages[0]).n;
  list.replaceChildren(...folded.stages.map((stage) => {
    const info = STAGE_STATUS[stage.status];
    const isCurrent = current && current.n === stage.n;
    const button = el("button", {
      className: `stage-chip ${stage.status}`,
      attrs: {
        type: "button",
        "data-n": String(stage.n),
        "aria-label": `단계 ${stage.n} · ${info.label} · 호출 ${stageCalls(stage)} · ${stage.goal}`,
        "aria-controls": "stage-detail",
        ...(isCurrent ? { "aria-current": "step" } : {}),
        ...(stage.n === focusN ? { "data-focused": "true" } : {}),
      },
    }, [
      el("span", { className: "stage-icon", text: info.icon, attrs: { "aria-hidden": "true" } }),
      el("span", { className: "stage-num", text: stage.n, attrs: { "aria-hidden": "true" } }),
      el("span", { className: "stage-goal", text: stage.goal, attrs: { "aria-hidden": "true" } }),
      el("span", { className: "stage-calls", text: stageCalls(stage), attrs: { "aria-hidden": "true" } }),
    ]);
    button.addEventListener("click", () => chooseStage(stage.n));
    return el("li", {}, [button]);
  }));
  renderStageDetail(folded, folded.stages.find((stage) => stage.n === focusN));
}

function renderStageDetail(folded, stage) {
  const detail = $("stage-detail");
  const info = STAGE_STATUS[stage.status];
  const head = [`단계 ${stage.n}/${folded.stages.length}`, info.label, `호출 ${stageCalls(stage)}`];
  if (folded.revision) head.push(`계획 고침 ${folded.revision}번`);
  const parts = [
    el("strong", { className: `stage-detail-head ${stage.status}`, text: `${info.icon} ${head.join(" · ")}` }),
    el("span", { text: ` 목표: ${stage.goal}` }),
    el("span", { text: ` · 완료 기준: ${stage.done_when}` }),
  ];
  const finished = stage.status === "done" ? stage : state.stageFocus === stage.n ? laterDone(stage) : null;
  if (finished) {
    const later = finished === stage ? "" : " · 나중에 완료";
    parts.push(el("span", { className: "stage-detail-done", text: `${later} · 근거: ${STAGE_KIND_LABELS[finished.kind] || finished.kind || "알 수 없음"}` }));
    if (finished.note) parts.push(el("span", { className: "stage-detail-done", text: ` · 메모: ${finished.note}` }));
  }
  if (firstEntryOfStage(stage.n) < 0 && stage.status === "pending") parts.push(el("span", { text: " · 아직 시작하지 않았습니다" }));
  if (stage.status !== "pending" && stageLayout().empty.has(stage.n)) parts.push(el("span", { text: " · 이 단계에는 타임라인 스텝이 없습니다(모델 호출만)" }));
  detail.replaceChildren(...parts);
  detail.classList.toggle("expanded", state.stageDetailOpen);
  const toggle = $("stage-detail-toggle");
  toggle.setAttribute("aria-expanded", String(state.stageDetailOpen));
  toggle.textContent = state.stageDetailOpen ? "접기" : "전체 보기";
  toggle.hidden = !state.stageDetailOpen && detail.scrollHeight <= detail.clientHeight + 1;
}

/* The same stage at the end of the record when it was done later than the step shown: a stage chosen
   in the bar opens at its first step, before it was done, and still shows its evidence and note. */
function laterDone(stage) {
  const end = foldStages(state.events, state.events.length, firstLimit(state.events));
  const final = end.stages.find((other) => other.n === stage.n && other.goal === stage.goal);
  return final && final.status === "done" ? final : null;
}

function chooseStage(n) {
  const index = firstEntryOfStage(n);
  if (index >= 0 && index !== state.index) {
    select(index);
  }
  state.stageFocus = n;
  renderStageBar();
  const chip = $("stage-list").querySelector(`[data-n="${n}"]`);
  if (chip) chip.focus();
}

function toggleStageDetail() {
  state.stageDetailOpen = !state.stageDetailOpen;
  renderStageBar();
}

/* The labels put before the timeline step where a stage starts; a stage without steps of its own is
   marked before the next step and says so. */
function timelineStageTag(index) {
  const { starts, empty } = stageLayout();
  const tags = [...starts].filter(([, at]) => at === index).map(([n]) => n).sort((a, b) => a - b)
    .map((n) => el("span", { className: empty.has(n) ? "stage-tag empty" : "stage-tag", text: empty.has(n) ? `단계 ${n} · 스텝 없음` : `단계 ${n}` }));
  return tags.length ? el("span", { className: "stage-tags" }, tags) : null;
}
