"""What the model is sent: the whole conversation, trimmed to what still matters.

The recorder keeps everything; this module only decides what goes back to the model.
Responses API input must keep every tool call next to its output and every reasoning item
with the calls of its turn, so a turn is dropped whole or kept whole, and inside a kept turn
only the text of an output changes. Nothing here reads or writes files except the capture
images, which agent.images reads once per result and image choice.

Rules of view():
- Only the most recent result with an observation keeps its text; earlier ones say how many
  lines were left out. Their other keys (effects, expected_met, refused, ...) stay.
- Images go with the most recent observation result and the results after it. A look's image
  also stays until a later app-changing call that was not refused.
- ask and plan calls and their outputs are never trimmed and never dropped.
- close_stage drops the turns since the last close, except turns with an ask or plan call, and
  puts one summary message after them. The turn holding the most recent observation is still
  sent, in its place, until a newer observation arrives: closing a stage changes nothing on
  the screen, so that observation is still the current one.
"""

from agent.images import IMAGE_MODES, image_items
from agent.tool_schemas import READ_ONLY_TOOLS

KEPT_TOOLS = frozenset({"ask", "plan"})
"""Calls whose turn survives close_stage and whose output is never trimmed."""
OMITTED = "(earlier observation omitted: {lines} lines)"


class _Result:
    def __init__(self, seq, call_id, name, output, captures):
        self.seq = seq
        self.call_id = call_id
        self.name = name
        self.output = output
        self.captures = list(captures)
        self.items = {}


class _Turn:
    def __init__(self, output):
        self.output = list(output)
        self.call_ids = {_field(item, "call_id") for item in self.output if _field(item, "type") == "function_call"}
        self.kept = any(_field(item, "type") == "function_call" and _field(item, "name") in KEPT_TOOLS
                        for item in self.output)
        self.results = []
        self.closed = False


class Transcript:
    """The model's input items, built from responses and tool results as the loop gets them."""

    def __init__(self, first_message, image_mode):
        if image_mode not in IMAGE_MODES:
            raise ValueError(f"unknown image mode: {image_mode!r}")
        self._first = first_message
        self._mode = image_mode
        self._blocks = []
        self._results = []
        self._boundary = 0

    def add_response(self, output):
        """Starts a turn with the output items of one model response (SDK objects or dicts)."""
        self._blocks.append(_Turn(output))

    def add_result(self, call_id, name, output, captures):
        """Adds the result of one call to the turn that made it, even when that turn is closed.
        `output` is kept as given and never changed."""
        turn = next((block for block in reversed(self._blocks)
                     if isinstance(block, _Turn) and call_id in block.call_ids), None)
        if turn is None:
            raise ValueError(f"no call {call_id!r} in the transcript")
        result = _Result(len(self._results), call_id, name, output, captures)
        self._results.append(result)
        turn.results.append(result)

    def add_note(self, text):
        """Adds one user message, such as the person's answer to a limit question."""
        self._blocks.append({"role": "user", "content": text})

    def close_stage(self, summary):
        """Replaces the turns since the last close with one summary message, keeping the turns
        that hold an ask or plan call and every note. view() still sends the closed turn that
        holds the most recent observation."""
        for block in self._blocks[self._boundary:]:
            if isinstance(block, _Turn) and not block.kept:
                block.closed = True
        self._blocks.append({"role": "user", "content": summary})
        self._boundary = len(self._blocks)

    def view(self):
        """The input items to send now. Calling it again gives an equal list."""
        observed = [result.seq for result in self._results if "observation" in result.output]
        latest = observed[-1] if observed else None
        changes = [result.seq for result in self._results
                   if result.name not in READ_ONLY_TOOLS and "refused" not in result.output]
        last_change = changes[-1] if changes else -1
        items = [self._first]
        for block in self._blocks:
            if not isinstance(block, _Turn):
                items.append(block)
                continue
            if block.closed and not any(result.seq == latest for result in block.results):
                continue
            items += block.output
            for result in block.results:
                items += self._items(result, latest, last_change)
        return items

    def _items(self, result, latest, last_change):
        trimmed = (result.name not in KEPT_TOOLS and latest is not None and result.seq != latest
                   and "observation" in result.output)
        imaged = (latest is None or result.seq >= latest
                  or (result.name == "look" and result.seq > last_change))
        key = (trimmed, imaged)
        if key not in result.items:
            output = result.output
            if trimmed:
                output = {**output, "observation": OMITTED.format(lines=_line_count(output["observation"]))}
            result.items[key] = image_items(result.call_id, output, result.captures if imaged else [], self._mode)
        return result.items[key]


def _line_count(observation):
    return len(observation.splitlines()) if isinstance(observation, str) else 0


def _field(item, name):
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)
