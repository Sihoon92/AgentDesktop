"use strict";

/* Questions, answers, registration and completion in the viewer: the chat panel, the answer form,
   the candidate boxes over the question's frame, the pin a person drops on that frame, and the
   expected/observed change of each action. app.js owns the state, the DOM helpers and rendering
   and calls into the functions here; nothing here runs before app.js has started. */

const QUESTION_KIND_LABELS = { confirm: "누르기 전 확인", stuck: "막힘", completion: "완료 확인", registration: "등록 승인" };
const EXPECT_LABELS = {
  window_opened: "새 창이 뜸",
  window_closed: "창이 닫힘",
  content_changed: "화면 내용이 바뀜",
  value_changed: "값이 바뀜",
  any: "무엇이든",
};
const COMPLETION_BY_LABELS = { task: "작업 파일의 조건", knowledge: "지식의 완료 조건", human: "사람이 확인" };
const ITEM_DECISION_LABELS = { approve: "승인", edit: "고쳐서 승인", reject: "거절" };
const EFFECT_NAMES = 3;
const ANSWER_MAX_CHARS = 2000;

function circled(n) {
  return Number.isInteger(n) && n >= 1 && n <= 20 ? String.fromCharCode(0x2460 + n - 1) : `(${n})`;
}

function questionKindLabel(kind) {
  return QUESTION_KIND_LABELS[kind] || kind || "질문";
}

function pendingQuestion() {
  if (!isLive()) return null;
  return state.entries.find((entry) => entry.kind === "question" && !entry.answer) || null;
}

function canAnswerHere() {
  const status = state.status;
  return Boolean(status && status.can_answer && status.active === state.runId);
}

function questionState(entry) {
  const { answer } = entry;
  if (answer && answer.timed_out) return { text: "답 없음 (시간이 지남)", className: "bad-text", suffix: " · 답 없음", bad: true };
  if (answer) return { text: "답함", className: "ok-text", suffix: " · 답함", bad: false };
  if (isLive()) return { text: "답을 기다리는 중", className: "warn-text", suffix: " · 대기", bad: false };
  return { text: "답 없음", className: "", suffix: " · 답 없음", bad: true };
}

function questionTimelineLabel(entry) {
  return `질문 ${questionKindLabel(entry.question.kind)}${questionState(entry).suffix}`;
}

/* ---------- step information ---------- */

function effectsSummary(effects) {
  if (!effects || typeof effects !== "object") return null;
  if (effects.none) return "변화 없음";
  const names = (list) => {
    const shown = (list || []).slice(0, EFFECT_NAMES).filter(Boolean);
    return shown.length ? ` (${shown.join(", ")}${(list || []).length > shown.length ? " …" : ""})` : "";
  };
  const quote = (value) => (value === null || value === undefined ? "없음" : `"${value}"`);
  const parts = [];
  for (const window of effects.windows_opened || []) parts.push(`새 창 "${window.title || window.id}"`);
  for (const window of effects.windows_closed || []) parts.push(`닫힌 창 "${window.title || window.id}"`);
  if (effects.added && effects.added.count) parts.push(`생김 ${effects.added.count}개${names(effects.added.names)}`);
  if (effects.removed && effects.removed.count) parts.push(`사라짐 ${effects.removed.count}개${names(effects.removed.names)}`);
  for (const change of effects.changed || []) {
    parts.push(`${change.name || `표시 ${change.mark}`}: ${change.field || "값"} ${quote(change.before)} → ${quote(change.after)}`);
  }
  if (effects.focus && (effects.focus.name || effects.focus.mark)) parts.push(`포커스 → ${effects.focus.name || `표시 ${effects.focus.mark}`}`);
  return parts.length ? parts.join(" · ") : "변화 없음";
}

const REFUSED_CODE_LABELS = {
  needs_confirmation: "누르기 전 확인이 필요함",
  blocked_until_question: "기대와 다른 결과가 이어져 질문이 먼저 필요함",
  unknown_knowledge: "없는 지식 id",
  stale_capture: "캡처 뒤 창이 움직임",
  no_one_to_ask: "답할 사람이 없음",
};

