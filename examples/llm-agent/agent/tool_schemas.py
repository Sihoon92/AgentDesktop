"""OpenAI function definitions, each tagged with the permission level that unlocks it."""

REF = {"type": "string", "description": "A ref from the latest observation, such as @s1:e2"}
WINDOW = {"type": "string", "description": "A window id from list_windows"}
COORD = {"type": "integer", "description": "Absolute screen pixel inside the window"}
INTENT = {
    "type": "string",
    "description": "One sentence on why you take this action, in the language of the goal",
}
READ_ONLY_TOOLS = frozenset({"list_windows", "observe", "wait_for_text", "finish"})
"""Tools that change nothing in the app. Every other tool takes a required intent."""


def _tool(name, description, level, **properties):
    if name not in READ_ONLY_TOOLS:
        properties["intent"] = INTENT
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
    _tool("restart_target", "Close and relaunch the target. The user must confirm; unsaved work may be lost.", 3),
    _tool(
        "finish",
        "End the run. A success claim is checked independently before it counts.",
        0,
        success={"type": "boolean"},
        evidence={"type": "string", "description": "What you observed"},
    ),
]
