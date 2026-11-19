"use strict";

/* The workflow view and the workflow marks on a run.

   The view lists the chosen system's saved workflows as the server sends them (id, goal, status,
   counts, parameters with their examples, values that cannot change) and replays one with the
   values typed into its parameter boxes; an empty box means the example (a path: a new name in
   the example's folder). A replay is a run like any other: once it starts, the view switches to
   its timeline. Everything in a workflow file came from a screen, so it is only ever put on the
   page as text. app.js owns the DOM helpers, the token, the run list and selectRun();
   knowledge.js owns knowledgeRequest() and the knowledge view this one takes turns with.

   On a run, each action of a replay is tagged with the workflow step it belongs to (the
   workflow_step event that follows it), and the run summary says where a replay stopped, or what
   a model run's workflow making gave. */

const WORKFLOW_KIND_LABELS = { text: "글", list: "여러 개", path: "저장 경로" };
const WORKFLOW_KIND_HINTS = {
  text: "비우면 예시 값",
  list: "쉼표로 나눔 · 비우면 예시 값",
  path: "절대 경로 · 비우면 예시 폴더에 새 이름으로 저장",
};
const WORKFLOW_STEP_KINDS = { act: "동작", select_only: "이것만 선택", check_file: "파일 확인" };

const workflowsView = {
  available: false,
  shown: false,
  system: null,
  items: [],
  drafts: {},
  message: null,
  busy: false,
  seq: 0,
};

function workflowStatusLabel(item) {
  if (item.status !== "verified") return "미확인";
  return item.verified_with_other_values ? "확인됨(다른 값)" : "확인됨(같은 값)";
}

function workflowExampleText(param) {
  const example = param.example;
  if (Array.isArray(example)) return example.map(String).join(", ");
  return example === undefined || example === null ? "" : String(example);
}

async function initWorkflows() {
  const button = $("show-workflows");
  if (state.exportMode || !state.token) {
    button.hidden = true;
    return;
  }
  const reply = await knowledgeRequest("/api/workflows");
  workflowsView.available = reply.ok;
  button.hidden = !reply.ok;
  if (!reply.ok) return;
  $("view-switch").hidden = false;
  button.addEventListener("click", () => showWorkflows(true));
  $("show-runs").addEventListener("click", () => showWorkflows(false));
  $("show-knowledge").addEventListener("click", () => showWorkflows(false, true));
  if (new URLSearchParams(location.hash.slice(1)).has("workflows")) showWorkflows(true);
}

async function showWorkflows(shown, forKnowledge = false) {
  if (!workflowsView.available) return;
  if (!shown && !workflowsView.shown) return;
  workflowsView.shown = shown;
  document.body.classList.toggle("workflow-mode", shown);
  $("workflow-view").hidden = !shown;
  $("show-workflows").setAttribute("aria-pressed", String(shown));
  if (shown) {
    if (knowledge.shown) await showKnowledge(false);
    $("show-runs").setAttribute("aria-pressed", "false");
    $("show-knowledge").setAttribute("aria-pressed", "false");
    if (!workflowsView.system) workflowsView.system = defaultWorkflowSystem();
    await loadWorkflows();
    return;
  }
  if (forKnowledge) return;
  $("show-runs").setAttribute("aria-pressed", "true");
  const run = state.runId ? `#${state.runKind === "legacy" ? "legacy" : "run"}=${encodeURIComponent(state.runId)}` : "";
  history.replaceState(null, "", location.pathname + run);
  render();
}

function workflowSystems() {
  const names = state.status && Array.isArray(state.status.systems) ? state.status.systems : [];
  return names.filter((name) => typeof name === "string");
}

function defaultWorkflowSystem() {
  const names = workflowSystems();
  const chosen = typeof chosenSystem === "function" ? chosenSystem() : null;
  if (chosen && names.includes(chosen)) return chosen;
  const status = state.status || {};
  if (names.includes(status.default_choice)) return status.default_choice;
  return names[0] || null;
}

