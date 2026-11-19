"use strict";

/* The registration view: an administrator reads a system's screen and registers its ready
   signals and the names of picture-only elements (the system profile).

   Pick a system the settings name, launch it (or attach to it), pick one of its windows, then read
   the screen now or after PROFILE_READ_DELAY_S seconds (the server waits, so a menu opened in the
   app meanwhile is read open). The picture comes with a box per element; a box or a row gives
   "준비 신호로". The table lists the elements with an id and no name, each with its piece of the
   picture, its role, id and area, and a name box: a name registers it, an empty box drops it.
   Names given on other screens stay as they are. A name typed and not yet saved stays through
   another read: its row shows it again, and a row the new read lacks is still saved (the server
   checks a name only against the elements of the latest read, so one that is not there passes),
   which the view says next to "저장". Changing the system or launching again forgets them.
   "지금 확인" times both ways of reading the signals and shows the faster one; the choice of
   "읽는 방식" stays the administrator's. "저장" writes the profile, and what keeps it from being
   saved is shown on its row; the outcome of "지금 확인" and "저장" is shown next to the buttons.

   Every text here came from a screen or the profile file, so it is only ever set as textContent.
   A row's piece of the picture is drawn at the picture's own resolution and shown at most
   PROFILE_CROP_SHOWN_W by PROFILE_CROP_SHOWN_H (style.css says the same), so rows stay low; a
   piece shrunk to fit opens at its own size while the pointer is on its row or its name box has
   the focus. Leaving for another view and coming back keeps everything (the read, the names, the
   signals and the choices); only changing the system reads the saved profile again.

   app.js owns the DOM helpers, the token and the status; knowledge.js owns knowledgeRequest() and
   the knowledge view; workflows.js the workflow view. This view takes turns with them. */

const PROFILE_READ_DELAY_S = 3;
const PROFILE_READ_MODES = [["snapshot", "snapshot (트리를 통째로 읽음)"], ["find", "find (신호마다 묻기)"]];
const PROFILE_CROP_SHOWN_W = 150;
const PROFILE_CROP_SHOWN_H = 48;

const profilesView = {
  available: false,
  shown: false,
  system: null,
  saved: null,
  exists: false,
  windows: [],
  windowId: null,
  read: null,
  imageUrl: null,
  picture: null,
  selected: null,
  hovered: null,
  ready: [],
  names: {},
  seen: {},
  stableIds: false,
  readMode: "snapshot",
  check: null,
  problems: [],
  message: null,
  busy: false,
  countdown: 0,
  seq: 0,
  loadedFor: null,
};

function initProfiles() {
  const button = $("show-profiles");
  if (state.exportMode || !state.token || !state.status || state.status.can_register !== true) {
    button.hidden = true;
    return;
  }
  profilesView.available = true;
  button.hidden = false;
  $("view-switch").hidden = false;
  button.addEventListener("click", () => showProfiles(true));
  $("show-runs").addEventListener("click", () => showProfiles(false, true));
  $("show-knowledge").addEventListener("click", () => showProfiles(false));
  $("show-workflows").addEventListener("click", () => showProfiles(false));
}

async function showProfiles(shown, toRuns = false) {
  if (!profilesView.available) return;
  if (!shown && !profilesView.shown) return;
  profilesView.shown = shown;
  document.body.classList.toggle("profile-mode", shown);
  $("profile-view").hidden = !shown;
  $("show-profiles").setAttribute("aria-pressed", String(shown));
  if (shown) {
    if (typeof knowledge !== "undefined" && knowledge.shown) await showKnowledge(false);
    if (typeof workflowsView !== "undefined" && workflowsView.shown) await showWorkflows(false, true);
    for (const id of ["show-runs", "show-knowledge", "show-workflows"]) $(id).setAttribute("aria-pressed", "false");
    if (!profilesView.system) profilesView.system = profileSystems()[0] || null;
    history.replaceState(null, "", `${location.pathname}#register`);
    if (profilesView.system && profilesView.loadedFor === profilesView.system) renderProfiles();
    else await loadProfile();
    return;
  }
  if (!toRuns) return;
  $("show-runs").setAttribute("aria-pressed", "true");
  const run = state.runId ? `#${state.runKind === "legacy" ? "legacy" : "run"}=${encodeURIComponent(state.runId)}` : "";
  history.replaceState(null, "", location.pathname + run);
  render();
}

