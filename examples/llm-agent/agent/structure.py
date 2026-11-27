"""Works out the areas, lines and tables of one snapshot tree for the model.

Everything here is computed from roles, boxes, the shape of names and values, never from what
an app is called or what its screen says. The numbers are the observation's own (agent.observe
numbered_nodes): nothing here numbers anything, so render_marked, visible_elements and the
recorder keep agreeing.

- A table is a node whose role is table or grid, or else the innermost node holding cells in
  two rows or more. A cell is an element whose role is cell, or, inside a table or grid role, a
  row element with no element below it (Windows gives a DataItem without GridItem or TableItem
  the role row). A table's rows are the nodes the cells sit straight under; when the cells sit
  straight under the table, their boxes' vertical overlap groups them.
- A column is named, one column at a time, by a header element above the cells that overlaps it
  sideways (a label used twice gets " (2)"), else by the cells' names with each run of digits
  read as "#" when that shape is the same in every cell of the column, else by its position.
- An area is a node with an id or a name (agent.identity) that is no control, sits outside every
  table, and holds an element or a table straight, or two areas. Unnamed wrappers fold away.
- A line is the elements of one area, outside its tables, that overlap vertically, left to right.
"""

import re
from dataclasses import dataclass, field
from itertools import combinations

from agent import identity
from agent.locator import box, overlaps
from agent.observe import numbered_nodes
from agent.table import NUMERIC

TABLE_ROLES = frozenset({"table", "grid"})
CELL_ROLE = "cell"
ROW_ROLE = "row"
OFFSCREEN = "offscreen"
DIGITS = re.compile(r"\d+")
LETTER = re.compile(r"[^\W\d_]")


@dataclass(frozen=True)
class Cell:
    mark: int
    value: str | None
    offscreen: bool


@dataclass(frozen=True)
class Column:
    label: str
    rule: str


@dataclass(frozen=True)
class Row:
    index: int
    cells: tuple


@dataclass(frozen=True)
class Table:
    handle: str
    mark: int | None
    role: str
    native_id: str | None
    area: dict | None
    columns: tuple
    rows: tuple
    key_candidates: tuple


@dataclass(frozen=True)
class Area:
    handle: str
    container: dict
    lines: tuple
    tables: tuple


@dataclass(frozen=True)
class Structure:
    """areas, tables and cell_marks are the analysis; members gives each line element's
    {"role", "name", "control"} by its number and table_boxes each table's box (or None) by its
    handle, for the text and the tools."""

    areas: tuple
    tables: tuple
    cell_marks: frozenset
    members: dict = field(default_factory=dict, compare=False)
    table_boxes: dict = field(default_factory=dict, compare=False)

    def table(self, handle):
        return next((table for table in self.tables if table.handle == handle), None)

    def area(self, handle):
        return next((area for area in self.areas if area.handle == handle), None)


class _Tree:
    """The observation's nodes in tree order with their numbers, areas and parents."""

    def __init__(self, tree, profile):
        self.root = tree
        self.order = []
        self.mark = {}
        self.container = {}
        self.parent = {}
        for node, mark, container in numbered_nodes(tree, profile):
            self.order.append(node)
            self.mark[id(node)] = mark
            self.container[id(node)] = container
        self.position = {id(node): index for index, node in enumerate(self.order)}
        for node in self.order:
            for child in node.get("children") or []:
                self.parent[id(child)] = node

    def is_element(self, node):
        return self.mark.get(id(node)) is not None

    def is_cell(self, node):
        return node.get("role") == CELL_ROLE and self.is_element(node)

    def is_leaf_row(self, node):
        """A row element with no element below it: what Windows calls an item addressable by
        no row and column (a DataItem without GridItem or TableItem)."""
        return (node.get("role") == ROW_ROLE and self.is_element(node)
                and not any(self.is_element(below) for below in _subtree(node) if below is not node))


def analyze(tree, profile=None):
    """The areas, tables and cell numbers of one observation. The tree is not changed."""
    nodes = _Tree(tree, profile)
    found = _tables(nodes)
    tables = tuple(_table(nodes, f"T{index}", node, cells) for index, (node, cells) in enumerate(found, 1))
    inside = {id(member) for node, _ in found for member in _subtree(node)}
    areas, members = _areas(nodes, inside, [(table.handle, node) for table, (node, _) in zip(tables, found)])
    cell_marks = frozenset(nodes.mark[id(cell)] for _, cells in found for cell in cells)
    table_boxes = {table.handle: box(node) for table, (node, _) in zip(tables, found)}
    return Structure(areas, tables, cell_marks, members, table_boxes)


def _subtree(node):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.get("children") or []))