function expectationRows(action) {
  if (!action) return [];
  const mismatch = action.expected_met === false;
  const refused = action.refused_code ? `${REFUSED_CODE_LABELS[action.refused_code] || action.refused_code} (${action.refused_code})` : null;
  return [
    ["거절 이유", refused, "bad-text"],
    ["기대한 변화", action.expect === undefined || action.expect === null ? null : EXPECT_LABELS[action.expect] || action.expect],
    ["일어난 변화", effectsSummary(action.effects)],
    ["기대 판정", mismatch ? el("span", { className: "mismatch-badge", text: "기대와 다름" }) : action.expected_met === true ? "기대대로" : null,
      mismatch ? "" : "ok-text"],
    ["모델에게 보낸 캡처", action.capture || null],
  ];
}

function knowledgeNotes(entry) {
  const nodes = [];
  for (const used of entry.knowledgeUsed || []) {
    nodes.push(el("div", { className: "knowledge-used" }, [
      el("p", { className: "knowledge-title", text: "지난 답을 참고함" }),
      el("p", { className: "knowledge-text", text: used.text || "" }),
      el("p", { className: "knowledge-id", text: used.id || "" }),
    ]));
  }
  for (const failed of entry.knowledgeFailed || []) {
    nodes.push(el("p", { className: "knowledge-failed", text: `참고한 지식(${failed.id})대로 했지만 기대와 달랐습니다` }));
  }
  return nodes;
}

function candidateList(question) {
  const candidates = question.candidates || [];
  if (candidates.length === 0) return null;
  return el("ol", { className: "candidates" }, candidates.map((candidate) => {
    const proposed = candidate.n === question.proposed;
    return el("li", { className: proposed ? "candidate proposed" : "candidate" }, [
      el("span", { className: "candidate-num", text: circled(candidate.n) }),
      el("span", { className: "candidate-label", text: candidate.label || "" }),
      proposed ? el("span", { className: "candidate-tag", text: "제안" }) : null,
      Number.isInteger(candidate.mark) ? el("span", { className: "candidate-mark", text: `표시 ${candidate.mark}` }) : null,
      !candidate.rect && candidate.image_point ? el("span", { className: "candidate-mark", text: "지점" }) : null,
    ]);
  }));
}

function answerText(answer, question) {
  if (!answer) return null;
  if (answer.timed_out) return "답 없음";
  const parts = [];
  if (answer.accepted === true) parts.push("맞음");
  if (answer.accepted === false) parts.push("아님");
  if (question && question.kind === "registration") parts.push(itemsSummary(answer.items));
  if (answer.text) parts.push(answer.text);
  if (answer.pin) parts.push("📍 찍은 위치");
  return parts.filter(Boolean).join(" · ") || "(글 없음)";
}

function itemsSummary(items) {
  const counts = { approve: 0, edit: 0, reject: 0 };
  for (const item of items || []) if (item && counts[item.decision] !== undefined) counts[item.decision] += 1;
  return `승인 ${counts.approve} · 고쳐서 승인 ${counts.edit} · 거절 ${counts.reject}`;
}

function questionDetails(entry) {
  const { question, answer } = entry;
  const info = questionState(entry);
  const nodes = [
    el("div", { className: "step-head" }, [
      el("h2", { text: `#${state.index + 1} 질문` }),
      el("span", { className: "kind question-kind", text: questionKindLabel(question.kind) }),
    ]),
    el("p", { className: "intent question-intent", text: question.text || "" }),
    factList([
      ["상태", info.text, info.className],
      ["답", answerText(answer, question)],
      ["모델 스텝", question.step],
      ["관찰", entry.observation ? `관찰 ${entry.observation.id}` : "없음"],
      ["근거 표시", (question.basis || []).length ? `${question.basis.length}개 (노란 칸)` : null],
    ]),
    candidateList(question),
  ];
  if (question.kind === "registration") nodes.push(registrationItemsView(question, answer));
  if (!answer && isLive()) {
    nodes.push(el("p", { className: "hint", text: canAnswerHere() ? "오른쪽 아래 대화 칸에서 답하세요." : "이 실행은 터미널에서 답합니다." }));
  }
  return nodes.filter(Boolean);
}

