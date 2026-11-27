from agent.deep_read import DEEP_DEPTH, TIMED_OUT, TOO_LARGE, WindowRead, count_cut, read_window

TREE = {"role": "window", "children": [
    {"role": "pane", "children_count": 200},
    {"role": "group", "children": [{"role": "button", "name": "OK"}, {"role": "pane", "children_count": 3}]},
]}


def ok(tree=TREE):
    return {"ok": True, "data": {"tree": tree, "complete": True}}


def failed(code, message="failed"):
    return {"ok": False, "error": {"code": code, "message": message}}


class FakeDesktop:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def run(self, *args):
        self.calls.append(list(args))
        return self.replies.pop(0)


def test_reads_at_depth_30_first():
    desktop = FakeDesktop(ok())

    read = read_window(desktop, "w-1", "8000")

    assert DEEP_DEPTH == 30
    assert desktop.calls == [["snapshot", "--window-id", "w-1", "--compact", "--timeout-ms", "8000",
                              "--max-depth", "30", "--include-bounds"]]
    assert read == WindowRead(envelope=ok(), depth=30, fallback=None, cut=2)


def test_a_refmap_too_large_falls_back_to_the_default_depth():
    refused = failed("INTERNAL", "RefMap exceeds 1MB size limit on write")
    second = ok({"role": "window"})
    desktop = FakeDesktop(refused, second)

    read = read_window(desktop, "w-1", "8000")

    assert len(desktop.calls) == 2
    assert "--max-depth" in desktop.calls[0] and "--max-depth" not in desktop.calls[1]
    assert desktop.calls[1] == ["snapshot", "--window-id", "w-1", "--compact", "--timeout-ms", "8000",
                                "--include-bounds"]
    assert read.fallback == TOO_LARGE == "deep_read_too_large"
    assert read.depth is None and read.envelope is second and read.cut == 0


def test_a_timeout_falls_back():
    desktop = FakeDesktop(failed("TIMEOUT"), ok())

    read = read_window(desktop, "w-1", "8000")

    assert read.fallback == TIMED_OUT == "deep_read_timeout"
    assert read.depth is None and len(desktop.calls) == 2


def test_other_failures_are_returned_without_a_second_read():
    other_internal = failed("INTERNAL", "something else")
    for reply in (failed("WINDOW_NOT_FOUND"), other_internal):
        desktop = FakeDesktop(reply)

        read = read_window(desktop, "w-1", "8000")

        assert len(desktop.calls) == 1
        assert read == WindowRead(envelope=reply, depth=30, fallback=None, cut=0)


def test_a_failed_fallback_is_returned_with_its_code():
    second = failed("TIMEOUT")
    desktop = FakeDesktop(failed("TIMEOUT"), second)

    read = read_window(desktop, "w-1", "8000")

    assert read.envelope is second and read.fallback == TIMED_OUT and read.cut == 0


def test_count_cut_counts_nodes_with_children_count():
    assert count_cut(TREE) == 2
    assert count_cut({"role": "window"}) == 0
    assert count_cut({"role": "window", "children_count": 0}) == 1


def test_extra_args_reach_the_cli():
    desktop = FakeDesktop(failed("TIMEOUT"), ok())

    read_window(desktop, "w-1", "8000", extra=("--root", "@s:e3"), include_bounds=False, depth=20)

    assert desktop.calls == [
        ["snapshot", "--window-id", "w-1", "--compact", "--timeout-ms", "8000", "--max-depth", "20",
         "--root", "@s:e3"],
        ["snapshot", "--window-id", "w-1", "--compact", "--timeout-ms", "8000", "--root", "@s:e3"],
    ]