def _tables(nodes):
    """(table node, its cells in tree order) in tree order. A table role inside another table
    belongs to the outer one. Inside a table role a leaf row element is a cell too; outside one
    it is not, so the rows of a list never make a table."""
    owner = {}
    for node in nodes.order:
        parent = nodes.parent.get(id(node))
        outer = owner.get(id(parent)) if parent is not None else None
        owner[id(node)] = outer or (node if node.get("role") in TABLE_ROLES else None)
    found = {id(node): (node, []) for node in nodes.order if owner[id(node)] is node}
    for node in nodes.order:
        if owner[id(node)] is not None and (nodes.is_cell(node) or nodes.is_leaf_row(node)):
            found[id(owner[id(node)])][1].append(node)
    held, has_table = {}, {}
    for node in reversed(nodes.order):
        children = node.get("children") or []
        cells = [node] if owner[id(node)] is None and nodes.is_cell(node) else []
        for child in children:
            cells.extend(held.pop(id(child)))
        below = any(has_table.pop(id(child)) for child in children)
        cells.sort(key=lambda cell: nodes.position[id(cell)])
        if not below and owner[id(node)] is None and len(cells) >= 2 and len(_rows(nodes, node, cells)) >= 2:
            found[id(node)] = (node, cells)
            below = True
        held[id(node)], has_table[id(node)] = cells, below
    return sorted(found.values(), key=lambda pair: nodes.position[id(pair[0])])


def _rows(nodes, table, cells):
    """The table's cells as rows: by the node each sits straight under, or, when any sits straight
    under the table, by vertical overlap top to bottom, with every cell without a box last."""
    parents = [nodes.parent.get(id(cell)) for cell in cells]
    if all(parent is not table for parent in parents):
        grouped = {}
        for cell, parent in zip(cells, parents):
            grouped.setdefault(id(parent), []).append(cell)
        return list(grouped.values())
    rows = _bands([cell for cell in cells if box(cell)])
    unplaced = [cell for cell in cells if not box(cell)]
    return rows + [unplaced] if unplaced else rows


def _bands(nodes):
    """Boxed nodes in lines that overlap vertically, top to bottom, each left to right."""
    boxed = sorted(((box(node), node) for node in nodes), key=lambda pair: (pair[0]["y"], pair[0]["x"]))
    lines = []
    for node_box, node in boxed:
        line = next((line for line in lines if overlaps(line[0][0], node_box, "y")), None)
        if line is None:
            lines.append([(node_box, node)])
        else:
            line.append((node_box, node))
    return [[node for _, node in sorted(line, key=lambda pair: pair[0]["x"])] for line in lines]


def _table(nodes, handle, node, cells):
    rows = _rows(nodes, node, cells) if cells else []
    width = max((len(row) for row in rows), default=0)
    full = [row for row in rows if len(row) == width]
    column_boxes = [next((box(row[j]) for row in full if box(row[j])), None) for j in range(width)]
    placed = [list(row) if len(row) == width else _place(row, column_boxes) for row in rows]
    headers = _headers(nodes, node, cells)
    columns, shaped = [], []
    for j in range(width):
        shape = _shape([row[j] for row in placed if row[j] is not None])
        header = _header(headers, column_boxes[j])
        if header is not None:
            columns.append(Column(header, "header"))
        elif shape is not None:
            columns.append(Column(shape, "pattern"))
        else:
            columns.append(Column(f"열{j + 1}", "index"))
        shaped.append(shape is not None)
    columns = _distinct(columns)
    table_rows = tuple(Row(index, tuple(None if cell is None else _cell(nodes, cell, shaped[j])
                                        for j, cell in enumerate(row)))
                       for index, row in enumerate(placed))
    return Table(handle, nodes.mark[id(node)], node.get("role"), identity.native_value(node),
                 nodes.container[id(node)], tuple(columns), table_rows, _keys(columns, table_rows))


def _distinct(columns):
    """The columns with a label that repeats an earlier one numbered " (2)", " (3)": a label
    names one column only."""
    seen = {}
    distinct = []
    for column in columns:
        seen[column.label] = seen.get(column.label, 0) + 1
        label = column.label if seen[column.label] == 1 else f"{column.label} ({seen[column.label]})"
        distinct.append(Column(label, column.rule))
    return distinct


def _place(row, column_boxes):
    """A row with fewer cells: each cell goes to the next column whose box it overlaps sideways,
    else to the column after the last one filled."""
    slots = [None] * len(column_boxes)
    last = -1
    for cell in row:
        cell_box = box(cell)
        column = next((j for j in range(last + 1, len(slots))
                       if cell_box and column_boxes[j] and overlaps(column_boxes[j], cell_box, "x")), last + 1)
        if column >= len(slots):
            break
        slots[column] = cell
        last = column
    return slots