function describeEntry(entry) {
  if (!entry || typeof entry !== "object") return "";
  if (entry.type === "completion") {
    const checks = (entry.checks || []).map((check) => check && check.kind).filter(Boolean).join(", ");
    return `완료 조건: ${entry.task || ""}${checks ? ` (확인: ${checks})` : ""}`;
  }
  return `상황: ${entry.situation || ""} → 해결: ${entry.resolution || ""}`;
}

function registrationItemsView(question, answer) {
  const decided = new Map(((answer && answer.items) || []).map((item) => [item.n, item]));
  return el("ol", { className: "registration-items" }, (question.items || []).map((item) => {
    const decision = decided.get(item.n);
    const head = `${item.action === "update" ? `고침 ${item.id || ""}` : "새 항목"}`.trim();
    return el("li", { className: "registration-item" }, [
      el("span", { className: "registration-head", text: `${item.n}. ${head}` }),
      el("span", { className: "registration-body", text: describeEntry(item.entry) }),
      decision ? el("span", { className: `registration-decision ${decision.decision}`, text: ITEM_DECISION_LABELS[decision.decision] || decision.decision }) : null,
      decision && decision.entry ? el("span", { className: "registration-body edited", text: `고친 내용 · ${describeEntry(decision.entry)}` }) : null,
    ]);
  }));
}

/* ---------- overlay ---------- */

function questionShapes(entry, observation) {
  if (!entry || entry.kind !== "question" || !observation || entry.observation !== observation) return [];
  const { question } = entry;
  const shapes = [];
  for (const box of question.basis || []) {
    if (box.rect) shapes.push({ layer: "qbasis", rect: box.rect, item: { kind: "question", role: "근거", name: box.mark ? `표시 ${box.mark}` : "" } });
  }
  for (const candidate of question.candidates || []) {
    const proposed = candidate.n === question.proposed;
    const layer = proposed ? "qtarget" : "qcandidate";
    const item = { kind: "question", role: proposed ? "제안한 후보" : "후보", name: candidate.label || "" };
    if (candidate.rect) shapes.push({ layer, rect: candidate.rect, item, label: circled(candidate.n) });
    else if (candidate.image_point) shapes.push({ layer, cross: candidate.image_point, item, label: circled(candidate.n) });
  }
  const pin = shownPin(entry);
  if (pin) shapes.push({ layer: "pin", cross: pin, item: { kind: "question", role: "찍은 위치", name: `(${pin[0]}, ${pin[1]})` }, label: "📍" });
  return shapes;
}

function shownPin(entry) {
  if (entry.answer && entry.answer.pin && Array.isArray(entry.answer.pin.image_point)) return entry.answer.pin.image_point;
  const pin = state.pin;
  if (!entry.answer && pin && pin.questionId === entry.question.id && pin.runId === state.runId) return [pin.x, pin.y];
  return null;
}

function appendCross(parent, shape) {
  const [cx, cy] = shape.cross;
  const size = 9;
  const className = `cross ${shape.layer}`;
  parent.append(svg("line", { x1: cx - size, y1: cy, x2: cx + size, y2: cy, class: "halo", "stroke-width": 5 }));
  parent.append(svg("line", { x1: cx, y1: cy - size, x2: cx, y2: cy + size, class: "halo", "stroke-width": 5 }));
  parent.append(svg("line", { x1: cx - size, y1: cy, x2: cx + size, y2: cy, class: className }));
  parent.append(svg("line", { x1: cx, y1: cy - size, x2: cx, y2: cy + size, class: className }));
  const dot = svg("circle", { cx, cy, r: 5, class: `${className} cross-dot` });
  overlayItems.set(dot, shape.item);
  parent.append(dot);
}

function pinnable() {
  const pending = pendingQuestion();
  const entry = state.entries[state.index];
  return Boolean(pending && entry === pending && canAnswerHere() && pending.observation && pending.observation.image
    && pending.question.kind !== "registration" && pending.question.kind !== "completion" && !state.sentAnswers.has(pending.question.id));
}

