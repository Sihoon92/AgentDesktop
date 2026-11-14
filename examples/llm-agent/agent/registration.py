"""After a verified or confirmed run: turn the run's questions and answers, and the stages where
the agent took a detour, into knowledge entries, show them to the person, and save only what
they approve.

A detour is a done stage with a signal (agent/stages.py): an unexpected result, a window opened
and left unused, two looks, or a limit question. Each item says where it came from: "question"
for one drawn from an exchange or a person's confirmation, "discovered" for one drawn from a
detour stage, with that stage's number.

The model writes the draft in one call; the store validates every item before the person sees
it, and again when it saves, so an entry with coordinates or unknown keys never lands.
"""

import json
from datetime import date

from agent.guard import AgentStopped
from agent.knowledge import KnowledgeError, check_entry

REGISTRATION_QUESTION = "다음 내용을 등록할까요?"
CONTENT_FIELDS = {"resolution": ("situation", "resolution"), "completion": ("task", "checks")}
NULLABLE_TEXT = {"type": ["string", "null"]}

DRAFT_INSTRUCTIONS = """\
You turn what a person told an agent during one desktop-automation run, and what the agent found
out by itself, into short notes the agent reads at the start of later runs of the same
application, so it does not ask again or search again.

You get the run's goal, how its result was confirmed, the questions the agent asked with the
person's answers and the actions that worked after each answer, the stages where the agent took
a detour, and the notes that already exist. Call propose_entries once. Propose nothing when
nothing would spare a question or a search next time.

- stages lists done stages with signals of a detour: mismatch (an action did not have the
  expected effect), abandoned_window (a window was opened and closed or left without acting in
  it), looked_twice (the agent looked at the screen two or more times), limit_question (the stage
  ran out of calls and the person was asked). Each has its goal, done_when, the agent's note, and
  its actions with their intents and results in order. From a detour, write what the next run
  should do to avoid it as a resolution note: the situation that led into the detour and the
  resolution that would have gone straight to the goal. Set stage to that stage's n and exchange
  to null. Leave out a stage that teaches nothing about the application.
- When a stage and an exchange teach the same thing, write one note, from the exchange.

- A resolution note has a situation (what the agent tried and what went wrong, or what was
  ambiguous) and a resolution (what to do instead). Write them so they apply to the same screen
  on another day: name elements by role, visible name, part (left, right, top, bottom, center)
  and position relative to other elements.
- Never write screen coordinates, pixel values, [n] mark numbers, refs, window ids, or values
  shown on the screen (quantities, dates, item and person names). Notes with coordinates are
  refused.
- Do not repeat a note that already exists. When an exchange has failed_knowledge_id, the note
  with that id was followed and failed: propose action "update" with that id and corrected text
  instead of a new note.
- When the result was confirmed by a person (by "human") and a file was saved, propose one
  completion note: task describes the kind of task in general words, checks holds one file check
  whose pattern is "*" followed by the saved file's extension, with path null.
- exchange is the 1-based number of the exchange a note comes from, or null. stage is the n of
  the stage a note comes from, or null.
- Write situation, resolution and task in the language of the person's answers.
- Fields that do not apply to the note's type are null.
"""

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["file", "element", "human"]},
        "path": {**NULLABLE_TEXT, "description": "file: a folder or file path, or null to check the saved file"},
        "pattern": {**NULLABLE_TEXT, "description": "file: a file name pattern such as *.xlsx"},
        "role": {**NULLABLE_TEXT, "description": "element: the role the element must have"},
        "name": {**NULLABLE_TEXT, "description": "element: the exact name"},
        "name_contains": {**NULLABLE_TEXT, "description": "element: text the name contains"},
    },
    "required": ["kind", "path", "pattern", "role", "name", "name_contains"],
    "additionalProperties": False,
}
ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "update"]},
        "id": {**NULLABLE_TEXT, "description": "update: the id of the existing note; add: null"},
        "exchange": {"type": ["integer", "null"], "description": "1-based exchange number, or null"},
        "stage": {"type": ["integer", "null"], "description": "n of the detour stage, or null"},
        "type": {"type": "string", "enum": ["resolution", "completion"]},
        "situation": NULLABLE_TEXT,
        "resolution": NULLABLE_TEXT,
        "task": NULLABLE_TEXT,
        "checks": {"type": ["array", "null"], "items": CHECK_SCHEMA},
    },
    "required": ["action", "id", "exchange", "stage", "type", "situation", "resolution", "task", "checks"],
    "additionalProperties": False,
}
PROPOSE_TOOL = {
    "type": "function",
    "name": "propose_entries",
    "description": "Proposes the notes to save. The person approves, edits or rejects each one.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {"items": {"type": "array", "items": ITEM_SCHEMA}},
        "required": ["items"],
        "additionalProperties": False,
    },
}


