"""Which UI toolkit or grid component a window class or a module file name belongs to.

The tables hold toolkit and component names only, never an application's name. A name that
matches no row is unknown; the caller counts it and keeps the name itself only for the
company-PC-only names file.
"""

import os
import re
from collections.abc import Callable, Iterable

from agent.survey_facts import FamilyCount

UI_FAMILIES = ("WinForms", "WPF", "Win32", "MFC", "Delphi", "Java", "Qt", "Nexacro", "WebView2", "CEF", "IE")
GRID_FAMILIES = ("DevExpress", "Infragistics", "FarPoint", "ComponentOne", "Syncfusion", "Telerik", "ActiveX")

DIALOG_CLASS = "#32770"

_I = re.IGNORECASE
_WIN32_CLASSES = (
    r"#32770", r"Button", r"Edit", r"Static", r"ComboBox", r"ComboBoxEx32", r"ComboLBox", r"ListBox",
    r"ScrollBar", r"SysListView32", r"SysTreeView32", r"SysTabControl32", r"SysHeader32", r"msctls_\w+",
    r"ToolbarWindow32", r"ReBarWindow32", r"MDIClient", r"RichEdit\w*", r"tooltips_class32",
)

_CLASS_TABLE = (
    (re.compile(r"^WindowsForms\d+\.", _I), "WinForms"),
    (re.compile(r"^HwndWrapper\[", _I), "WPF"),
    (re.compile(r"^SunAwt", _I), "Java"),
    (re.compile(r"^Qt\d*Q?Window", _I), "Qt"),
    (re.compile(r"^Afx(:|Wnd|FrameOrView|MDIFrame|ControlBar)", _I), "MFC"),
    (re.compile(r"^Chrome_(WidgetWin_|RenderWidgetHostHWND)", _I), "WebView2"),
    (re.compile(r"^Internet Explorer_Server$", _I), "IE"),
    (re.compile(r"^(" + "|".join(_WIN32_CLASSES) + r")$", _I), "Win32"),
    (re.compile(r"^T[A-Za-z][A-Za-z0-9]+$"), "Delphi"),
)

_MODULE_TABLE = (
    (re.compile(r"^DevExpress\.", _I), "DevExpress"),
    (re.compile(r"^Infragistics", _I), "Infragistics"),
    (re.compile(r"^(FPSpread|FarPoint\.)", _I), "FarPoint"),
    (re.compile(r"^C1\.", _I), "ComponentOne"),
    (re.compile(r"^Syncfusion\.", _I), "Syncfusion"),
    (re.compile(r"^Telerik\.", _I), "Telerik"),
    (re.compile(r"^AxInterop\.", _I), "ActiveX"),
    (re.compile(r"^(nexacro|XPlatform|MiPlatform)", _I), "Nexacro"),
    (re.compile(r"^(WebView2Loader|EmbeddedBrowserWebView)\.dll$", _I), "WebView2"),
    (re.compile(r"^libcef\.dll$", _I), "CEF"),
    (re.compile(r"^(mshtml|ieframe)\.dll$", _I), "IE"),
    (re.compile(r"^(vcl|rtl)\d*\w*\.bpl$", _I), "Delphi"),
    (re.compile(r"^System\.Windows\.Forms(\.|$)", _I), "WinForms"),
    (re.compile(r"^(PresentationFramework|PresentationCore)(\.|$)", _I), "WPF"),
    (re.compile(r"^(jvm|awt|java)\.dll$", _I), "Java"),
    (re.compile(r"^Qt\d*(Core|Gui|Widgets)", _I), "Qt"),
    (re.compile(r"^mfc\d+u?d?\.dll$", _I), "MFC"),
)


def _first(table, name: str) -> str | None:
    for pattern, family in table:
        if pattern.search(name):
            return family
    return None


def class_family(name: str) -> str | None:
    """The family of one window class name, or None when no row knows it."""
    return _first(_CLASS_TABLE, name or "")


def module_family(file_name: str) -> str | None:
    """The UI or grid family of one module file name (no folder), or None."""
    return _first(_MODULE_TABLE, file_name or "")


def is_dialog_class(name: str) -> bool:
    return name == DIALOG_CLASS


def is_system_module(path: str, windows_dir: str) -> bool:
    """True when `path` lies under `windows_dir`, ignoring case."""
    if not path or not windows_dir:
        return False
    root = os.path.normcase(os.path.abspath(windows_dir)).rstrip("\\/") + os.sep
    return os.path.normcase(os.path.abspath(path)).startswith(root)


def count_families(names: Iterable[str],
                   classify: Callable[[str], str | None]) -> tuple[FamilyCount, tuple[str, ...]]:
    """Counts by family and returns the unknown names apart, sorted and without repeats."""
    families: dict[str, int] = {}
    unknown = 0
    unknown_names = set()
    for name in names:
        family = classify(name)
        if family is None:
            unknown += 1
            unknown_names.add(name)
        else:
            families[family] = families.get(family, 0) + 1
    return FamilyCount(families, unknown), tuple(sorted(unknown_names))