function onStageClick(event) {
  if (!pinnable()) return;
  const entry = state.entries[state.index];
  const observation = entry.observation;
  const image = $("frame").getBoundingClientRect();
  if (!image.width || !image.height) return;
  const offsetX = event.clientX - image.left;
  const offsetY = event.clientY - image.top;
  if (offsetX < 0 || offsetY < 0 || offsetX > image.width || offsetY > image.height) return;
  const { width, height } = observation.image;
  state.pin = {
    runId: state.runId,
    questionId: entry.question.id,
    observation: observation.id,
    x: Math.min(width - 1, Math.max(0, Math.floor((offsetX * width) / image.width))),
    y: Math.min(height - 1, Math.max(0, Math.floor((offsetY * height) / image.height))),
  };
  renderStage();
  renderPinChip();
}

/* ---------- chat log ---------- */

function chatRecords(events) {
  const questions = new Map();
  const records = [];
  for (const event of events) {
    if (event.type === "question") {
      const record = { kind: "question", question: event, answer: null };
      questions.set(event.id, record);
      records.push(record);
    } else if (event.type === "answer") {
      const record = questions.get(event.id);
      if (record) record.answer = event;
      records.push({ kind: "answer", answer: event, question: record ? record.question : null });
    } else if (["completion", "registration", "knowledge_loaded", "warning"].includes(event.type)) {
      records.push({ kind: event.type, event });
    }
  }
  return records;
}

function chatMessage(record) {
  if (record.kind === "question") return questionMessage(record);
  if (record.kind === "answer") {
    const { answer, question } = record;
    const pin = answer.pin && !answer.timed_out ? el("span", { className: "pin-chip", text: pinChipText(answer.pin) }) : null;
    const decisions = question && question.kind === "registration" ? registrationDecisionLines(question, answer) : null;
    return el("li", { className: answer.timed_out ? "chat-msg answer timed-out" : "chat-msg answer" }, [
      el("span", { className: "chat-who", text: answer.timed_out ? "답 없음" : "내 답" }),
      el("p", { className: "chat-text", text: answer.timed_out ? "시간이 지나 실행이 멈췄습니다." : answerText(answer, question) }),
      decisions,
      pin,
    ]);
  }
  if (record.kind === "completion") {
    const { event } = record;
    const by = COMPLETION_BY_LABELS[event.by] || event.by || "";
    return systemMessage(`완료 판정 · ${by} · ${event.met ? "성공" : "아직 아님"}${event.detail ? ` — ${event.detail}` : ""}`, event.met ? "ok" : "bad");
  }
  if (record.kind === "registration") {
    const { event } = record;
    const saved = (event.saved || []).map((item) => `${item.id} (${item.action === "update" ? "고침" : "새 항목"})`);
    const text = saved.length ? `등록함: ${saved.join(", ")}` : "등록한 항목 없음";
    return systemMessage(`${text}${event.rejected ? ` · 거절 ${event.rejected}` : ""}`, saved.length ? "ok" : "");
  }
  const { event } = record;
  if (record.kind === "warning") return systemMessage(`경고 · ${event.message || ""}`, "warn");
  const count = (event.ids || []).length;
  return systemMessage(`지식 ${count}개를 불러옴${event.system ? ` · ${event.system}` : ""}${event.truncated ? " · 일부만 줌" : ""}`, "");
}

function pinChipText(pin) {
  const point = pin.image_point || pin.point;
  return Array.isArray(point) ? `📍 찍은 위치 (${point[0]}, ${point[1]})` : "📍 찍은 위치";
}

function registrationDecisionLines(question, answer) {
  const items = new Map((question.items || []).map((item) => [item.n, item]));
  return el("ul", { className: "chat-items" }, (answer.items || []).map((decision) => {
    const item = items.get(decision.n);
    const entry = decision.entry || (item && item.entry);
    return el("li", { className: `chat-item ${decision.decision}` }, [
      el("span", { className: "chat-item-decision", text: `${decision.n}. ${ITEM_DECISION_LABELS[decision.decision] || decision.decision}` }),
      el("span", { text: entry ? describeEntry(entry) : "" }),
    ]);
  }));
}

