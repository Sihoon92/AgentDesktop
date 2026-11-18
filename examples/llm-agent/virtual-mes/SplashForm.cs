using System;
using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// The small loading window shown before the main window, like the real MES's start-up popup.
    /// Its title and text share no word with the menu, so finding a menu name can never pick it.
    /// </summary>
    internal sealed class SplashForm : Form
    {
        internal const string WindowTitle = "VirtualMes";
        internal const string LoadingText = "불러오는 중";

        private readonly EventLog log;
        private readonly int durationMs;
        private readonly Timer timer = new Timer();

        internal SplashForm(EventLog log, int durationMs)
        {
            this.log = log;
            this.durationMs = durationMs;
            this.Text = WindowTitle;
            this.AutoScaleMode = AutoScaleMode.None;
            this.Font = Ui.Font;
            this.BackColor = Ui.PanelBack;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.ClientSize = new Size(Ui.Px(320), Ui.Px(110));

            Label loading = new Label();
            loading.Text = LoadingText;
            loading.Dock = DockStyle.Fill;
            loading.TextAlign = ContentAlignment.MiddleCenter;
            loading.ForeColor = Ui.Text;
            this.Controls.Add(loading);

            this.timer.Interval = Math.Max(1, durationMs);
            this.timer.Tick += delegate
            {
                this.timer.Stop();
                this.Close();
            };
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            this.log.Write("splash_shown", EventLog.Fields(
                "pid", Process.GetCurrentProcess().Id, "splash_ms", this.durationMs));
            this.timer.Start();
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            this.timer.Stop();
            this.log.Write("splash_closed", EventLog.Fields("pid", Process.GetCurrentProcess().Id));
            base.OnFormClosed(e);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                this.timer.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}