function profileSystems() {
  const names = state.status && Array.isArray(state.status.systems) ? state.status.systems : [];
  const choice = state.status ? state.status.default_choice : null;
  const listed = names.filter((name) => typeof name === "string");
  return listed.includes(choice) ? [choice, ...listed.filter((name) => name !== choice)] : listed;
}

async function loadProfile() {
  const seq = ++profilesView.seq;
  const system = profilesView.system;
  if (!system) {
    renderProfiles();
    return;
  }
  const reply = await knowledgeRequest(`/api/profile?system=${encodeURIComponent(system)}`);
  if (seq !== profilesView.seq) return;
  if (reply.ok) {
    applySavedProfile(reply.body.profile || {}, reply.body.exists === true);
    profilesView.loadedFor = system;
  } else {
    profilesView.saved = null;
    profilesView.loadedFor = null;
    profilesView.message = { kind: "error", text: reply.body.error || `프로파일을 읽지 못했습니다 (${reply.status})` };
  }
  renderProfiles();
}

function applySavedProfile(profile, exists) {
  profilesView.saved = profile;
  profilesView.exists = exists;
  profilesView.ready = (profile.ready || []).map((signal) => ({ ...signal }));
  profilesView.stableIds = profile.stable_ids === true;
  profilesView.readMode = profile.ready_read === "find" ? "find" : "snapshot";
  profilesView.check = null;
  profilesView.problems = [];
  fillNames();
}

function clearRead() {
  if (profilesView.imageUrl) URL.revokeObjectURL(profilesView.imageUrl);
  Object.assign(profilesView, { read: null, imageUrl: null, picture: null, selected: null, hovered: null,
    check: null, problems: [] });
}

function resetRead() {
  clearRead();
  profilesView.names = {};
  profilesView.seen = {};
}

/* --- what a read gives ------------------------------------------------------------------- */

function sameArea(left, right) {
  if (!left || !right) return !left && !right;
  return left.role === right.role && left.id === right.id && left.name === right.name;
}

function rowKey(item) {
  return JSON.stringify([item.role, item.id, item.in || null]);
}

function idRows() {
  const items = profilesView.read ? profilesView.read.items || [] : [];
  return items.filter((item) => item.id && !(typeof item.name === "string" && item.name.trim()));
}

function entryOfRow(entry, row) {
  return entry.role === row.role && entry.id === row.id && (entry.in === undefined || sameArea(entry.in, row.in));
}

function savedElements() {
  return (profilesView.saved && profilesView.saved.elements) || [];
}

function savedName(row) {
  const entry = savedElements().find((candidate) => entryOfRow(candidate, row));
  return entry ? String(entry.name) : "";
}

/* A new read: its rows join the rows seen since the launch, and a row seen before keeps the
   name typed for it. */
function takeRows() {
  for (const row of idRows()) {
    const key = rowKey(row);
    profilesView.seen[key] = { role: row.role, id: row.id, in: row.in || null };
    if (!Object.prototype.hasOwnProperty.call(profilesView.names, key)) profilesView.names[key] = savedName(row);
  }
}

/* The saved profile changed: every row seen shows its saved name. */
function fillNames() {
  const names = {};
  for (const [key, row] of Object.entries(profilesView.seen)) names[key] = savedName(row);
  profilesView.names = names;
}

function changedElsewhere() {
  const shown = new Set(idRows().map(rowKey));
  return Object.entries(profilesView.seen).filter(([key, row]) => !shown.has(key)
    && (profilesView.names[key] || "").trim() !== savedName(row).trim()).length;
}