function questionMessage(record) {
  const { question } = record;
  const jump = el("button", { className: "chat-jump", text: "캡처에서 보기", attrs: { type: "button" } });
  jump.addEventListener("click", () => {
    const index = state.entries.findIndex((entry) => entry.kind === "question" && entry.question.id === question.id);
    if (index >= 0) select(index);
  });
  const items = question.kind === "registration"
    ? el("ol", { className: "chat-items" }, (question.items || []).map((item) => el("li", { text: `${item.n}. ${describeEntry(item.entry)}` })))
    : null;
  return el("li", { className: `chat-msg question ${record.answer ? "" : "open"}`.trim() }, [
    el("span", { className: "chat-who", text: `질문 · ${questionKindLabel(question.kind)}${question.step !== undefined ? ` · 스텝 ${question.step}` : ""}` }),
    el("p", { className: "chat-text", text: question.text || "" }),
    candidateList(question),
    items,
    jump,
  ]);
}

function systemMessage(text, tone) {
  return el("li", { className: `chat-msg system ${tone || ""}`.trim() }, [el("p", { className: "chat-text", text })]);
}

function renderChat() {
  const records = chatRecords(state.events);
  const pending = pendingQuestion();
  const chat = $("chat");
  chat.hidden = records.length === 0 && !pending;
  if (chat.hidden) {
    state.chatShown = null;
    $("chat-composer").replaceChildren();
    state.composerKey = null;
    return;
  }
  const logKey = `${state.runId}|${state.events.length}|${isLive()}`;
  if (state.chatShown !== logKey) {
    state.chatShown = logKey;
    const log = $("chat-log");
    log.replaceChildren(...records.map(chatMessage));
    log.scrollTop = log.scrollHeight;
  }
  renderComposer(pending);
}

/* ---------- answer form ---------- */

function renderComposer(pending) {
  const box = $("chat-composer");
  const answerable = canAnswerHere();
  const sent = pending ? state.sentAnswers.has(pending.question.id) : false;
  const key = pending ? `${state.runId}|${pending.question.id}|${answerable}|${sent}` : "";
  if (key === state.composerKey) {
    renderPinChip();
    return;
  }
  state.composerKey = key;
  if (!pending) {
    box.replaceChildren();
    return;
  }
  if (!answerable) {
    box.replaceChildren(el("div", { className: "composer locked" }, [
      el("p", { className: "composer-note", text: "이 실행은 터미널에서 답합니다." }),
      el("textarea", { attrs: { rows: "2", disabled: "", "aria-label": "답", placeholder: "이 뷰어에서는 답할 수 없습니다" } }),
      el("button", { className: "primary", text: "보내기", attrs: { type: "button", disabled: "" } }),
    ]));
    return;
  }
  if (sent) {
    box.replaceChildren(el("p", { className: "composer-note", text: "보냈습니다. 실행이 이어지기를 기다리는 중…" }));
    return;
  }
  const { question } = pending;
  if (question.kind === "completion") box.replaceChildren(completionComposer(question));
  else if (question.kind === "registration") box.replaceChildren(registrationComposer(question));
  else box.replaceChildren(textComposer(question));
}

function composerMessage() {
  return el("p", { className: "composer-message", attrs: { role: "status", "aria-live": "polite" } });
}

function draftArea(question, placeholder) {
  const area = el("textarea", { attrs: { rows: "2", maxlength: String(ANSWER_MAX_CHARS), placeholder, "aria-label": "답" } });
  area.value = state.answerDrafts.get(question.id) || "";
  area.addEventListener("input", () => state.answerDrafts.set(question.id, area.value));
  return area;
}

