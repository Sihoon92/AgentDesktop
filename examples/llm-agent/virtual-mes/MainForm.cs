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
        private readonly Options options;
        private readonly Surface menuBar;

        /// <summary>The strip under the menu bar that holds the duplicate of the first menu item
        /// (--menu-dup-id other), or null.</summary>
        private readonly Surface otherStrip;
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
            this.options = options;
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
            this.menuBar.AreaId = MenuIds.Area(MenuIds.BarArea, options);

            this.Controls.Add(this.body);
            this.Controls.Add(this.status);
            if (options.MenuDupId == Options.MenuDupOther)
            {
                this.otherStrip = new Surface(UiRegion.Menu, options.Expose[UiRegion.Menu]);
                this.otherStrip.Dock = DockStyle.Top;
                this.otherStrip.Height = Ui.Px(30);
                this.otherStrip.BackColor = Ui.PanelBack;
                this.otherStrip.AreaId = MenuIds.Area(MenuIds.OtherArea, options);
                this.Controls.Add(this.otherStrip);
            }
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
                this.Shown += delegate
                {
                    session.MenuReady();
                    this.OpenStartupNoticesLater();
                };
            }
        }

        /// <summary>
        /// Opens the start-up notices (--startup-notices) after the handler that called it returns,
        /// so the window has finished showing and its menu is filled before the first notice.
        /// </summary>
        private void OpenStartupNoticesLater()
        {
            int count = this.options.StartupNotices;
            if (count > 0)
            {
                this.BeginInvoke(new MethodInvoker(delegate { StartupNotices.Show(this, this.session, count); }));
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
                this.OpenStartupNoticesLater();
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
            Exposure exposure = this.options.Expose[UiRegion.Menu];
            List<SurfaceItem> items = new List<SurfaceItem>();
            int left = Ui.Px(6);
            for (int index = 0; index < Categories.Length; index++)
            {
                int width = TextRenderer.MeasureText(Categories[index], Ui.Font).Width + Ui.Px(24);
                Rectangle bounds = new Rectangle(left, 0, width, this.menuBar.Height);
                SurfaceItem item = this.CategoryButton(this.menuBar, index, bounds);
                item.Id = "menu-" + index;
                item.AutomationId = MenuIds.BarItem(index, exposure, this.options.MenuDupId);
                items.Add(item);
                left += width + Ui.Px(2);
            }
            this.menuBar.SetItems(items);
            if (this.otherStrip != null)
            {
                SurfaceItem twin = this.CategoryButton(this.otherStrip, 0, items[0].Bounds);
                twin.Id = "menu-other-0";
                twin.AutomationId = items[0].AutomationId;
                twin.Back = Ui.Accent;
                this.otherStrip.SetItems(new[] { twin });
            }
        }

        /// <summary>
        /// A menu button for one category, drawn on the given surface; clicking it opens that
        /// category's list under the button.
        /// </summary>
        private SurfaceItem CategoryButton(Surface owner, int category, Rectangle bounds)
        {
            SurfaceItem item = new SurfaceItem();
            item.Kind = ItemKind.Button;
            item.Text = Categories[category];
            item.Fore = Color.White;
            item.Align = ContentAlignment.MiddleCenter;
            item.Bounds = bounds;
            item.Click = delegate { this.OpenCategory(category, owner, item.Bounds); };
            return item;
        }

        /// <summary>Shows the list window under the clicked category. Only 생산운영 has screens.</summary>
        private void OpenCategory(int index, Surface from, Rectangle anchor)
        {
            string category = Categories[index];
            ScreenSpec[] screens = category == ProductionCategory ? ScreenSpecs.All : new ScreenSpec[0];
            Exposure exposure = this.session.ExposureOf(UiRegion.Menu);
            this.session.OpenMenu(category);

            int headerHeight = Ui.Px(26);
            int rowHeight = Ui.Px(30);
            int width = Ui.Px(260);
            int height = headerHeight + Math.Max(1, screens.Length) * rowHeight + 2;
            Point origin = from.PointToScreen(new Point(anchor.Left, anchor.Bottom));
            PopupHost popup = PopupHost.Show(
                this, new Rectangle(origin, new Size(width + 2, height)), UiRegion.Menu,
                exposure, "메뉴 목록", null, MenuIds.Area(MenuIds.ListArea, this.options));

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
                entry.AutomationId = MenuIds.ListItem(row, spec.Id, exposure);
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
            this.body.Controls.Add(Nested(this.screen, this.options.Nest));
            this.SetStatus("준비");
        }

        /// <summary>
        /// The screen wrapped in `layers` filling panels (--nest), outermost first: an odd layer
        /// has no name, an even one is named nest-&lt;i&gt;, which UI Automation shows as an
        /// AutomationId and no name. With 0 layers the screen itself.
        /// </summary>
        private static Control Nested(Control screen, int layers)
        {
            Control inner = screen;
            for (int layer = layers; layer >= 1; layer--)
            {
                Panel wrapper = new Panel();
                wrapper.Dock = DockStyle.Fill;
                wrapper.BackColor = Ui.WindowBack;
                if (layer % 2 == 0)
                {
                    wrapper.Name = "nest-" + layer;
                }
                wrapper.Controls.Add(inner);
                inner = wrapper;
            }
            return inner;
        }
    }
}
