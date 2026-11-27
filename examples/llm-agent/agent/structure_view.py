"""The model's view of an agent.structure.Structure: the summary at the head of an observation.

Areas, tables and lines carry their handles (A1, T1) and the observation's numbers ([n]); a
handle is only a name for the tools that open it, never something to press.
"""

import json

SUMMARY_COLUMNS = 8
READ_ROWS = 30


def render_summary(structure):
    """The "구조" block, or "" when the observation has no area with a line or a table and no
    table. An area with neither is left out."""
    lines = []
    placed = set()
    for area in structure.areas:
        if not area.lines and not area.tables:
            continue
        lines.append(f"  {area.handle} 영역 {_area_label(area.container)}")
        lines.extend(f"    줄{index}: {_line(structure, line)}" for index, line in enumerate(area.lines, 1))
        for handle in area.tables:
            lines.extend(_table_lines(structure.table(handle), "    "))
            placed.add(handle)
    for table in structure.tables:
        if table.handle not in placed:
            lines.extend(_table_lines(table, "  "))
    return "\n".join(["구조", *lines]) if lines else ""


def _area_label(container):
    if "id" in container:
        return f"id={_quoted(container['id'])}"
    return _quoted(container.get("name"))


def _member(structure, mark):
    name = (structure.members.get(mark) or {}).get("name")
    return f"[{mark}] {_quoted(name)}" if name else f"[{mark}]"


def _line(structure, marks):
    """The line's elements left to right; a named first element that is no control (a label) is
    set off from the rest with an arrow."""
    parts = [_member(structure, mark) for mark in marks]
    first = structure.members.get(marks[0]) or {}
    if len(parts) > 1 and first.get("name") and not first.get("control"):
        return f"{parts[0]} → " + " · ".join(parts[1:])
    return " · ".join(parts)


def _table_lines(table, indent):
    mark = f" [{table.mark}]" if table.mark is not None else ""
    has_values = any(cell is not None and cell.value is not None for row in table.rows for cell in row.cells)
    keys = ", ".join(table.key_candidates) or "없음"
    head = (f"{indent}{table.handle} 표{mark}  열 {len(table.columns)}개 · 행 {len(table.rows)}개 · "
            f"{'값 있음' if has_values else '값 없음'} · 행을 가르는 열: {keys}")
    if not table.columns:
        return [head]
    labels = [column.label for column in table.columns]
    shown = " | ".join(labels[:SUMMARY_COLUMNS]) + (" | …" if len(labels) > SUMMARY_COLUMNS else "")
    return [head, f"{indent}  열: {shown}"]


def render_area(structure, handle):
    """One area's lines with each element's number, role and name, then its tables; None for a
    handle this structure does not have."""
    area = structure.area(handle)
    if area is None:
        return None
    lines = [f"{area.handle} 영역 {_area_label(area.container)}"]
    for index, marks in enumerate(area.lines, 1):
        parts = [_detailed(structure, mark) for mark in marks]
        lines.append(f"  줄{index}: " + " · ".join(parts))
    for table_handle in area.tables:
        lines.extend(_table_lines(structure.table(table_handle), "  "))
    if len(lines) == 1:
        lines.append("  (줄도 표도 없음)")
    return "\n".join(lines)


def _detailed(structure, mark):
    member = structure.members.get(mark) or {}
    name = member.get("name")
    return f"[{mark}] {member.get('role') or 'unknown'}" + (f" {_quoted(name)}" if name else "")


def render_table(table, columns=None, start=0):
    """Up to READ_ROWS rows from `start` as "row<i>: 열=값[번호] …", only the chosen columns (all
    when None). A blank cell shows nothing after "=", a missing one "-", and a cell off the screen
    "[번호 화면 밖]". Raises ValueError for a column the table does not have."""
    chosen = _chosen(table, columns)
    total = len(table.rows)
    start = max(0, min(int(start or 0), total))
    rows = table.rows[start:start + READ_ROWS]
    lines = [f"{table.handle} 열: " + " | ".join(table.columns[j].label for j in chosen)]
    for row in rows:
        lines.append(f"row{row.index}: " + " ".join(_shown(table.columns[j].label, row.cells[j]) for j in chosen))
    shown = len(rows)
    following = start + shown if start + shown < total else None
    return {"text": "\n".join(lines), "start": start, "shown": shown, "total": total, "next": following}


def _shown(label, cell):
    if cell is None:
        return f"{label}=-"
    where = f"[{cell.mark} 화면 밖]" if cell.offscreen else f"[{cell.mark}]"
    return f"{label}={cell.value or ''}{where}"


def _chosen(table, columns):
    labels = [column.label for column in table.columns]
    if columns is None:
        return list(range(len(labels)))
    missing = [name for name in columns if name not in labels]
    if missing:
        raise ValueError(f"no such column: {', '.join(missing)}")
    return [labels.index(name) for name in columns]


def find_rows(table, where, columns=None):
    """The rows whose cells equal every value in `where` ({열: 값}), compared with outer spaces
    dropped; a blank or missing cell is "" and is never filled from the row above. Each row gives
    its index, "keys" (the values of the where columns and the key candidate columns) and "cells"
    (the chosen columns, all when None: {"mark", "value", "offscreen"}, or None for a missing
    cell). reason is "no_such_column" for a column the table does not have, "no_match" for no
    row, else None. A column is named by its exact label."""
    labels = [column.label for column in table.columns]
    asked = list(where) + list(columns or [])
    if any(name not in labels for name in asked):
        return {"rows": [], "reason": "no_such_column"}
    keyed = [name for name in labels if name in where or name in _key_columns(table)]
    chosen = _chosen(table, columns)
    found = []
    for row in table.rows:
        if all(_value(row.cells[labels.index(name)]) == str(wanted).strip() for name, wanted in where.items()):
            found.append({
                "index": row.index,
                "keys": {name: _value(row.cells[labels.index(name)]) for name in keyed},
                "cells": {labels[j]: _cell_fields(row.cells[j]) for j in chosen},
            })
    return {"rows": found, "reason": None if found else "no_match"}


def _key_columns(table):
    return {name for candidate in table.key_candidates for name in candidate.split("+")}


def _value(cell):
    return (cell.value or "").strip() if cell is not None else ""


def _cell_fields(cell):
    if cell is None:
        return None
    return {"mark": cell.mark, "value": cell.value, "offscreen": cell.offscreen}


def _quoted(text):
    return json.dumps(str(text), ensure_ascii=False)