function textComposer(question) {
  const area = draftArea(question, question.kind === "confirm" ? "예: 맞아, 2번, 아니 아래쪽 것" : "예: 3번, 오른쪽 끝 화살표");
  const send = el("button", { className: "primary", text: "보내기", attrs: { type: "button" } });
  const message = composerMessage();
  const quick = [];
  if (question.kind === "confirm") quick.push("맞아");
  for (const candidate of question.candidates || []) quick.push(`${candidate.n}번`);
  const quickRow = el("div", { className: "quick-answers", attrs: { role: "group", "aria-label": "빠른 답" } }, quick.map((text) => {
    const button = el("button", { className: "quick", text, attrs: { type: "button" } });
    button.addEventListener("click", () => {
      area.value = text;
      state.answerDrafts.set(question.id, text);
      area.focus();
    });
    return button;
  }));
  const submit = async () => {
    const pin = state.pin && state.pin.questionId === question.id && state.pin.runId === state.runId ? state.pin : null;
    const text = area.value.trim() || (pin ? "찍은 위치" : "");
    if (!text) {
      message.textContent = "답을 적거나 캡처에서 위치를 찍어 주세요.";
      message.dataset.kind = "error";
      area.focus();
      return;
    }
    const body = { question_id: question.id, text };
    if (pin) body.pin = { observation: pin.observation, x: pin.x, y: pin.y };
    await submitAnswer(question, body, [send, area], message);
  };
  send.addEventListener("click", submit);
  area.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submit();
    }
  });
  return el("div", { className: "composer", attrs: { role: "group", "aria-label": "질문에 답하기" } }, [
    el("p", { className: "composer-note", text: `${questionKindLabel(question.kind)} 질문에 답하세요. 캡처를 클릭하면 위치를 찍어 답에 붙입니다.` }),
    quick.length ? quickRow : null,
    el("div", { className: "pin-slot", attrs: { id: "pin-chip-slot" } }),
    area,
    el("div", { className: "composer-buttons" }, [send]),
    message,
  ]);
}

function renderPinChip() {
  const slot = document.getElementById("pin-chip-slot");
  if (!slot) return;
  const pin = state.pin;
  const pending = pendingQuestion();
  if (!pin || !pending || pin.questionId !== pending.question.id || pin.runId !== state.runId) {
    slot.replaceChildren();
    return;
  }
  const remove = el("button", { className: "pin-remove", text: "×", attrs: { type: "button", "aria-label": "찍은 위치 지우기" } });
  remove.addEventListener("click", () => {
    state.pin = null;
    renderPinChip();
    renderStage();
  });
  slot.replaceChildren(el("span", { className: "pin-chip" }, [el("span", { text: `📍 찍은 위치 (${pin.x}, ${pin.y})` }), remove]));
}

function completionComposer(question) {
  const area = draftArea(question, "의견 (선택, 아니라면 무엇이 다른지 적으면 모델이 읽습니다)");
  const yes = el("button", { className: "approve", text: "맞음", attrs: { type: "button" } });
  const no = el("button", { className: "reject", text: "아님", attrs: { type: "button" } });
  const message = composerMessage();
  const submit = (accepted) => submitAnswer(question, { question_id: question.id, text: area.value.trim(), accepted }, [yes, no, area], message);
  yes.addEventListener("click", () => submit(true));
  no.addEventListener("click", () => submit(false));
  return el("div", { className: "composer", attrs: { role: "group", "aria-label": "결과 확인" } }, [
    el("p", { className: "composer-note", text: "결과가 맞나요?" }),
    area,
    el("div", { className: "approval-buttons" }, [yes, no]),
    message,
  ]);
}

function registrationDraft(question) {
  if (!state.registrationDrafts.has(question.id)) state.registrationDrafts.set(question.id, new Map());
  return state.registrationDrafts.get(question.id);
}

function editableField(entry) {
  return entry && entry.type === "completion" ? "task" : "resolution";
}

function registrationDecisions(question) {
  const draft = registrationDraft(question);
  const decisions = [];
  for (const item of question.items || []) {
    const choice = draft.get(item.n);
    if (!choice || !choice.decision) return null;
    if (choice.decision === "edit") {
      const text = (choice.text || "").trim();
      if (!text) return null;
      decisions.push({ n: item.n, decision: "edit", entry: { ...(item.entry || {}), [editableField(item.entry)]: text } });
    } else {
      decisions.push({ n: item.n, decision: choice.decision });
    }
  }
  return decisions;
}

