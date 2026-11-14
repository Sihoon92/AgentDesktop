"use strict";

/* The knowledge view: per-system entries the agent learned from answers, which a person can read,
   reword, stop using, start using again, or delete. The server only serves systems that exist in
   the knowledge folder; an entry that fails validation comes back as a 400 with the reason, and
   the files can still be edited by hand. app.js owns the DOM helpers, the token and start(). */

const KNOWLEDGE_TYPE_LABELS = { resolution: "해결 기록", completion: "완료 조건" };
const KNOWLEDGE_STATUS_LABELS = { active: "사용 중", disabled: "멈춤" };

const knowledge = {
  available: false,
  shown: false,
  systems: [],
  system: null,
  entries: [],
  warnings: [],
  editing: null,
  confirming: null,
  message: null,
  seq: 0,
};

async function knowledgeRequest(path, body) {
  const options = { headers: { "X-Agent-Token": state.token || "" }, cache: "no-store" };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    return { ok: false, status: 0, body: { error: `서버에 연결하지 못했습니다: ${error.message}` } };
  }
  const reply = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body: reply };
}

function systemPath(system) {
  return `/api/knowledge/${encodeURIComponent(system)}`;
}

function entryPath(system, id) {
  return `${systemPath(system)}/${encodeURIComponent(id)}`;
}

async function initKnowledge() {
  const toggle = $("view-switch");
  if (state.exportMode || !state.token) {
    toggle.hidden = true;
    return;
  }
  const hash = new URLSearchParams(location.hash.slice(1));
  const reply = await knowledgeRequest("/api/knowledge");
  knowledge.available = reply.ok;
  toggle.hidden = !reply.ok;
  if (!reply.ok) return;
  knowledge.systems = reply.body.systems || [];
  $("show-runs").addEventListener("click", () => showKnowledge(false));
  $("show-knowledge").addEventListener("click", () => showKnowledge(true));
  if (hash.has("knowledge")) showKnowledge(true, hash.get("knowledge") || null);
}

async function showKnowledge(shown, system = null) {
  if (!knowledge.available) return;
  knowledge.shown = shown;
  document.body.classList.toggle("knowledge-mode", shown);
  $("knowledge-view").hidden = !shown;
  $("show-runs").setAttribute("aria-pressed", String(!shown));
  $("show-knowledge").setAttribute("aria-pressed", String(shown));
  if (!shown) {
    const run = state.runId ? `#${state.runKind === "legacy" ? "legacy" : "run"}=${encodeURIComponent(state.runId)}` : "";
    history.replaceState(null, "", location.pathname + run);
    render();
    return;
  }
  const reply = await knowledgeRequest("/api/knowledge");
  knowledge.systems = reply.ok ? reply.body.systems || [] : [];
  const wanted = system || knowledge.system;
  knowledge.system = knowledge.systems.includes(wanted) ? wanted : knowledge.systems[0] || null;
  await loadKnowledgeEntries();
}

async function loadKnowledgeEntries() {
  const seq = ++knowledge.seq;
  const system = knowledge.system;
  history.replaceState(null, "", `${location.pathname}#knowledge${system ? `=${encodeURIComponent(system)}` : ""}`);
  if (!system) {
    knowledge.entries = [];
    knowledge.warnings = [];
    renderKnowledge();
    return;
  }
  const reply = await knowledgeRequest(systemPath(system));
  if (seq !== knowledge.seq) return;
  if (!reply.ok) {
    knowledge.entries = [];
    knowledge.warnings = [];
    knowledge.message = { kind: "error", text: reply.body.error || `지식을 읽지 못했습니다 (${reply.status})` };
  } else {
    knowledge.entries = reply.body.entries || [];
    knowledge.warnings = reply.body.warnings || [];
  }
  renderKnowledge();
}