function signalOf(item) {
  if (typeof item.name === "string" && item.name.trim()) return { name: item.name.trim() };
  if (!item.id) return null;
  return item.in ? { id: item.id, role: item.role, in: item.in } : { id: item.id, role: item.role };
}

function signalText(signal) {
  if (signal.id !== undefined) {
    const area = signal.in ? ` · ${areaText(signal.in)} 안` : "";
    return `id ${signal.id} (${signal.role})${area}`;
  }
  return signal.role ? `이름 "${signal.name}" (${signal.role})` : `이름 "${signal.name}"`;
}

function areaText(area) {
  if (!area) return "없음";
  return area.id !== undefined ? `${area.role} id ${area.id}` : `${area.role} "${area.name}"`;
}

function addSignal(item, focusKey = null) {
  const signal = signalOf(item);
  if (!signal) return;
  const text = JSON.stringify(signal);
  if (profilesView.ready.some((known) => JSON.stringify(known) === text)) return;
  profilesView.ready.push(signal);
  profilesView.check = null;
  renderProfiles();
  if (focusKey !== null) refocusRow(focusKey);
}

function refocusRow(key) {
  const buttons = Array.from(document.querySelectorAll("#profile-view button.profile-row-signal"));
  const button = buttons.find((candidate) => candidate.dataset.key === key);
  if (button) button.focus();
}

/* The saved names of rows never seen stay; every row seen since the launch is saved with its
   name, or dropped when its box is empty. */
function elementsToSave() {
  const rows = Object.values(profilesView.seen);
  const kept = savedElements().filter((entry) => !rows.some((row) => entryOfRow(entry, row)));
  const named = [];
  for (const [key, row] of Object.entries(profilesView.seen)) {
    const name = (profilesView.names[key] || "").trim();
    if (!name) continue;
    named.push(row.in ? { id: row.id, role: row.role, in: row.in, name } : { id: row.id, role: row.role, name });
  }
  return [...kept, ...named];
}

/* --- requests ---------------------------------------------------------------------------- */

/* `where` is where an error is shown: "head" at the top, "actions" next to "지금 확인" and "저장". */
async function profilePost(route, body, where = "head") {
  profilesView.busy = true;
  renderProfiles();
  const reply = await knowledgeRequest(`/api/profile/${route}`, { system: profilesView.system, ...body });
  profilesView.busy = false;
  if (!reply.ok && reply.body.error) profilesView.message = { kind: "error", text: reply.body.error, where };
  return reply;
}

async function launchSystem() {
  profilesView.message = null;
  resetRead();
  const reply = await profilePost("launch", {});
  if (reply.ok) takeWindows(reply.body.windows);
  renderProfiles();
}

async function listWindows() {
  profilesView.message = null;
  const reply = await profilePost("windows", {});
  if (reply.ok) takeWindows(reply.body.windows);
  renderProfiles();
}

function takeWindows(windows) {
  profilesView.windows = Array.isArray(windows) ? windows : [];
  if (!profilesView.windows.some((row) => row.id === profilesView.windowId)) {
    profilesView.windowId = profilesView.windows.length ? profilesView.windows[0].id : null;
  }
  if (!profilesView.windows.length) {
    profilesView.message = { kind: "error", text: "창이 목록에 없습니다. 창이 뜰 때까지 기다렸다가 다시 받거나, 시스템을 닫고 다시 띄워 주세요." };
  }
}