async function loadWorkflows() {
  const seq = ++workflowsView.seq;
  const system = workflowsView.system;
  history.replaceState(null, "", `${location.pathname}#workflows${system ? `=${encodeURIComponent(system)}` : ""}`);
  const query = system ? `?system=${encodeURIComponent(system)}` : "";
  const reply = await knowledgeRequest(`/api/workflows${query}`);
  if (seq !== workflowsView.seq) return;
  if (reply.ok) {
    workflowsView.items = Array.isArray(reply.body.workflows) ? reply.body.workflows : [];
  } else {
    workflowsView.items = [];
    workflowsView.message = { kind: "error", text: reply.body.error || `workflow를 읽지 못했습니다 (${reply.status})` };
  }
  renderWorkflows();
}

function renderWorkflows() {
  const view = $("workflow-view");
  const names = workflowSystems();
  const head = [el("h2", { text: workflowsView.system ? `workflow (${workflowsView.system})` : "workflow" })];
  if (names.length) {
    const picker = el("select", { attrs: { id: "workflow-system", "aria-label": "시스템" } },
      names.map((name) => el("option", { text: name, attrs: { value: name } })));
    picker.value = workflowsView.system || names[0];
    picker.addEventListener("change", () => {
      workflowsView.system = picker.value;
      workflowsView.message = null;
      loadWorkflows();
    });
    head.push(el("label", { className: "field knowledge-picker" }, [el("span", { text: "시스템" }), picker]));
  }
  const reload = el("button", { className: "ghost", text: "새로고침", attrs: { type: "button" } });
  reload.addEventListener("click", () => {
    workflowsView.message = null;
    loadWorkflows();
  });
  head.push(reload);
  const nodes = [
    el("div", { className: "knowledge-head" }, head),
    el("p", { className: "hint", text: "성공한 실행에서 만든 workflow입니다. 재실행은 모델을 부르지 않고 적힌 단계대로 돌다가, 대상이 없거나 여럿이거나 결과가 다르면 그 단계에서 멈춥니다. 대상 앱은 닫지 않습니다." }),
  ];
  if (workflowsView.message) {
    nodes.push(el("p", { className: `knowledge-message ${workflowsView.message.kind}`, text: workflowsView.message.text,
                         attrs: { role: "status" } }));
  }
  if (workflowsView.items.length === 0) {
    nodes.push(el("p", { className: "empty", text: "이 시스템에는 workflow가 없습니다. 성공한 모델 실행이 끝나면 만들어집니다." }));
  } else {
    nodes.push(el("ul", { className: "knowledge-list" }, workflowsView.items.map(workflowCard)));
  }
  view.replaceChildren(el("div", { className: "knowledge-inner" }, nodes));
}

function workflowCard(item) {
  const verified = item.status === "verified";
  const card = el("li", { className: "knowledge-card workflow-card", attrs: { "data-id": item.id } });
  card.append(el("div", { className: "knowledge-card-head" }, [
    el("span", { className: "knowledge-entry-id", text: item.id }),
    el("span", { className: `workflow-status ${verified ? "verified" : "unverified"}`, text: workflowStatusLabel(item) }),
    el("span", { className: "knowledge-uses", text: `통과 ${item.passes || 0} · 실패 ${item.failures || 0}` }),
  ]));
  card.append(el("p", { className: "workflow-goal", text: item.goal || "" }));
  const drafts = workflowsView.drafts[item.id] || (workflowsView.drafts[item.id] = {});
  const params = Array.isArray(item.params) ? item.params : [];
  if (params.length) card.append(el("div", { className: "workflow-params" }, params.map((param) => workflowParam(param, drafts))));
  const fixed = Array.isArray(item.fixed) ? item.fixed : [];
  if (fixed.length) {
    card.append(el("p", { className: "workflow-fixed" }, [
      el("span", { className: "workflow-fixed-title", text: "바꿀 수 없는 값: " }),
      el("span", { text: fixed.map((entry) => `${entry.name} = ${entry.value}`).join(", ") }),
    ]));
  }
  const run = el("button", { className: "primary", text: "재실행", attrs: { type: "button" } });
  run.disabled = workflowsView.busy || Boolean(state.status && state.status.active);
  if (state.status && state.status.active) run.title = `실행 중인 작업이 있습니다: ${state.status.active}`;
  run.addEventListener("click", () => replayWorkflow(item));
  card.append(el("div", { className: "knowledge-actions" }, [run]));
  return card;
}

