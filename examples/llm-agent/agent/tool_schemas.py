"""OpenAI function definitions, each tagged with the permission level that unlocks it."""

REF = {"type": "string", "description": "A ref from the latest observation, such as @s1:e2"}
WINDOW = {"type": "string", "description": "A window id from list_windows"}
COORD = {"type": "integer", "description": "Absolute screen pixel inside the window"}
INTENT = {
    "type": "string",
    "description": "One sentence on why you take this action, in the language of the goal",
}
EXPECTATIONS = ("window_opened", "window_closed", "content_changed", "value_changed", "any")
EXPECT = {
    "type": "string",
    "enum": list(EXPECTATIONS),
    "description": "The change you expect this action to cause. The result tells you whether it happened",
}
KNOWLEDGE_ID = {
    "type": ["string", "null"],
    "description": "The id of the learned entry this action follows, or null",
}
MARK = {"type": "integer", "description": "A [n] mark number from the latest observation"}
CLICK_ACTION = {"type": "string", "enum": ["click", "double_click", "right_click"]}
READ_ONLY_TOOLS = frozenset({"list_windows", "observe", "wait_for_text", "look", "find_cells", "ask", "finish"})
"""Tools that change nothing in the app. Every other tool takes a required intent."""
EXPECTING_TOOLS = frozenset({
    "click", "toggle", "expand", "collapse", "set_value", "select", "check", "uncheck", "scroll",
    "type", "double_click", "right_click", "mouse_click", "mouse_wheel", "drag", "press",
    "restore_window", "click_part", "click_point",
})
"""Tools that change the app and re-observe; each takes expect and knowledge_id."""