async function readScreen(delay) {
  if (!profilesView.windowId) return;
  profilesView.message = null;
  let timer = null;
  if (delay) {
    profilesView.countdown = delay;
    timer = setInterval(() => {
      profilesView.countdown = Math.max(0, profilesView.countdown - 1);
      const left = $("profile-countdown");
      if (left) left.textContent = countdownText();
    }, 1000);
  }
  const reply = await profilePost("read", { window_id: profilesView.windowId, delay_s: delay });
  if (timer) clearInterval(timer);
  profilesView.countdown = 0;
  if (reply.ok) {
    clearRead();
    profilesView.read = reply.body;
    takeRows();
    if (reply.body.image) await loadPicture(reply.body.image);
    if (!reply.body.image) {
      profilesView.message = { kind: "info", text: "창을 그림으로 찍지 못했습니다(최소화됐거나 가려짐). 표는 트리로 보여 줍니다. 창을 띄운 뒤 다시 읽으면 그림과 박스가 나옵니다." };
    } else if (reply.body.missing > 0) {
      profilesView.message = { kind: "info", text: `위에 겹친 창 ${reply.body.missing}개(메뉴 목록 같은)를 찍지 못해 그림에서 뺐습니다. 그 안의 요소는 박스 자리가 비어 보입니다. 다시 읽어 주세요.` };
    }
  }
  renderProfiles();
}

function countdownText() {
  return profilesView.countdown > 0 ? `${profilesView.countdown}초 뒤에 읽습니다. 지금 앱에서 메뉴를 열어 두세요.` : "";
}

async function loadPicture(name) {
  try {
    const response = await fetch(`/api/profile/shots/${encodeURIComponent(name)}`,
      { headers: { "X-Agent-Token": state.token || "" }, cache: "no-store" });
    if (!response.ok) return;
    profilesView.imageUrl = URL.createObjectURL(await response.blob());
    const picture = new Image();
    await new Promise((resolve) => {
      picture.onload = resolve;
      picture.onerror = resolve;
      picture.src = profilesView.imageUrl;
    });
    profilesView.picture = picture.naturalWidth ? picture : null;
  } catch (error) {
    profilesView.picture = null;
  }
}

async function checkSignals() {
  profilesView.message = null;
  profilesView.check = null;
  const reply = await profilePost("check", { window_id: profilesView.windowId, ready: profilesView.ready }, "actions");
  if (reply.ok) {
    profilesView.check = reply.body;
    profilesView.message = { kind: "ok", text: `확인했습니다. ${checkSummary(reply.body)}`, where: "actions" };
  }
  renderProfiles();
}

async function saveProfile() {
  profilesView.message = null;
  profilesView.problems = [];
  const profile = { stable_ids: profilesView.stableIds, ready_read: profilesView.readMode, ready: profilesView.ready,
    elements: elementsToSave() };
  const reply = await profilePost("save", { read_id: profilesView.read ? profilesView.read.read_id : null, profile },
    "actions");
  if (reply.ok) {
    const reloaded = await knowledgeRequest(`/api/profile?system=${encodeURIComponent(profilesView.system)}`);
    if (reloaded.ok) applySavedProfile(reloaded.body.profile || {}, true);
    profilesView.message = { kind: "ok", text: "저장했습니다.", where: "actions" };
  } else if (Array.isArray(reply.body.problems)) {
    profilesView.problems = reply.body.problems;
    profilesView.message = { kind: "error", text: refusedText(), where: "actions", problems: true };
  }
  renderProfiles();
}

function refusedText() {
  const elements = elementsToSave();
  const shown = idRows();
  const inTable = profilesView.problems.filter((problem) => problem.where === "elements"
    && shown.some((row) => elements[problem.index] && entryOfRow(elements[problem.index], row))).length;
  const parts = [];
  if (inTable) parts.push(`위 표의 줄 ${inTable}개`);
  const ready = profilesView.problems.filter((problem) => problem.where === "ready").length;
  if (ready) parts.push(`준비 신호 ${ready}개`);
  const other = otherScreenProblems().length;
  if (other) parts.push(`표 아래의 다른 화면 등록 이름 ${other}개`);
  return `저장하지 못했습니다. ${parts.join(", ")}에 이유를 붙였습니다. 빨간 줄을 고쳐 다시 저장해 주세요.`;
}

function showFirstProblem() {
  const [first] = document.querySelectorAll("#profile-view .has-problem");
  if (first && first.scrollIntoView) first.scrollIntoView({ block: "center" });
}

