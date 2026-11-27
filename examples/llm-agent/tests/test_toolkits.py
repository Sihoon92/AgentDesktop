import os

import pytest

from agent.survey_facts import FamilyCount
from agent.toolkits import (GRID_FAMILIES, UI_FAMILIES, class_family, count_families, is_dialog_class,
                            is_system_module, module_family)


@pytest.mark.parametrize("name, family", [
    ("WindowsForms10.Window.8.app.0.141b42a_r6_ad1", "WinForms"),
    ("HwndWrapper[App;;1f2e]", "WPF"),
    ("TfrmMain", "Delphi"), ("TApplication", "Delphi"),
    ("SunAwtFrame", "Java"), ("Qt5QWindowIcon", "Qt"), ("Afx:00400000:8", "MFC"),
    ("Chrome_WidgetWin_1", "WebView2"), ("Internet Explorer_Server", "IE"),
    ("#32770", "Win32"), ("Button", "Win32"), ("SysListView32", "Win32"),
    ("ToolbarWindow32", "Win32"), ("MDIClient", "Win32"), ("msctls_statusbar32", "Win32"),
    ("CompanyGridWnd", None), ("tfrmmain", None),
])
def test_class_family(name, family):
    assert class_family(name) == family


@pytest.mark.parametrize("name, family", [
    ("DevExpress.XtraGrid.v21.2.dll", "DevExpress"), ("Infragistics4.Win.UltraWinGrid.v19.1.dll", "Infragistics"),
    ("FPSpreadADO.ocx", "FarPoint"), ("FarPoint.Win.Spread.dll", "FarPoint"),
    ("C1.Win.C1FlexGrid.4.dll", "ComponentOne"), ("Syncfusion.Grid.Windows.dll", "Syncfusion"),
    ("Telerik.WinControls.GridView.dll", "Telerik"), ("AxInterop.SomeCtl.dll", "ActiveX"),
    ("nexacro.exe", "Nexacro"), ("XPlatform.dll", "Nexacro"), ("MiPlatform.dll", "Nexacro"),
    ("WebView2Loader.dll", "WebView2"), ("libcef.dll", "CEF"), ("vcl270.bpl", "Delphi"),
    ("System.Windows.Forms.dll", "WinForms"), ("PresentationFramework.dll", "WPF"), ("jvm.dll", "Java"),
    ("Qt5Core.dll", "Qt"), ("mfc140u.dll", "MFC"), ("mshtml.dll", "IE"), ("CompanyMes.Core.dll", None),
])
def test_module_family(name, family):
    assert module_family(name) == family


def test_every_family_answer_is_a_listed_family():
    for name in ("TfrmMain", "#32770", "WindowsForms10.Window", "Afx:1"):
        assert class_family(name) in UI_FAMILIES
    for name in ("DevExpress.Data.dll", "AxInterop.X.dll"):
        assert module_family(name) in GRID_FAMILIES


def test_system_module_is_under_the_windows_folder():
    assert is_system_module(r"C:\WINDOWS\System32\user32.dll", r"C:\Windows")
    assert not is_system_module(r"C:\Program Files\App\App.dll", r"C:\Windows")
    assert not is_system_module(r"C:\WindowsApps\x.dll", r"C:\Windows")
    assert not is_system_module("", r"C:\Windows")
    assert not is_system_module(os.path.join(os.getcwd(), "a.dll"), "")


def test_count_keeps_unknown_names_sorted_and_unique():
    counted, unknown = count_families(["TfrmA", "TfrmB", "X", "X", "Y"], class_family)
    assert counted == FamilyCount({"Delphi": 2}, 3)
    assert unknown == ("X", "Y")


def test_dialog_class_is_only_32770():
    assert is_dialog_class("#32770")
    assert not is_dialog_class("TfrmDialog")
    assert not is_dialog_class("Button")
