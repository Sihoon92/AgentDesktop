"""The tools that read the structure of the latest observation: open_area, read_table, find_rows.

They change nothing in the app and take only the handles (A1, T1) of the latest observation's
structure (agent.structure, kept by the toolbox as _structure). find_rows that matches two rows
or more tells the asking part every cell of those rows, so pressing one needs a question first;
when one of them is off the screen, the whole table is held until then, because a cell without
a box cannot be told apart by its place.
"""

from agent.geometry import has_area
from agent.results import refused
from agent.structure import find_rows, render_area, render_table


class TableTools:
    def _tool_open_area(self, arguments):
        structure = getattr(self, "_structure", None)
        text = render_area(structure, arguments["area"]) if structure is not None else None
        if text is None:
            return refused(f"{arguments['area']} is not an area of the latest observation. Observe again.")
        return {"ok": True, "area": arguments["area"], "text": text}

    def _tool_read_table(self, arguments):
        table = self._latest_table(arguments["table"])
        if table is None:
            return self._no_table(arguments["table"])
        try:
            shown = render_table(table, arguments.get("columns"), arguments.get("start") or 0)
        except ValueError as error:
            return refused(f"{error}. Columns: {self._labels(table)}")
        return {"ok": True, "table": table.handle, **shown}

    def _tool_find_rows(self, arguments):
        handle = arguments["table"]
        table = self._latest_table(handle)
        if table is None:
            return self._no_table(handle)
        conditions = arguments.get("where") or []
        where = {item["column"]: item["value"] for item in conditions}
        if len(where) != len(conditions):
            return refused("Give each column once in where.")
        found = find_rows(table, where, arguments.get("columns"))
        rows = found["rows"]
        if len(rows) >= 2:
            self._note_cells(self._row_candidates(table, {row["index"] for row in rows}), self._table_box(table))
        else:
            self._note_cells([])
        cells = [{"mark": cell["mark"], "column": column, "row": row["index"]}
                 for row in rows for column, cell in row["cells"].items() if cell is not None]
        self._emit({"type": "find_rows", "table": handle, "where": where, "matched": len(rows),
                    "reason": found["reason"], "marks": [cell["mark"] for cell in cells], "cells": cells})
        result = {"ok": True, "table": handle, "matched": len(rows), "rows": rows, "reason": found["reason"]}
        if found["reason"] == "no_such_column":
            result["columns"] = [column.label for column in table.columns]
        if len(rows) >= 2:
            result["note"] = "Two or more rows match: ask before you press one of their cells."
        return result

    def _latest_table(self, handle):
        structure = getattr(self, "_structure", None)
        return structure.table(handle) if structure is not None else None

    def _no_table(self, handle):
        return refused(f"{handle} is not a table of the latest observation. Observe again.")

    @staticmethod
    def _labels(table):
        return " | ".join(column.label for column in table.columns)

    def _row_candidates(self, table, indexes):
        """Every cell of the matched rows as {"bounds"}: the element's box, or None off the screen."""
        candidates = []
        for row in table.rows:
            if row.index not in indexes:
                continue
            for cell in row.cells:
                if cell is not None:
                    element = self._marks.get(cell.mark) or {}
                    bounds = None if cell.offscreen else element.get("bounds")
                    candidates.append({"bounds": bounds})
        return candidates

    def _table_box(self, table):
        """The table's box, else the box around its cells on the screen, else None."""
        box = self._structure.table_boxes.get(table.handle)
        if has_area(box):
            return box
        boxes = [self._marks[cell.mark]["bounds"] for row in table.rows for cell in row.cells
                 if cell is not None and not cell.offscreen and cell.mark in self._marks
                 and has_area(self._marks[cell.mark].get("bounds"))]
        if not boxes:
            return None
        left, top = min(b["x"] for b in boxes), min(b["y"] for b in boxes)
        right = max(b["x"] + b["width"] for b in boxes)
        bottom = max(b["y"] + b["height"] for b in boxes)
        return {"x": left, "y": top, "width": right - left, "height": bottom - top}