/* --- rendering --------------------------------------------------------------------------- */

function problemFor(where, index) {
  const found = profilesView.problems.find((problem) => problem.where === where && problem.index === index);
  return found ? found.problem : null;
}

function rowProblem(row) {
  const elements = elementsToSave();
  const index = elements.findIndex((entry) => entry.role === row.role && entry.id === row.id && sameArea(entry.in, row.in));
  return index < 0 ? null : problemFor("elements", index);
}

function otherScreenProblems() {
  const elements = elementsToSave();
  const rows = idRows();
  return profilesView.problems.filter((problem) => problem.where === "elements"
    && !rows.some((row) => elements[problem.index] && entryOfRow(elements[problem.index], row)));
}

function profileButton(text, onClick, options = {}) {
  const button = el("button", { className: options.className || "", text, attrs: { type: "button" } });
  button.disabled = profilesView.busy || options.disabled === true;
  button.addEventListener("click", onClick);
  return button;
}

function renderProfiles() {
  const view = $("profile-view");
  const names = profileSystems();
  const system = profilesView.system;
  const nodes = [el("div", { className: "knowledge-head" }, profileHead(names, system))];
  if (!names.length) {
    nodes.push(el("p", { className: "hint", text: "설정 파일의 systems에 시스템을 적어 주세요." }));
    view.replaceChildren(el("div", { className: "knowledge-inner" }, nodes));
    return;
  }
  nodes.push(el("p", { className: "hint", text: "등록 화면은 앱을 띄우고 화면을 읽기만 합니다. 메뉴를 여는 일은 앱에서 직접 하세요." }));
  if (profilesView.message && profilesView.message.where !== "actions") {
    nodes.push(el("p", { className: `message ${profilesView.message.kind}`, text: profilesView.message.text }));
  }
  nodes.push(el("p", { className: "hint", text: countdownText(), attrs: { id: "profile-countdown", "aria-live": "polite" } }));
  if (profilesView.read) {
    nodes.push(el("div", { className: "profile-grid" }, [profileStage(), profilePanel()]));
  } else {
    nodes.push(profileReadyPanel());
  }
  view.replaceChildren(el("div", { className: "knowledge-inner" }, nodes));
  paintCrops();
}

function profileHead(names, system) {
  const head = [el("h2", { text: system ? `등록 (${system})` : "등록" })];
  if (!names.length) return head;
  const picker = el("select", { attrs: { id: "profile-system", "aria-label": "시스템" } },
    names.map((name) => el("option", { text: name, attrs: { value: name } })));
  picker.value = system || names[0];
  picker.addEventListener("change", () => {
    profilesView.system = picker.value;
    profilesView.message = null;
    profilesView.windows = [];
    profilesView.windowId = null;
    resetRead();
    loadProfile();
  });
  head.push(el("label", { className: "field knowledge-picker" }, [el("span", { text: "시스템" }), picker]));
  head.push(profileButton("앱 띄우기", launchSystem));
  if (profilesView.windows.length) {
    const windows = el("select", { attrs: { id: "profile-window", "aria-label": "창" } },
      profilesView.windows.map((row) => el("option", { text: row.title || "(제목 없음)", attrs: { value: row.id } })));
    windows.value = profilesView.windowId || profilesView.windows[0].id;
    windows.addEventListener("change", () => {
      profilesView.windowId = windows.value;
    });
    head.push(el("label", { className: "field knowledge-picker" }, [el("span", { text: "창" }), windows]));
    head.push(profileButton("창 목록 다시", listWindows, { className: "ghost" }));
    head.push(profileButton("화면 읽기", () => readScreen(0)));
    head.push(profileButton(`${PROFILE_READ_DELAY_S}초 뒤 읽기`, () => readScreen(PROFILE_READ_DELAY_S), { className: "ghost" }));
  }
  return head;
}

