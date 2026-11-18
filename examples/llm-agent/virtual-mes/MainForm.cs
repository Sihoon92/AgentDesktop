using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>The app frame: menu bar on top, the current screen in the middle, status line at the bottom.</summary>
    internal sealed class MainForm : Form
    {
        internal const string WindowTitle = "MES (가상)";

        internal static readonly string[] Categories =
        {
            "기준정보", "생산운영", "품질관리", "설비관리", "자재관리"
        };

        private const string ProductionCategory = "생산운영";

        private readonly Session session;
        private readonly Surface menuBar;
        private readonly Panel body;
        private readonly Surface status;
        private readonly QueryRunner runner;
        private ScreenPanel screen;

        /// <summary>True while a query is running. The window does not close until it ends.</summary>
        internal bool Busy;

        internal MainForm(Session session)
        {
            this.session = session;
            Options options = session.Options;
            this.runner = new QueryRunner(options);
            this.Text = WindowTitle;
            this.AutoScaleMode = AutoScaleMode.None;
            this.Font = Ui.Font;
            this.BackColor = Ui.WindowBack;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.ClientSize = new Size(Ui.Px(1100), Ui.Px(680));
            this.MinimumSize = new Size(Ui.Px(760), Ui.Px(480));

            this.body = new Panel();
            this.body.Dock = DockStyle.Fill;
            this.body.BackColor = Ui.WindowBack;

            this.status = new Surface(UiRegion.Grid, options.Expose[UiRegion.Grid]);
            this.status.Dock = DockStyle.Bottom;
            this.status.Height = Ui.Px(22);
            this.status.BackColor = Ui.StatusBack;

            this.menuBar = new Surface(UiRegion.Menu, options.Expose[UiRegion.Menu]);
            this.menuBar.Dock = DockStyle.Top;
            this.menuBar.Height = Ui.Px(30);
            this.menuBar.BackColor = Ui.Accent;

            this.Controls.Add(this.body);
            this.Controls.Add(this.status);
            this.Controls.Add(this.menuBar);

            this.SetStatus("준비");
            session.MainStarted();
            if (options.MenuDelayMs.HasValue)
            {
                this.FillMenuLater(options.MenuDelayMs.Value);
            }
            else
            {
                this.BuildMenuBar();
                this.Shown += delegate { session.MenuReady(); };
            }
        }

        /// <summary>Shows the window with an empty menu bar and fills it after the delay.</summary>
        private void FillMenuLater(int delayMs)
        {
            Timer timer = new Timer();
            timer.Interval = Math.Max(1, delayMs);
            timer.Tick += delegate
            {
                timer.Stop();
                timer.Dispose();
                this.BuildMenuBar();
                this.session.MenuReady();
            };
            this.Shown += delegate { timer.Start(); };
        }

        internal QueryRunner Runner
        {
            get { return this.runner; }
        }

        protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
        {
            if (keyData == Keys.F5)
            {
                if (this.screen != null)
                {
                    this.screen.RequestQuery("f5");
                }
                return true;
            }
            return base.ProcessCmdKey(ref msg, keyData);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (this.Busy)
            {
                e.Cancel = true;
                return;
            }
            PopupHost.CloseAll(PopupHost.ReasonDeactivated);
            base.OnFormClosing(e);
        }

        internal void SetStatus(string text)
        {
            SurfaceItem item = new SurfaceItem();
            item.Id = "status";
            item.Text = text;
            item.Bounds = new Rectangle(Ui.Px(4), 0, Ui.Px(600), this.status.Height);
            this.status.SetItems(new[] { item });
        }

        private void BuildMenuBar()
        {
            List<SurfaceItem> items = new List<SurfaceItem>();
            int left = Ui.Px(6);
            for (int index = 0; index < Categories.Length; index++)
            {
                int width = TextRenderer.MeasureText(Categories[index], Ui.Font).Width + Ui.Px(24);
                SurfaceItem item = new SurfaceItem();
                item.Id = "menu-" + index;
                item.Kind = ItemKind.Button;
                item.Text = Categories[index];
                item.Fore = Color.White;
                item.Align = ContentAlignment.MiddleCenter;
                item.Bounds = new Rectangle(left, 0, width, this.menuBar.Height);
                int category = index;
                item.Click = delegate { this.OpenCategory(category, item.Bounds); };
                items.Add(item);
                left += width + Ui.Px(2);
            }
            this.menuBar.SetItems(items);
        }

        /// <summary>Shows the list window under the clicked category. Only 생산운영 has screens.</summary>
        private void OpenCategory(int index, Rectangle anchor)
        {
            string category = Categories[index];
            ScreenSpec[] screens = category == ProductionCategory ? ScreenSpecs.All : new ScreenSpec[0];
            this.session.OpenMenu(category);

            int headerHeight = Ui.Px(26);
            int rowHeight = Ui.Px(30);
            int width = Ui.Px(260);
            int height = headerHeight + Math.Max(1, screens.Length) * rowHeight + 2;
            Point origin = this.menuBar.PointToScreen(new Point(anchor.Left, anchor.Bottom));
            PopupHost popup = PopupHost.Show(
                this, new Rectangle(origin, new Size(width + 2, height)), UiRegion.Menu,
                this.session.ExposureOf(UiRegion.Menu), "메뉴 목록", null);

            List<SurfaceItem> items = new List<SurfaceItem>();
            SurfaceItem header = new SurfaceItem();
            header.Id = "menu-list-title";
            header.Text = category;
            header.Bold = true;
            header.Back = Ui.TitleBack;
            header.Fore = Color.White;
            header.Bounds = new Rectangle(0, 0, width, headerHeight);
            items.Add(header);
            for (int row = 0; row < screens.Length; row++)
            {
                ScreenSpec spec = screens[row];
                SurfaceItem entry = new SurfaceItem();
                entry.Id = "screen-" + spec.Id;
                entry.Kind = ItemKind.Button;
                entry.Text = spec.Title;
                entry.Bounds = new Rectangle(0, headerHeight + row * rowHeight, width, rowHeight);
                entry.Click = delegate
                {
                    popup.Close(PopupHost.ReasonChoice);
                    this.ShowScreen(spec.Id);
                };
                items.Add(entry);
            }
            popup.Surface.SetItems(items);
        }

        internal void ShowScreen(string screenId)
        {
            this.session.OpenScreen(screenId);
            List<Control> old = new List<Control>();
            foreach (Control control in this.body.Controls)
            {
                old.Add(control);
            }
            this.body.Controls.Clear();
            foreach (Control control in old)
            {
                control.Dispose();
            }
            this.screen = new ScreenPanel(this.session, this.session.Screen, this);
            this.body.Controls.Add(this.screen);
            this.SetStatus("준비");
        }
    }
}
