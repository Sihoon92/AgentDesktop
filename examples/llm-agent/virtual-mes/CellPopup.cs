using System;
using System.Drawing;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// The detail window a number cell opens. It has no Excel icon: its table
    /// is exported by right-clicking it, through the round menu.
    /// </summary>
    internal sealed class CellPopup : Form
    {
        internal CellPopup(Session session, Form owner, string title, string fileName, QueryResult detail)
        {
            this.Text = title;
            this.AutoScaleMode = AutoScaleMode.None;
            this.Font = Ui.Font;
            this.BackColor = Color.White;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MinimizeBox = false;
            this.MaximizeBox = false;
            this.ShowInTaskbar = false;
            this.StartPosition = FormStartPosition.Manual;
            this.ClientSize = new Size(Ui.Px(460), Ui.Px(26) * (detail.Rows.Count + 1) + Ui.Px(40));
            this.Location = new Point(
                owner.Left + (owner.Width - this.Width) / 2, owner.Top + (owner.Height - this.Height) / 2);

            Action<Point> openRadial = delegate(Point screen)
            {
                RadialMenu.Show(
                    this, screen, session, delegate { return detail; },
                    ExportFlow.SourceCellPopup, fileName);
            };

            Surface grid = new Surface(UiRegion.Grid, session.ExposureOf(UiRegion.Grid));
            grid.Location = Point.Empty;
            this.Controls.Add(grid);
            GridSurface.Fill(grid, detail, this.ClientSize.Width, null, openRadial);

            this.MouseUp += delegate(object sender, MouseEventArgs e)
            {
                if (e.Button == MouseButtons.Right)
                {
                    openRadial(this.PointToScreen(e.Location));
                }
            };
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            PopupHost.CloseAll(PopupHost.ReasonDeactivated);
            base.OnFormClosing(e);
        }
    }
}