function profileStage() {
  const read = profilesView.read;
  const stage = el("div", { className: "profile-stage" });
  if (!read.image || !profilesView.imageUrl || !read.size) {
    stage.append(el("p", { className: "hint", text: "그림이 없습니다. 창을 띄운 뒤 다시 읽어 주세요." }));
    return stage;
  }
  const [width, height] = read.size;
  const image = el("img", { attrs: { alt: "읽은 창의 그림", src: profilesView.imageUrl } });
  const overlay = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "profile-overlay", preserveAspectRatio: "none" });
  for (const item of read.items || []) {
    if (!item.rect) continue;
    const classes = ["profile-box", item.kind === "id" ? "id" : item.kind === "text" ? "text" : "control"];
    if (profilesView.selected === item.n) classes.push("selected");
    if (profilesView.hovered === item.n) classes.push("hovered");
    const box = svg("rect", { x: item.rect.x, y: item.rect.y, width: item.rect.width, height: item.rect.height,
      class: classes.join(" ") });
    const tip = svg("title", {});
    tip.textContent = [item.role, item.name || item.alias || (item.id ? `id ${item.id}` : "")].filter(Boolean).join(" · ");
    box.append(tip);
    box.addEventListener("click", () => {
      profilesView.selected = item.n;
      renderProfiles();
    });
    overlay.append(box);
  }
  stage.append(el("div", { className: "profile-picture" }, [image, overlay]));
  return stage;
}

function profilePanel() {
  return el("div", { className: "profile-panel" }, [selectedPanel(), idTable(), profileReadyPanel()]);
}

function selectedPanel() {
  const items = profilesView.read.items || [];
  const item = items.find((candidate) => candidate.n === profilesView.selected);
  const section = el("section", { className: "profile-section" }, [el("h3", { text: "고른 요소" })]);
  if (!item) {
    section.append(el("p", { className: "hint", text: "그림의 박스를 누르면 여기에 나옵니다." }));
    return section;
  }
  const facts = el("dl", { className: "profile-facts" });
  for (const [term, value] of [["역할", item.role], ["이름", item.name || "이름 없음"], ["ID", item.id || "없음"],
    ["담긴 영역", areaText(item.in)], ["등록 이름", item.alias || ""]]) {
    if (!value) continue;
    facts.append(el("dt", { text: term }), el("dd", { text: value }));
  }
  section.append(facts);
  const signal = signalOf(item);
  section.append(profileButton("준비 신호로", () => addSignal(item), { disabled: !signal }));
  if (!signal) section.append(el("p", { className: "hint", text: "이름도 ID도 없는 요소는 준비 신호가 될 수 없습니다." }));
  return section;
}