def draft(model, exchanges, finish_note, existing, stages=()):
    """Asks the model once for draft items. Returns (items, tokens); each item is
    {"n", "action", "id", "exchange", "stage", "origin", "entry"}. `stages` are the detour stage
    records. No proposal call means no items."""
    items, tokens, _problems = _draft(model, exchanges, finish_note, existing, stages)
    return items, tokens


def register_after_run(model, toolbox, log, *, store, run_id, today=None):
    """Drafts entries from the run's exchanges and detour stages, asks the person about them, and
    saves the approved ones. Returns the tokens the draft used. With no exchange, no human
    confirmation and no detour, or no one to ask, the model is not called. Never raises for a
    refused entry or an unanswered question; those become warning events."""
    exchanges = toolbox.exchanges()
    note = toolbox.finish_note
    human_confirmed = bool(note) and note.get("by") == "human"
    detours = _detours(getattr(toolbox, "stage_records", None))
    if not toolbox.can_ask or (not exchanges and not human_confirmed and not detours):
        return 0
    existing = store.entries()
    items, tokens, problems = _draft(model, exchanges, note, existing, detours)
    for problem in problems:
        _warn(log, problem)
    items = _checked(items, {entry["id"]: entry for entry in existing}, log)
    if not items:
        return tokens
    try:
        answer = toolbox.ask({"kind": "registration", "text": REGISTRATION_QUESTION,
                              "items": [_shown(item) for item in items]})
    except AgentStopped:
        _warn(log, "등록 질문에 답이 없어 아무것도 저장하지 않았습니다")
        return tokens
    day = (today or date.today()).isoformat()
    decisions = {decision.get("n"): decision for decision in answer.items if isinstance(decision, dict)}
    saved, rejected = [], 0
    for item in items:
        decision = decisions.get(item["n"]) or {"decision": "reject"}
        if decision.get("decision") not in ("approve", "edit"):
            rejected += 1
            continue
        entry = item["entry"]
        if decision["decision"] == "edit" and isinstance(decision.get("entry"), dict):
            entry = {**entry, **decision["entry"], "type": entry["type"]}
        source = _source(run_id, day, exchanges, item)
        try:
            saved.append(_save(store, item, entry, source, today))
        except KnowledgeError as error:
            _warn(log, f"{item['n']}번 항목을 저장하지 못했습니다: {error}")
    log({"type": "registration", "saved": saved, "rejected": rejected})
    return tokens


def _detours(records):
    """The done stages with at least one signal; stopped, unfinished and quiet stages are left out."""
    return [record for record in records or []
            if isinstance(record, dict) and record.get("status") == "done" and record.get("signals")]


def _draft(model, exchanges, finish_note, existing, stages=()):
    stages = list(stages)
    material = {
        "goal": (finish_note or {}).get("goal"),
        "result": _result(finish_note),
        "exchanges": [{"exchange": number, **exchange} for number, exchange in enumerate(exchanges, 1)],
        "stages": stages,
        "existing_notes": [_existing_view(entry) for entry in existing],
    }
    detour_numbers = {record.get("n") for record in stages}
    message = {"role": "user", "content": json.dumps(material, ensure_ascii=False)}
    response = model.respond(DRAFT_INSTRUCTIONS, [message], [PROPOSE_TOOL])
    tokens = response.usage.total_tokens if getattr(response, "usage", None) else 0
    calls = [item for item in response.output
             if getattr(item, "type", None) == "function_call" and getattr(item, "name", None) == PROPOSE_TOOL["name"]]
    if not calls:
        return [], tokens, []
    try:
        raw_items = json.loads(calls[0].arguments).get("items") or []
    except (ValueError, AttributeError) as error:
        return [], tokens, [f"등록 초안을 읽지 못했습니다: {type(error).__name__}"]
    items, problems = [], []
    for raw in raw_items:
        try:
            items.append(_item(len(items) + 1, raw, len(exchanges), detour_numbers))
        except (KeyError, TypeError, ValueError) as error:
            problems.append(f"등록 초안 항목을 버렸습니다: {type(error).__name__}: {error}")
    return items, tokens, problems