def _headers(nodes, table, cells):
    """(box, name) of the named elements in the table, not cells, that end above the middle of
    its top cell."""
    tops = [box(cell) for cell in cells if box(cell)]
    if not tops:
        return []
    top = min(tops, key=lambda each: each["y"])
    limit = top["y"] + top["height"] / 2
    chosen = set(map(id, cells))
    found = []
    for node in _subtree(table):
        node_box, name = box(node), _text(node.get("name"))
        if (nodes.is_element(node) and id(node) not in chosen and name and node_box
                and node_box["y"] + node_box["height"] <= limit):
            found.append((node_box, name))
    return found


def _header(headers, column_box):
    """The name of the lowest header that overlaps the column sideways, or None."""
    if column_box is None:
        return None
    over = [(node_box, name) for node_box, name in headers if overlaps(node_box, column_box, "x")]
    if not over:
        return None
    return max(over, key=lambda pair: pair[0]["y"] + pair[0]["height"])[1]


def _shape(cells):
    """The names' shape with each run of digits as "#", when every cell has a name of that same
    shape and it holds a digit run and a letter."""
    names = [_text(cell.get("name")) for cell in cells]
    if not names or None in names:
        return None
    shapes = {DIGITS.sub("#", name or "") for name in names}
    if len(shapes) != 1:
        return None
    shape = shapes.pop()
    return shape if "#" in shape and LETTER.search(shape) else None


def _cell(nodes, node, named_by_shape):
    """The cell's value, else its name unless the names only label the column; blank is None."""
    value = _text(node.get("value"))
    if value is None and not named_by_shape:
        value = _text(node.get("name"))
    offscreen = box(node) is None or OFFSCREEN in (node.get("states") or [])
    return Cell(nodes.mark[id(node)], value, offscreen)


def _keys(columns, rows):
    """Labels of the columns whose values tell every row apart, or one pair of them, or none.
    A column that has a blank cell or only numbers is never one."""
    usable = []
    for j, column in enumerate(columns):
        values = [(row.cells[j].value or "").strip() if row.cells[j] is not None else "" for row in rows]
        if not values or "" in values or all(NUMERIC.match(value) for value in values):
            continue
        usable.append((column.label, values))
    singles = tuple(label for label, values in usable if len(set(values)) == len(values))
    if singles:
        return singles
    for (first, first_values), (second, second_values) in combinations(usable, 2):
        pairs = list(zip(first_values, second_values))
        if len(set(pairs)) == len(pairs):
            return (f"{first}+{second}",)
    return ()


def _areas(nodes, inside, tables):
    """Areas in tree order with their lines and the handles of the tables they hold, and the
    line elements' roles and names by number."""
    def can_be_area(node):
        is_root_window = node is nodes.root and node.get("role") == "window"
        return (not is_root_window and id(node) not in inside and not node.get("ref_id")
                and identity.as_container(node) is not None)

    table_ids = {id(node) for _, node in tables}
    holder, direct = {}, {}
    for node in nodes.order:
        parent = nodes.parent.get(id(node))
        above = holder.get(id(parent)) if parent is not None else None
        if above is not None and (nodes.is_element(node) or id(node) in table_ids):
            direct.setdefault(id(above), []).append(node)
        holder[id(node)] = node if can_be_area(node) else above
    kept, tops = set(), {}
    for node in reversed(nodes.order):
        below = []
        for child in node.get("children") or []:
            below.extend([child] if id(child) in kept else tops.pop(id(child)))
        if can_be_area(node) and (direct.get(id(node)) or len(below) >= 2):
            kept.add(id(node))
        tops[id(node)] = below
    table_handles = {}
    for handle, node in tables:
        above = nodes.parent.get(id(node))
        while above is not None and id(above) not in kept:
            above = nodes.parent.get(id(above))
        if above is not None:
            table_handles.setdefault(id(above), []).append(handle)
    areas, members = [], {}
    for node in (node for node in nodes.order if id(node) in kept):
        chosen = [element for element in direct.get(id(node), [])
                   if id(element) not in inside and id(element) not in kept and box(element)]
        lines = tuple(tuple(nodes.mark[id(element)] for element in line) for line in _bands(chosen))
        members.update((nodes.mark[id(element)], {"role": element.get("role"), "name": _text(element.get("name")),
                                                  "control": bool(element.get("ref_id"))}) for element in chosen)
        areas.append(Area(f"A{len(areas) + 1}", identity.as_container(node), lines,
                          tuple(table_handles.get(id(node), ()))))
    return tuple(areas), members


def _text(value):
    if value is None or isinstance(value, bool):
        return None
    stripped = str(value).strip()
    return stripped or None


from agent.structure_record import record, table_identity  # noqa: E402,F401
from agent.structure_view import READ_ROWS, find_rows, render_area, render_summary, render_table  # noqa: E402,F401