function idTable() {
  const rows = idRows();
  const section = el("section", { className: "profile-section" }, [el("h3", { text: `이름 없는 요소 (ID 있음) ${rows.length}개` })]);
  if (!rows.length) {
    section.append(el("p", { className: "hint", text: "이 화면에는 이름 없이 ID만 있는 요소가 없습니다." }));
    return section;
  }
  const body = el("tbody");
  for (const row of rows) {
    const key = rowKey(row);
    const input = el("input", { attrs: { type: "text", maxlength: "80", "aria-label": "등록 이름" } });
    input.value = profilesView.names[key] || "";
    input.addEventListener("input", () => {
      profilesView.names[key] = input.value.replace(/[\r\n]+/g, " ");
    });
    const crop = el("canvas", { className: "profile-crop", attrs: { "data-n": String(row.n) } });
    const full = el("canvas", { className: "profile-crop-full", attrs: { "data-n": String(row.n), "aria-hidden": "true" } });
    const what = el("td", { className: "profile-what" }, [
      el("span", { text: `${row.leaf ? row.role : `${row.role} (자식 있음)`} · ID ${row.id}` }),
      el("span", { className: "profile-area", text: row.in ? `${areaText(row.in)} 안` : "담긴 영역 없음" })]);
    const problem = rowProblem(row);
    const nameCell = el("td", { className: "profile-name" },
      [input, problem ? el("span", { className: "profile-problem", text: problem }) : null]);
    const signal = profileButton("준비 신호로", () => addSignal(row, key), { className: "ghost profile-row-signal" });
    signal.setAttribute("data-key", key);
    const classes = [problem ? "has-problem" : "", shrunk(row.rect) ? "big" : ""].filter(Boolean).join(" ");
    const line = el("tr", { className: classes }, [el("td", { className: "profile-crop-cell" }, [crop, full]),
      what, nameCell, el("td", { className: "profile-row-action" }, [signal])]);
    line.addEventListener("pointerenter", () => {
      highlight(row.n);
      line.classList.add("zoomed");
    });
    line.addEventListener("pointerleave", () => {
      highlight(null);
      if (document.activeElement !== input) line.classList.remove("zoomed");
    });
    input.addEventListener("focus", () => line.classList.add("zoomed"));
    input.addEventListener("blur", () => line.classList.remove("zoomed"));
    body.append(line);
  }
  const head = el("thead", {}, [el("tr", {},
    ["그림", "요소", "이름", ""].map((text) => el("th", { text })))]);
  section.append(el("table", { className: "profile-table" }, [head, body]));
  for (const problem of otherScreenProblems()) {
    section.append(el("p", { className: "profile-problem", text: `다른 화면의 등록 이름 ${problem.index + 1}번째: ${problem.problem}` }));
  }
  return section;
}

function highlight(number) {
  profilesView.hovered = number;
  for (const box of document.querySelectorAll("#profile-view .profile-box")) box.classList.remove("hovered");
  if (number === null) return;
  const items = (profilesView.read && profilesView.read.items) || [];
  const index = items.filter((item) => item.rect).findIndex((item) => item.n === number);
  const boxes = document.querySelectorAll("#profile-view .profile-box");
  if (index >= 0 && boxes[index]) boxes[index].classList.add("hovered");
}

function shrunk(rect) {
  return Boolean(rect) && (rect.width > PROFILE_CROP_SHOWN_W || rect.height > PROFILE_CROP_SHOWN_H);
}

/* Both pieces (the one in the row and the one that opens) are drawn 1:1 from the picture; CSS
   only shrinks the one in the row. */
function paintCrops() {
  const picture = profilesView.picture;
  const items = (profilesView.read && profilesView.read.items) || [];
  const canvases = [...document.querySelectorAll("#profile-view canvas.profile-crop"),
    ...document.querySelectorAll("#profile-view canvas.profile-crop-full")];
  for (const canvas of canvases) {
    const item = items.find((candidate) => String(candidate.n) === canvas.dataset.n);
    if (!picture || !item || !item.rect) {
      canvas.width = 0;
      canvas.height = 0;
      continue;
    }
    canvas.width = Math.max(1, Math.round(item.rect.width));
    canvas.height = Math.max(1, Math.round(item.rect.height));
    canvas.getContext("2d").drawImage(picture, item.rect.x, item.rect.y, item.rect.width, item.rect.height,
      0, 0, canvas.width, canvas.height);
  }
}