def _tool(name, description, level, **properties):
    if name not in READ_ONLY_TOOLS:
        properties["intent"] = INTENT
    if name in EXPECTING_TOOLS:
        properties["expect"] = EXPECT
        properties["knowledge_id"] = KNOWLEDGE_ID
    return {
        "level": level,
        "definition": {
            "type": "function",
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


SCHEMAS = [
    _tool("launch_target", "Start the target app, or attach if that exact exe already runs. Call first.", 0),
    _tool("list_windows", "List the target process's windows.", 0),
    _tool(
        "observe",
        "Read a window's accessibility tree. Actions already return a fresh observation.",
        0,
        window_id={"type": ["string", "null"], "description": "Null keeps the current window"},
        root_ref={"type": ["string", "null"], "description": "Read only this ref's subtree"},
        surface={"type": ["string", "null"], "enum": ["window", "focused", "menu", "sheet", None]},
        include_bounds={"type": "boolean", "description": "Add pixel bounds, for mouse tools"},
    ),
    _tool(
        "wait_for_text",
        "Re-read the current window until this text appears or the timeout ends.",
        0,
        text={"type": "string"},
        timeout_ms={"type": "integer"},
    ),
    _tool(
        "look",
        "See a screenshot of the current window with each [n] mark drawn as a numbered box. Use it when "
        "the tree does not show the target or an action did not do what you expected.",
        0,
        mark={"type": ["integer", "null"], "description": "Null for the whole window, or a mark to crop around"},
    ),
    _tool(
        "find_cells",
        "Find table cells where row labels and column labels cross, from the text of the latest "
        "observation. Clicks nothing. Returns every candidate with its point and mark.",
        0,
        rows={"type": "array", "items": {"type": "string"},
              "description": "Labels on one table row, such as a group and its subtotal label"},
        columns={"type": "array", "items": {"type": "string"},
                 "description": "Stacked header labels of one column, top level first"},
    ),
    _tool(
        "ask",
        "Ask the user and wait for the answer. Use kind \"confirm\" before pressing a target you are "
        "not sure of (several candidates, a point you picked in a capture), with the one you propose. "
        "Use kind \"stuck\" when actions keep failing, numbering the candidates near the problem. "
        "Changes nothing in the app.",
        0,
        kind={"type": "string", "enum": ["confirm", "stuck"]},
        text={"type": "string", "description": "The question, in the language of the goal"},
        candidates={
            "type": "array",
            "description": "What the user can pick; the user sees them numbered from 1",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Short description of this candidate"},
                    "mark": {"type": ["integer", "null"], "description": "Its [n] mark, or null"},
                    "capture": {"type": ["string", "null"], "description": "Capture id for a picked pixel"},
                    "x": {"type": ["integer", "null"], "description": "Image pixel column in that capture"},
                    "y": {"type": ["integer", "null"], "description": "Image pixel row in that capture"},
                },
                "required": ["label", "mark", "capture", "x", "y"],
                "additionalProperties": False,
            },
        },
        proposed={"type": ["integer", "null"], "description": "The candidate number you propose, or null"},
        basis={"type": "array", "items": {"type": "integer"},
               "description": "Marks you based the choice on, such as row and column labels"},
    ),
    _tool("restore_window", "Restore a minimized window. Refs fail while it is minimized.", 0, window_id=WINDOW),
    _tool(
        "click",
        "Invoke a button, link or menu item without moving the cursor. "
        "For a switch or checkbox use toggle, check or uncheck instead.",
        0,
        ref=REF,
    ),
    _tool(
        "toggle",
        "Flip a switch or checkbox once. Prefer check or uncheck when available.",
        0,
        ref=REF,
    ),
    _tool("expand", "Expand a menu, tree item or combobox.", 0, ref=REF),
    _tool("collapse", "Collapse a menu, tree item or combobox.", 0, ref=REF),
    _tool("set_value", "Set an input's value without focusing it.", 1, ref=REF, value={"type": "string"}),
    _tool("select", "Pick an option by its display text.", 1, ref=REF, value={"type": "string"}),
    _tool("check", "Turn a checkbox or switch on. Safe to repeat.", 1, ref=REF),
    _tool("uncheck", "Turn a checkbox or switch off. Safe to repeat.", 1, ref=REF),
    _tool(
        "scroll",
        "Scroll an element.",
        1,
        ref=REF,
        direction={"type": "string", "enum": ["up", "down", "left", "right"]},
        amount={"type": "integer"},
    ),
    _tool("press", "Send a key combo to a target window.", 1, window_id=WINDOW, combo={"type": "string"}),
    _tool("type", "Type with physical keystrokes. Takes focus; prefer set_value.", 2, ref=REF, text={"type": "string"}),
    _tool("double_click", "Physically double-click an element. Takes focus.", 2, ref=REF),
    _tool("right_click", "Physically right-click an element. Takes focus.", 2, ref=REF),
    _tool(
        "mouse_click",
        "Physically click a point inside a target window. Moves the real cursor.",
        2,
        window_id=WINDOW,
        x=COORD,
        y=COORD,
        button={"type": "string", "enum": ["left", "right", "middle"]},
    ),
    _tool(
        "mouse_wheel",
        "Send wheel lines at a point inside a target window. Negative dy scrolls down.",
        2,
        window_id=WINDOW,
        x=COORD,
        y=COORD,
        dy={"type": "integer"},
    ),
    _tool(
        "drag",
        "Physically drag between two points inside a target window.",
        2,
        window_id=WINDOW,
        from_x=COORD,
        from_y=COORD,
        to_x=COORD,
        to_y=COORD,
    ),
    _tool(
        "click_part",
        "Physically click one part of the element numbered [mark] in the latest observation. Use it for "
        "an element without a ref, or for one end of a box: a dropdown often opens only from the arrow "
        "at its right end. Moves the real cursor.",
        2,
        mark=MARK,
        part={"type": "string", "enum": ["center", "left", "right", "top", "bottom"]},
        action=CLICK_ACTION,
    ),
    _tool(
        "click_point",
        "Physically click a pixel you picked in a capture from look. x and y are pixels of that "
        "capture image, not screen coordinates. Moves the real cursor.",
        2,
        capture={"type": "string", "description": "A capture id from look, such as c1"},
        x={"type": "integer", "description": "Image pixel column in the capture"},
        y={"type": "integer", "description": "Image pixel row in the capture"},
        action=CLICK_ACTION,
    ),
    _tool("restart_target", "Close and relaunch the target. The user must confirm; unsaved work may be lost.", 3),
    _tool(
        "finish",
        "End the run. A success claim is checked independently before it counts.",
        0,
        success={"type": "boolean"},
        evidence={"type": "string", "description": "What you observed"},
        saved_path={"type": ["string", "null"], "description": "The full path of the file you saved, or null"},
        completion_id={"type": ["string", "null"],
                       "description": "The id of the learned completion entry that fits this task, or null"},
    ),
]
