"""The summary lines about how the run read the screen: shallow reads and table lookups.

Counts only; the windows, codes, conditions and values stay in events.jsonl.
"""

from agent.deep_read import deep_read_counts

SEPARATOR = " · "


def find_rows_counts(events):
    """(lookups, lookups with no row, lookups with two rows or more) from a run's find_rows
    events, or None when there are none. A matched count that is not a whole number counts as a
    lookup only."""
    found = [event.get("matched") for event in events if event.get("type") == "find_rows"]
    if not found:
        return None
    counts = [value for value in found if type(value) is int and value >= 0]
    return len(found), sum(1 for value in counts if value == 0), sum(1 for value in counts if value >= 2)


def read_lines(events):
    """"  깊이: …" when a read fell back or left nodes cut, "  표 찾기: …" when find_rows ran."""
    lines = []
    depth = deep_read_counts(events)
    if depth:
        lines.append(f"  깊이: 얕게 {depth[0]}회{SEPARATOR}잘림 {depth[1]}")
    found = find_rows_counts(events)
    if found:
        lines.append(f"  표 찾기: {found[0]}회{SEPARATOR}없음 {found[1]}{SEPARATOR}여럿 {found[2]}")
    return lines