function profileReadyPanel() {
  const section = el("section", { className: "profile-section" }, [el("h3", { text: `준비 신호 ${profilesView.ready.length}개` })]);
  const list = el("ul", { className: "profile-signals" });
  profilesView.ready.forEach((signal, index) => {
    const drop = profileButton("지움", () => {
      profilesView.ready.splice(index, 1);
      profilesView.check = null;
      renderProfiles();
    }, { className: "ghost" });
    const problem = problemFor("ready", index);
    const found = checkFound(index);
    list.append(el("li", { className: problem ? "has-problem" : "" }, [
      el("span", { text: signalText(signal) }), found ? el("span", { className: "hint", text: found }) : null, drop,
      problem ? el("span", { className: "profile-problem", text: problem }) : null]));
  });
  section.append(list);
  if (profilesView.ready.length === 1 && profilesView.ready[0].id !== undefined) {
    section.append(el("p", { className: "message info", text: "이름 신호나 다른 ID 신호를 함께 두세요. ID 신호 하나만으로는 다른 화면과 헷갈릴 수 있습니다." }));
  }
  const stable = el("input", { attrs: { type: "checkbox", id: "profile-stable-ids" } });
  stable.checked = profilesView.stableIds;
  stable.addEventListener("change", () => {
    profilesView.stableIds = stable.checked;
  });
  section.append(el("label", { className: "profile-check" }, [stable, el("span", { text: "이 시스템의 ID는 다시 켜도 같음" })]));
  const mode = el("select", { attrs: { id: "profile-read-mode", "aria-label": "읽는 방식" } },
    PROFILE_READ_MODES.map(([value, text]) => el("option", { text, attrs: { value } })));
  mode.value = profilesView.readMode;
  const modeField = el("label", { className: "field profile-read-mode" }, [el("span", { text: "읽는 방식" }), mode]);
  const advice = el("p", { className: "message info profile-advice", attrs: { id: "profile-read-advice" } });
  const showAdvice = () => {
    advice.textContent = readAdvice();
    advice.hidden = !advice.textContent;
    modeField.classList.toggle("differs", Boolean(advice.textContent));
  };
  mode.addEventListener("change", () => {
    profilesView.readMode = mode.value;
    showAdvice();
  });
  showAdvice();
  section.append(modeField);
  if (profilesView.check) section.append(el("p", { className: "hint", text: checkSummary(profilesView.check) }));
  section.append(advice);
  const canRead = Boolean(profilesView.read && profilesView.windowId);
  section.append(el("div", { className: "profile-actions" }, [
    profileButton("지금 확인", checkSignals, { disabled: !canRead || !profilesView.ready.length }),
    profileButton("저장", saveProfile, { disabled: !canRead }),
  ]));
  const elsewhere = profilesView.read ? changedElsewhere() : 0;
  if (elsewhere) {
    section.append(el("p", { className: "hint", text: `이 화면에 없는 요소의 고친 이름 ${elsewhere}개(앞서 읽은 화면에서 적음)도 함께 저장합니다.` }));
  }
  const message = profilesView.message;
  if (message && message.where === "actions") {
    const line = el("div", { className: `message ${message.kind} profile-action-message`,
      attrs: { id: "profile-action-message", role: "status" } }, [el("span", { text: message.text })]);
    if (message.problems) line.append(profileButton("표시된 곳 보기", showFirstProblem, { className: "ghost" }));
    section.append(line);
  }
  if (!canRead) section.append(el("p", { className: "hint", text: "확인과 저장은 화면을 읽은 뒤에 할 수 있습니다." }));
  return section;
}

function readAdvice() {
  const check = profilesView.check;
  if (!check || !check.recommended || check.recommended === profilesView.readMode) return "";
  return `잰 결과로는 ${check.recommended}가 권장입니다. 지금 고른 읽는 방식은 ${profilesView.readMode}입니다. 바꾸려면 위에서 고르세요.`;
}

function checkFound(index) {
  const check = profilesView.check;
  if (!check || !check.snapshot || !check.find) return null;
  const word = (found) => (found ? "찾음" : "없음");
  return `snapshot ${word(check.snapshot.found[index])} · find ${word(check.find.found[index])}`;
}

function checkSummary(check) {
  const seconds = (ms) => `${(Number(ms) / 1000).toFixed(1)}초`;
  const parts = [`snapshot ${check.snapshot.ok ? seconds(check.snapshot.ms) : "실패"}`,
    `find ${check.find.ok ? seconds(check.find.ms) : "실패"}${check.find.timeouts ? ` (시간 초과 ${check.find.timeouts})` : ""}`];
  parts.push(check.recommended ? `권장 ${check.recommended}` : "권장 없음 (신호를 다 찾지 못함)");
  return parts.join(" · ");
}