function renderKnowledge() {
  const view = $("knowledge-view");
  const picker = el("select", { attrs: { id: "knowledge-system", "aria-label": "시스템" } },
    knowledge.systems.map((name) => el("option", { text: name, attrs: { value: name } })));
  picker.value = knowledge.system || "";
  picker.disabled = knowledge.systems.length === 0;
  picker.addEventListener("change", () => {
    knowledge.system = picker.value;
    knowledge.editing = null;
    knowledge.confirming = null;
    knowledge.message = null;
    loadKnowledgeEntries();
  });
  const reload = el("button", { className: "ghost", text: "새로고침", attrs: { type: "button" } });
  reload.addEventListener("click", () => showKnowledge(true, knowledge.system));
  const counts = knowledge.entries.length
    ? `${knowledge.entries.length}개 · 사용 중 ${knowledge.entries.filter((entry) => entry.status === "active").length}개`
    : "";
  const nodes = [
    el("div", { className: "knowledge-head" }, [
      el("h2", { text: "시스템별 지식" }),
      el("label", { className: "field knowledge-picker" }, [el("span", { text: "시스템" }), picker]),
      el("span", { className: "knowledge-counts", text: counts }),
      reload,
    ]),
    el("p", { className: "hint", text: "에이전트가 질문하고 받은 답 가운데 승인한 것입니다. 실행할 때 사용 중인 항목만 모델에게 줍니다. 파일을 직접 고쳐도 됩니다." }),
  ];
  if (knowledge.message) {
    nodes.push(el("p", { className: `knowledge-message ${knowledge.message.kind}`, text: knowledge.message.text, attrs: { role: "status" } }));
  }
  if (knowledge.warnings.length) {
    nodes.push(el("div", { className: "knowledge-warnings", attrs: { role: "note" } }, [
      el("p", { className: "knowledge-warnings-title", text: `읽지 못해 건너뛴 파일 ${knowledge.warnings.length}개 (실행에도 쓰이지 않습니다)` }),
      el("ul", {}, knowledge.warnings.map((line) => el("li", { text: line }))),
    ]));
  }
  if (knowledge.systems.length === 0) {
    nodes.push(el("p", { className: "empty", text: "아직 등록된 지식이 없습니다. 성공한 실행에서 등록을 승인하면 여기에 생깁니다." }));
  } else if (knowledge.entries.length === 0) {
    nodes.push(el("p", { className: "empty", text: "이 시스템에는 항목이 없습니다." }));
  } else {
    nodes.push(el("ul", { className: "knowledge-list" }, knowledge.entries.map(knowledgeCard)));
  }
  view.replaceChildren(el("div", { className: "knowledge-inner" }, nodes));
}

function knowledgeCard(entry) {
  const disabled = entry.status === "disabled";
  const card = el("li", { className: `knowledge-card${disabled ? " disabled" : ""}`, attrs: { "data-id": entry.id } });
  const head = el("div", { className: "knowledge-card-head" }, [
    el("span", { className: `knowledge-type ${entry.type}`, text: KNOWLEDGE_TYPE_LABELS[entry.type] || entry.type }),
    el("span", { className: `knowledge-status ${entry.status}`, text: KNOWLEDGE_STATUS_LABELS[entry.status] || entry.status }),
    el("span", { className: "knowledge-entry-id", text: entry.id }),
    el("span", { className: "knowledge-uses", text: `참고 ${entry.uses || 0}회 · 참고 뒤 실패 ${entry.failures || 0}회` }),
  ]);
  card.append(head);
  if (knowledge.editing === entry.id) card.append(knowledgeEditor(entry));
  else card.append(knowledgeBody(entry));
  card.append(knowledgeSource(entry));
  if (knowledge.editing !== entry.id) card.append(knowledgeActions(entry));
  return card;
}

function knowledgeBody(entry) {
  if (entry.type === "completion") {
    return factList([
      ["작업", entry.task],
      ["확인 방법", (entry.checks || []).map(describeCheck).join(" · ")],
    ]);
  }
  return factList([["상황", entry.situation], ["해결", entry.resolution]]);
}

function describeCheck(check) {
  if (!check || typeof check !== "object") return String(check);
  if (check.kind === "file") return `파일 ${[check.path, check.pattern].filter(Boolean).join(" ")}`.trim();
  if (check.kind === "human") return "사람 확인";
  if (check.kind === "element") {
    const requirements = Array.isArray(check.require) ? check.require : [check.require || {}];
    return requirements.map((item) => `화면 요소 ${[item.role, item.name || item.name_contains || item.description_contains].filter(Boolean).join(" ")}`.trim()).join(" + ");
  }
  return check.kind || "";
}