def _result(finish_note):
    if not finish_note:
        return None
    saved_path = finish_note.get("saved_path")
    extension = saved_path.rsplit(".", 1)[-1].lower() if saved_path and "." in saved_path else None
    return {"confirmed_by": finish_note.get("by"), "saved_file_extension": f".{extension}" if extension else None}


def _existing_view(entry):
    keys = ("id", "type", *CONTENT_FIELDS[entry["type"]])
    return {key: entry[key] for key in keys if key in entry}


def _item(number, raw, exchange_count, detour_numbers):
    kind = raw["type"]
    if kind not in CONTENT_FIELDS:
        raise ValueError(f"unknown type {kind!r}")
    entry = {"type": kind}
    for field in CONTENT_FIELDS[kind]:
        if raw.get(field) is not None:
            entry[field] = _checks(raw[field]) if field == "checks" else raw[field]
    action = raw.get("action")
    if action not in ("add", "update"):
        raise ValueError(f"unknown action {action!r}")
    exchange = raw.get("exchange") if type(raw.get("exchange")) is int else None
    stage = raw.get("stage") if type(raw.get("stage")) is int else None
    from_exchange = exchange is not None and 1 <= exchange <= exchange_count
    discovered = stage in detour_numbers and not from_exchange
    return {"n": number, "action": action, "id": raw.get("id") if action == "update" else None,
            "exchange": exchange, "stage": stage if discovered else None,
            "origin": "discovered" if discovered else "question", "entry": entry}


def _checks(raw_checks):
    checks = []
    for raw in raw_checks:
        kind = raw["kind"]
        if kind == "file":
            check = {"kind": "file"}
            check.update({key: raw[key] for key in ("path", "pattern") if raw.get(key)})
        elif kind == "element":
            require = {key: raw[key] for key in ("role", "name", "name_contains") if raw.get(key)}
            check = {"kind": "element", "require": require}
        else:
            check = {"kind": kind}
        checks.append(check)
    return checks


def _checked(items, existing_by_id, log):
    """Drops items the store would refuse and renumbers the rest from 1."""
    kept = []
    for item in items:
        try:
            if item["action"] == "update":
                current = existing_by_id.get(item["id"])
                if current is None or current["type"] != item["entry"]["type"]:
                    raise KnowledgeError(f"고칠 항목 {item['id']}이 사용 중인 같은 종류의 항목이 아닙니다")
                check_entry({**current, **item["entry"]})
            else:
                check_entry(item["entry"])
        except KnowledgeError as error:
            _warn(log, f"등록 초안 {item['n']}번을 버렸습니다: {error}")
            continue
        kept.append({**item, "n": len(kept) + 1})
    return kept


def _shown(item):
    return {"n": item["n"], "action": item["action"], "id": item["id"], "origin": item["origin"],
            "stage": item["stage"], "entry": item["entry"]}


def _source(run_id, day, exchanges, item):
    """Where a saved entry came from. A discovered one names its stage and no exchange; one from a
    question names the exchange it came from, or the only exchange when it named none."""
    if item["origin"] == "discovered":
        return {"run_id": run_id, "origin": "discovered", "stage": item["stage"], "date": day}
    source = {"run_id": run_id}
    exchange = item.get("exchange")
    if type(exchange) is not int or not 1 <= exchange <= len(exchanges):
        exchange = 1 if len(exchanges) == 1 else None
    if exchange is not None:
        chosen = exchanges[exchange - 1]
        source["question"] = chosen["question"]["text"]
        source["answer"] = chosen["answer"]["text"]
    source["origin"] = "question"
    source["date"] = day
    return source


def _save(store, item, entry, source, today):
    content = {key: value for key, value in entry.items() if key != "type"}
    if item["action"] == "update":
        updated = store.update(item["id"], {**content, "source": source})
        return {"id": updated["id"], "type": updated["type"], "action": "update"}
    added = store.add({**entry, "source": source}, today=today)
    return {"id": added["id"], "type": added["type"], "action": "add"}


def _warn(log, message):
    log({"type": "warning", "message": message})
