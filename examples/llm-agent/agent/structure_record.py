"""How a table is told apart in a later observation, and the structure as a recorded event.

agent.structure re-exports both; this module only reads its dataclasses.
"""


def table_identity(table, use_ids):
    """How a later observation tells this table: its role and id when ids are stable and it has
    one, else its role and column labels; both with "in", its area, when it sits in one."""
    if use_ids and table.native_id is not None:
        found = {"role": table.role, "id": table.native_id}
    else:
        found = {"role": table.role}
    if table.area is not None:
        found["in"] = dict(table.area)
    if "id" not in found:
        found["columns"] = [column.label for column in table.columns]
    return found


def record(structure):
    """The structure as an event: handles, areas, table identities and ids, column labels and row
    counts. No cell value."""
    return {
        "areas": [{"handle": area.handle, "area": dict(area.container)} for area in structure.areas],
        "tables": [{"handle": table.handle, "identity": table_identity(table, use_ids=False),
                    "id": table.native_id,
                    "columns": [column.label for column in table.columns], "rows": len(table.rows),
                    "key_candidates": list(table.key_candidates)} for table in structure.tables],
    }