function knowledgeSource(entry) {
  const source = entry.source || {};
  const rows = [
    ["실행", source.run_id],
    ["출처", ORIGIN_LABELS[source.origin] ? `${ORIGIN_LABELS[source.origin]}${Number.isInteger(source.stage) ? ` · 단계 ${source.stage}` : ""}` : null],
    ["날짜", source.date],
    ["질문", source.question],
    ["답", source.answer],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (rows.length === 0) return el("span");
  return el("details", { className: "knowledge-source" }, [el("summary", { text: "근거" }), factList(rows)]);
}

function knowledgeActions(entry) {
  const edit = el("button", { text: "글 고치기", attrs: { type: "button" } });
  edit.addEventListener("click", () => {
    knowledge.editing = entry.id;
    knowledge.confirming = null;
    knowledge.message = null;
    renderKnowledge();
  });
  const disabled = entry.status === "disabled";
  const toggle = el("button", { text: disabled ? "다시 쓰기" : "사용 멈추기", attrs: { type: "button" } });
  toggle.addEventListener("click", () => saveKnowledge(entry, { status: disabled ? "active" : "disabled" },
    disabled ? "다시 사용합니다." : "사용을 멈췄습니다. 다음 실행부터 모델에게 주지 않습니다."));
  const buttons = [edit, toggle];
  if (knowledge.confirming === entry.id) {
    const yes = el("button", { className: "reject", text: "정말 지우기", attrs: { type: "button" } });
    const no = el("button", { text: "취소", attrs: { type: "button" } });
    yes.addEventListener("click", () => deleteKnowledge(entry));
    no.addEventListener("click", () => {
      knowledge.confirming = null;
      renderKnowledge();
    });
    return el("div", { className: "knowledge-actions confirming" }, [
      ...buttons,
      el("span", { className: "knowledge-confirm", text: `${entry.id} 파일을 지웁니다. 되돌릴 수 없습니다.` }),
      yes,
      no,
    ]);
  }
  const remove = el("button", { className: "danger", text: "지우기", attrs: { type: "button" } });
  remove.addEventListener("click", () => {
    knowledge.confirming = entry.id;
    renderKnowledge();
  });
  return el("div", { className: "knowledge-actions" }, [...buttons, remove]);
}

function knowledgeEditor(entry) {
  const fields = entry.type === "completion" ? [["task", "작업"]] : [["situation", "상황"], ["resolution", "해결"]];
  const areas = fields.map(([key, label]) => {
    const area = el("textarea", { attrs: { rows: key === "resolution" ? "3" : "2", maxlength: "2000", "aria-label": label, "data-field": key } });
    area.value = entry[key] || "";
    return [key, label, area];
  });
  const message = el("p", { className: "knowledge-message", attrs: { role: "status", "aria-live": "polite" } });
  const save = el("button", { className: "primary", text: "저장", attrs: { type: "button" } });
  const cancel = el("button", { text: "취소", attrs: { type: "button" } });
  save.addEventListener("click", async () => {
    const changes = {};
    for (const [key, , area] of areas) if (area.value.trim() !== (entry[key] || "")) changes[key] = area.value.trim();
    if (Object.keys(changes).length === 0) {
      knowledge.editing = null;
      renderKnowledge();
      return;
    }
    save.disabled = true;
    message.textContent = "저장하는 중…";
    const outcome = await saveKnowledge(entry, changes, "고친 글을 저장했습니다.", true);
    if (!outcome.ok) {
      save.disabled = false;
      message.textContent = outcome.error;
      message.className = "knowledge-message error";
    }
  });
  cancel.addEventListener("click", () => {
    knowledge.editing = null;
    renderKnowledge();
  });
  const notes = entry.type === "completion" ? [el("p", { className: "hint", text: "확인 방법은 파일에서 고칩니다." })] : [];
  return el("div", { className: "knowledge-editor" }, [
    ...areas.map(([, label, area]) => el("label", { className: "field" }, [el("span", { text: label }), area])),
    el("p", { className: "hint", text: "화면 좌표 대신 요소의 역할, 이름, 부분, 상대 위치로 적어 주세요." }),
    ...notes,
    el("div", { className: "knowledge-actions" }, [save, cancel]),
    message,
  ]);
}

async function saveKnowledge(entry, changes, done, fromEditor = false) {
  const reply = await knowledgeRequest(entryPath(knowledge.system, entry.id), { changes });
  if (!reply.ok) {
    const error = reply.body.error || `저장하지 못했습니다 (${reply.status})`;
    if (!fromEditor) {
      knowledge.message = { kind: "error", text: error };
      renderKnowledge();
    }
    return { ok: false, error };
  }
  knowledge.editing = null;
  knowledge.message = { kind: "ok", text: `${entry.id}: ${done}` };
  await loadKnowledgeEntries();
  return { ok: true };
}

async function deleteKnowledge(entry) {
  const reply = await knowledgeRequest(`${entryPath(knowledge.system, entry.id)}/delete`, {});
  knowledge.confirming = null;
  knowledge.message = reply.ok
    ? { kind: "ok", text: `${entry.id}를 지웠습니다.` }
    : { kind: "error", text: reply.body.error || `지우지 못했습니다 (${reply.status})` };
  await loadKnowledgeEntries();
}
