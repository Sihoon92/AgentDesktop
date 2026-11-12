using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// Decides how long each query waits. With a fixed delay every query waits
    /// that long; otherwise the waits come from a seeded sequence, so the same
    /// seed gives the same waits in the same order.
    /// </summary>
    internal sealed class QueryRunner
    {
        private const int MinDelayMs = 1000;
        private const int MaxDelayMs = 4000;

        private readonly int? fixedDelayMs;
        private readonly Random random;

        internal QueryRunner(Options options)
        {
            this.fixedDelayMs = options.DelayMs;
            this.random = new Random(options.Seed);
        }

        internal int NextDelayMs()
        {
            return this.fixedDelayMs.HasValue
                ? this.fixedDelayMs.Value
                : this.random.Next(MinDelayMs, MaxDelayMs + 1);
        }

        /// <summary>Shows the loading window over the owner and returns when the wait is over.</summary>
        internal static void ShowLoading(Form owner, int delayMs)
        {
            using (LoadingDialog dialog = new LoadingDialog(delayMs))
            {
                dialog.ShowDialog(owner);
            }
        }

        /// <summary>A modal window with a rotating image. The user cannot close it; it closes itself.</summary>
        private sealed class LoadingDialog : Form
        {
            private readonly Timer finish = new Timer();
            private readonly Timer spin = new Timer();
            private readonly Spinner spinner = new Spinner();
            private bool finished;

            internal LoadingDialog(int delayMs)
            {
                this.Text = "조회 중";
                this.AutoScaleMode = AutoScaleMode.None;
                this.Font = Ui.Font;
                this.FormBorderStyle = FormBorderStyle.FixedDialog;
                this.ControlBox = false;
                this.ShowInTaskbar = false;
                this.StartPosition = FormStartPosition.CenterParent;
                this.ClientSize = new Size(Ui.Px(260), Ui.Px(120));
                this.BackColor = Color.White;

                this.spinner.Size = new Size(Ui.Px(48), Ui.Px(48));
                this.spinner.Location = new Point((this.ClientSize.Width - this.spinner.Width) / 2, Ui.Px(16));
                this.Controls.Add(this.spinner);

                Label message = new Label();
                message.Text = "데이터를 조회하고 있습니다";
                message.TextAlign = ContentAlignment.MiddleCenter;
                message.SetBounds(0, Ui.Px(74), this.ClientSize.Width, Ui.Px(28));
                this.Controls.Add(message);

                this.spin.Interval = 60;
                this.spin.Tick += delegate { this.spinner.Advance(); };
                this.finish.Interval = Math.Max(1, delayMs);
                this.finish.Tick += delegate
                {
                    this.finished = true;
                    this.Close();
                };
            }

            protected override void OnShown(EventArgs e)
            {
                base.OnShown(e);
                this.spin.Start();
                this.finish.Start();
            }

            protected override void OnFormClosing(FormClosingEventArgs e)
            {
                if (!this.finished)
                {
                    e.Cancel = true;
                    return;
                }
                this.spin.Stop();
                this.finish.Stop();
                base.OnFormClosing(e);
            }

            protected override void Dispose(bool disposing)
            {
                if (disposing)
                {
                    this.spin.Dispose();
                    this.finish.Dispose();
                }
                base.Dispose(disposing);
            }
        }

        /// <summary>The rotating image. It has no name and no text.</summary>
        private sealed class Spinner : Control
        {
            private int angle;

            internal Spinner()
            {
                this.SetStyle(
                    ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint
                    | ControlStyles.OptimizedDoubleBuffer, true);
                this.SetStyle(ControlStyles.Selectable, false);
            }

            internal void Advance()
            {
                this.angle = (this.angle + 30) % 360;
                this.Invalidate();
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                e.Graphics.Clear(Color.White);
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                int stroke = Ui.Px(5);
                Rectangle ring = new Rectangle(
                    stroke, stroke, this.Width - 2 * stroke, this.Height - 2 * stroke);
                using (Pen track = new Pen(Ui.StatusBack, stroke))
                using (Pen arc = new Pen(Ui.Accent, stroke))
                {
                    e.Graphics.DrawEllipse(track, ring);
                    e.Graphics.DrawArc(arc, ring, this.angle, 90);
                }
            }
        }
    }
}