function workflowParam(param, drafts) {
  const input = el("input", { attrs: { type: "text", spellcheck: "false", "aria-label": param.name } });
  input.placeholder = workflowExampleText(param);
  input.value = drafts[param.name] || "";
  input.addEventListener("input", () => { drafts[param.name] = input.value; });
  return el("label", { className: "workflow-param" }, [
    el("span", { className: "workflow-param-name", text: param.name }),
    el("span", { className: "workflow-param-kind", text: WORKFLOW_KIND_LABELS[param.kind] || param.kind || "" }),
    input,
    el("span", { className: "workflow-param-hint", text: WORKFLOW_KIND_HINTS[param.kind] || "" }),
  ]);
}

async function replayWorkflow(item) {
  const drafts = workflowsView.drafts[item.id] || {};
  const values = {};
  for (const param of item.params || []) {
    const value = (drafts[param.name] || "").trim();
    if (value) values[param.name] = value;
  }
  const payload = { values };
  if (workflowsView.system) payload.system = workflowsView.system;
  workflowsView.busy = true;
  workflowsView.message = { kind: "ok", text: "시작하는 중…" };
  renderWorkflows();
  const reply = await knowledgeRequest(`/api/workflows/${encodeURIComponent(item.id)}/run`, payload);
  workflowsView.busy = false;
  if (reply.status !== 201) {
    workflowsView.message = { kind: "error", text: reply.body.error || `재실행을 시작하지 못했습니다 (${reply.status})` };
    renderWorkflows();
    await loadStatus();
    return;
  }
  workflowsView.message = null;
  delete workflowsView.drafts[item.id];
  await showWorkflows(false);
  await loadStatus();
  try {
    await fetchRuns();
  } catch (error) {
    showBanner([error.message]);
    return;
  }
  await selectRun(reply.body.run_id, "run");
}

/* ---------- workflow marks on a run ---------- */

const workflowStepCache = { events: null, length: -1, after: [] };

function workflowStepsAfter() {
  if (workflowStepCache.events === state.events && workflowStepCache.length === state.events.length) {
    return workflowStepCache.after;
  }
  const after = new Array(state.events.length).fill(null);
  let next = null;
  for (let at = state.events.length - 1; at >= 0; at -= 1) {
    after[at] = next;
    if (state.events[at].type === "workflow_step") next = state.events[at];
  }
  workflowStepCache.events = state.events;
  workflowStepCache.length = state.events.length;
  workflowStepCache.after = after;
  return after;
}

/* The workflow step an entry's event belongs to: the first workflow_step event after it. */
function workflowStepOf(entry) {
  if (!entry || typeof entry.at !== "number" || !state.events.some((event) => event.type === "workflow_step")) return null;
  return workflowStepsAfter()[entry.at] || null;
}

function workflowTag(entry) {
  const step = workflowStepOf(entry);
  return step ? `W${step.n} ` : "";
}

function workflowStepText(entry) {
  const step = workflowStepOf(entry);
  if (!step) return null;
  const kind = WORKFLOW_STEP_KINDS[step.kind] || step.kind;
  const outcome = step.ok ? "통과" : `멈춤 ${step.code || ""}`.trim();
  return `${step.n}. ${step.goal || ""} (${kind} · ${outcome})`;
}

/* One part of the run summary: where a replay stopped or how far it got, or what a model run's
   workflow making gave. Null for a run with neither. */
function workflowSummaryPart(run, events) {
  const steps = events.filter((event) => event.type === "workflow_step");
  if (steps.length) {
    const last = steps[steps.length - 1];
    if (!last.ok) return `workflow 멈춘 단계 ${last.n} · ${last.code || "코드 없음"}`;
    return `workflow 단계 ${steps.length}개 통과`;
  }
  const made = events.filter((event) => event.type === "workflow");
  if (made.length) {
    const outcome = made[made.length - 1];
    if (outcome.created) return `workflow ${outcome.id} 미확인 · 단계 ${outcome.steps} · 파라미터 ${outcome.params}`;
    return `workflow 못 만듦 · ${outcome.code || "코드 없음"}`;
  }
  const finish = events.find((event) => event.type === "finish");
  if (run && run.started_by === "workflow" && finish && finish.reason) return `workflow 멈춤 · ${finish.reason}`;
  return null;
}
