using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>One screen: its name tab, the filter column on the left and the data panel on the right.</summary>
    internal sealed class ScreenPanel : Panel
    {
        private readonly Session session;
        private readonly ScreenSpec spec;
        private readonly MainForm frame;
        private readonly Surface tabStrip;
        private readonly Surface title;
        private readonly Panel filterHost;
        private readonly Panel dataHeader;
        private readonly Panel gridHost;
        private readonly FilterColumn filters;
        private readonly Surface grid;
        private bool querying;
        private CellPopup cellPopup;

        internal ScreenPanel(Session session, ScreenSpec spec, MainForm frame)
        {
            this.session = session;
            this.spec = spec;
            this.frame = frame;
            this.Dock = DockStyle.Fill;
            this.BackColor = Ui.WindowBack;

            Exposure grid = session.ExposureOf(UiRegion.Grid);

            this.gridHost = new Panel();
            this.gridHost.Dock = DockStyle.Fill;
            this.gridHost.BackColor = Color.White;
            this.gridHost.AutoScroll = true;

            this.grid = new Surface(UiRegion.Grid, grid);
            this.grid.Location = Point.Empty;
            this.grid.Size = Size.Empty;
            this.grid.BackgroundRightClick = this.OpenRadial;
            this.gridHost.Controls.Add(this.grid);
            this.gridHost.MouseUp += delegate(object sender, MouseEventArgs e)
            {
                if (e.Button == MouseButtons.Right)
                {
                    this.OpenRadial(this.gridHost.PointToScreen(e.Location));
                }
            };

            this.title = new Surface(UiRegion.Grid, grid);
            this.title.Dock = DockStyle.Fill;
            this.title.BackColor = Ui.HeaderBack;

            this.dataHeader = new Panel();
            this.dataHeader.Dock = DockStyle.Top;
            this.dataHeader.Height = Ui.Px(30);
            this.dataHeader.BackColor = Ui.HeaderBack;
            this.dataHeader.Controls.Add(this.title);
            if (spec.HasIcon)
            {
                this.dataHeader.Controls.Add(this.BuildIcon());
            }

            Panel dataHost = new Panel();
            dataHost.Dock = DockStyle.Fill;
            dataHost.Controls.Add(this.gridHost);
            dataHost.Controls.Add(this.dataHeader);

            Panel divider = new Panel();
            divider.Dock = DockStyle.Left;
            divider.Width = 1;
            divider.BackColor = Ui.Line;

            this.filterHost = new Panel();
            this.filterHost.Dock = DockStyle.Left;
            this.filterHost.Width = Ui.Px(236);
            this.filterHost.BackColor = Ui.PanelBack;

            this.tabStrip = new Surface(UiRegion.Grid, grid);
            this.tabStrip.Dock = DockStyle.Top;
            this.tabStrip.Height = Ui.Px(26);
            this.tabStrip.BackColor = Ui.StatusBack;

            this.Controls.Add(dataHost);
            this.Controls.Add(divider);
            this.Controls.Add(this.filterHost);
            this.Controls.Add(this.tabStrip);

            this.filters = new FilterColumn(session, spec, frame, this.filterHost);
            this.filters.QueryRequested += delegate
            {
                this.frame.BeginInvoke(new MethodInvoker(delegate { this.RequestQuery("button"); }));
            };
            this.BuildNames();
        }

        /// <summary>
        /// Runs a query: validates the period, shows the loading window for the
        /// wait, then fills the table. A request during a running query is ignored.
        /// </summary>
        internal void RequestQuery(string startedBy)
        {
            if (this.spec.Kind == ScreenSpec.KindEmpty || this.querying)
            {
                return;
            }
            PopupHost.CloseAll(PopupHost.ReasonOutsideClick);
            this.filters.CommitDates();

            DateTime from;
            DateTime to;
            string error;
            if (!this.session.Filters.TryResolvePeriod(out from, out to, out error))
            {
                this.session.RejectQuery("invalid_period");
                Alerts.Show(this.frame, this.session.Options, error, "조회", MessageBoxIcon.Warning);
                return;
            }

            FilterValues filters = this.session.Filters.Clone();
            int delayMs = this.frame.Runner.NextDelayMs();
            this.session.StartQuery(filters, startedBy, delayMs);
            this.querying = true;
            this.frame.Busy = true;
            try
            {
                QueryRunner.ShowLoading(this.frame, delayMs);
            }
            finally
            {
                this.querying = false;
                this.frame.Busy = false;
            }

            if (this.IsDisposed)
            {
                return;
            }
            QueryResult result = MesData.Query(this.spec.Id, filters);
            this.session.FinishQuery(result, filters);
            this.ShowResult(result);
            this.frame.SetStatus("조회 완료 · " + result.Rows.Count + "건");
        }

        private Surface BuildIcon()
        {
            Exposure exposure = this.session.ExposureOf(UiRegion.Icon);
            Surface icon = new Surface(UiRegion.Icon, exposure);
            icon.Dock = DockStyle.Right;
            icon.Width = Ui.Px(30);
            icon.BackColor = Ui.HeaderBack;

            SurfaceItem item = new SurfaceItem();
            item.Id = "excel-icon";
            item.Kind = ItemKind.Graphic;
            item.Text = exposure == Exposure.Ref ? "엑셀 저장" : string.Empty;
            item.Bounds = new Rectangle(0, 0, icon.Width, Ui.Px(30));
            item.Paint = ExportFlow.PaintIcon;
            item.Click = delegate
            {
                this.frame.BeginInvoke(new MethodInvoker(delegate
                {
                    PopupHost.CloseAll(PopupHost.ReasonOutsideClick);
                    ExportFlow.FromIcon(this.frame, this.session, this.spec.Title + ".xlsx");
                }));
            };
            icon.SetItems(new[] { item });
            return icon;
        }

        private void ShowResult(QueryResult result)
        {
            this.gridHost.AutoScrollPosition = Point.Empty;
            Action<int, int> cellClick = null;
            if (this.spec.Kind == ScreenSpec.KindMatrix)
            {
                cellClick = this.OpenCellPopup;
            }
            this.CloseCellPopup();
            if (this.spec.Kind == ScreenSpec.KindGrouped)
            {
                GridSurface.FillGrouped(
                    this.grid, result, this.gridHost.ClientSize.Width, this.OpenSummaryCell, this.OpenRadial);
                return;
            }
            GridSurface.Fill(this.grid, result, this.gridHost.ClientSize.Width, cellClick, this.OpenRadial);
        }

        /// <summary>
        /// Opens the detail window for a number cell of the grouped table and records the cell by
        /// its three row names and its upper and lower header names.
        /// </summary>
        private void OpenSummaryCell(int row, int column)
        {
            QueryResult result = this.session.Result;
            if (result == null || this.session.ResultFilters == null || row > result.Rows.Count
                || column < result.LabelColumns)
            {
                return;
            }
            string[] values = result.Rows[row - 1];
            string[] rowNames = { values[0], values[1], values[2] };
            string[] columnNames = { result.GroupOf(column), result.Header[column] };
            List<string> named = new List<string>();
            foreach (string name in rowNames)
            {
                if (name.Length > 0)
                {
                    named.Add(name);
                }
            }
            string rowText = string.Join(" · ", named.ToArray());
            QueryResult detail = MesData.SummaryDetail(this.session.ResultFilters.Clone(), values[0], values[1]);

            PopupHost.CloseAll(PopupHost.ReasonOutsideClick);
            this.CloseCellPopup();
            this.session.CellClicked(rowNames, columnNames);
            this.session.CellPopupOpened(rowText, columnNames[1]);
            this.cellPopup = new CellPopup(
                this.session, this.frame, "생산 상세 · " + rowText + " · " + columnNames[1],
                "생산 상세 " + rowText.Replace(" · ", " ") + ".xlsx", detail);
            this.cellPopup.Show(this.frame);
        }

        /// <summary>Opens the detail window for a number cell. The first column holds row names and opens nothing.</summary>
        private void OpenCellPopup(int row, int column)
        {
            QueryResult result = this.session.Result;
            if (column == 0 || result == null || this.session.ResultFilters == null || row > result.Rows.Count)
            {
                return;
            }
            string rowName = result.Rows[row - 1][0];
            string columnName = result.Header[column];
            FilterValues filters = this.session.ResultFilters.Clone();
            filters.Row = rowName;
            QueryResult detail = MesData.Query(MesData.DefectDetail, filters);

            PopupHost.CloseAll(PopupHost.ReasonOutsideClick);
            this.CloseCellPopup();
            this.session.CellPopupOpened(rowName, columnName);
            this.cellPopup = new CellPopup(
                this.session, this.frame, "불량 상세 · " + rowName + " · " + columnName,
                "불량 상세 " + rowName + ".xlsx", detail);
            this.cellPopup.Show(this.frame);
        }

        private void CloseCellPopup()
        {
            if (this.cellPopup != null)
            {
                CellPopup old = this.cellPopup;
                this.cellPopup = null;
                old.Close();
                old.Dispose();
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                this.CloseCellPopup();
            }
            base.Dispose(disposing);
        }

        /// <summary>Opens the round menu centred on the right-clicked point of the data panel.</summary>
        private void OpenRadial(Point screen)
        {
            RadialMenu.Show(
                this.frame, screen, this.session, delegate { return this.session.Result; },
                ExportFlow.SourcePanel, this.spec.Title + ".xlsx");
        }

        private void BuildNames()
        {
            SurfaceItem tab = new SurfaceItem();
            tab.Id = "tab";
            tab.Text = this.spec.Title;
            tab.Bold = true;
            tab.Back = Color.White;
            tab.Border = true;
            tab.Align = ContentAlignment.MiddleCenter;
            int width = TextRenderer.MeasureText(this.spec.Title, Ui.Bold).Width + Ui.Px(28);
            tab.Bounds = new Rectangle(Ui.Px(8), Ui.Px(3), width, this.tabStrip.Height - Ui.Px(3));
            this.tabStrip.SetItems(new[] { tab });

            SurfaceItem name = new SurfaceItem();
            name.Id = "screen-title";
            name.Text = this.spec.Title;
            name.Bold = true;
            name.Bounds = new Rectangle(Ui.Px(4), 0, Ui.Px(420), this.dataHeader.Height);
            this.title.SetItems(new[] { name });
        }
    }
}