function registrationComposer(question) {
  const draft = registrationDraft(question);
  const send = el("button", { className: "primary", text: "보내기", attrs: { type: "button" } });
  const message = composerMessage();
  const refresh = () => {
    send.disabled = registrationDecisions(question) === null;
  };
  const rows = (question.items || []).map((item) => {
    const choice = draft.get(item.n) || { decision: null, text: (item.entry || {})[editableField(item.entry)] || "" };
    draft.set(item.n, choice);
    const edit = el("textarea", { attrs: { rows: "2", maxlength: String(ANSWER_MAX_CHARS), "aria-label": `${item.n}번 고친 내용` } });
    edit.value = choice.text;
    edit.hidden = choice.decision !== "edit";
    edit.addEventListener("input", () => {
      choice.text = edit.value;
      refresh();
    });
    const buttons = Object.entries(ITEM_DECISION_LABELS).map(([decision, label]) => {
      const button = el("button", { className: `decision ${decision}`, text: label, attrs: { type: "button", "aria-pressed": String(choice.decision === decision) } });
      button.addEventListener("click", () => {
        choice.decision = decision;
        for (const other of buttons) other.setAttribute("aria-pressed", String(other === button));
        edit.hidden = decision !== "edit";
        if (decision === "edit") edit.focus();
        refresh();
      });
      return button;
    });
    const head = item.action === "update" ? `고침 ${item.id || ""}` : "새 항목";
    return el("li", { className: "registration-choice" }, [
      el("p", { className: "registration-head", text: `${item.n}. ${head}` }),
      el("p", { className: "registration-body", text: describeEntry(item.entry) }),
      el("div", { className: "decision-buttons", attrs: { role: "group", "aria-label": `${item.n}번 결정` } }, buttons),
      edit,
    ]);
  });
  send.addEventListener("click", () => {
    const items = registrationDecisions(question);
    if (!items) return;
    submitAnswer(question, { question_id: question.id, text: "", items }, [send], message);
  });
  refresh();
  return el("div", { className: "composer", attrs: { role: "group", "aria-label": "등록 승인" } }, [
    el("p", { className: "composer-note", text: "항목마다 승인, 고쳐서 승인, 거절을 고르세요. 승인한 것만 등록됩니다." }),
    el("ol", { className: "registration-choices" }, rows),
    el("div", { className: "composer-buttons" }, [send]),
    message,
  ]);
}

async function submitAnswer(question, body, controls, message) {
  for (const control of controls) control.disabled = true;
  message.textContent = "보내는 중…";
  message.dataset.kind = "";
  const outcome = await sendAnswer(body);
  message.textContent = outcome.message;
  message.dataset.kind = outcome.ok ? "" : "error";
  if (!outcome.ok) {
    for (const control of controls) control.disabled = false;
    return;
  }
  state.sentAnswers.add(question.id);
  state.answerDrafts.delete(question.id);
  state.registrationDrafts.delete(question.id);
  if (state.pin && state.pin.questionId === question.id) state.pin = null;
  render();
}

async function sendAnswer(body) {
  let response;
  try {
    response = await fetch(`/api/runs/${encodeURIComponent(state.runId)}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Agent-Token": state.token || "" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch (error) {
    return { ok: false, message: `서버에 연결하지 못했습니다: ${error.message}` };
  }
  const reply = await response.json().catch(() => ({}));
  if (response.ok) return { ok: true, message: "보냈습니다. 실행이 이어집니다." };
  return { ok: false, message: reply.error || `보내지 못했습니다 (${response.status})` };
}

/* ---------- following the open question ---------- */

function followPendingQuestion() {
  const pending = pendingQuestion();
  if (!pending || state.jumpedQuestion === `${state.runId}|${pending.question.id}`) return false;
  state.jumpedQuestion = `${state.runId}|${pending.question.id}`;
  if (!canAnswerHere()) loadStatus();
  select(state.entries.indexOf(pending));
  return true;
}

function updateTitle() {
  const base = state.baseTitle || document.title;
  document.title = pendingQuestion() ? `(질문) ${base}` : base;
}
