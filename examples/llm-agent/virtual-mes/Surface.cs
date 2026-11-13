using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Automation.Provider;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// Draws a list of items and hit-tests clicks itself. The exposure decides
    /// only what the accessibility tree shows: operable elements, plain text,
    /// or nothing but this one nameless control.
    /// </summary>
    internal sealed class Surface : Control
    {
        private const int WmGetObject = 0x003D;
        private static readonly IntPtr UiaRootObjectId = new IntPtr(-25);

        private readonly UiRegion region;
        private readonly Exposure exposure;
        private volatile SurfaceItem[] items = new SurfaceItem[0];
        private SurfaceRootProvider provider;
        private SurfaceItem pressed;
        private Rectangle caret = Rectangle.Empty;
        private bool caretShown;
        private Timer caretTimer;

        internal Action<Point> BackgroundRightClick;

        internal Surface(UiRegion region, Exposure exposure)
        {
            this.region = region;
            this.exposure = exposure;
            this.SetStyle(
                ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint
                | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            this.SetStyle(ControlStyles.Selectable, false);
            this.TabStop = false;
            this.Font = Ui.Font;
            this.BackColor = Color.White;
            this.ForeColor = Ui.Text;
        }

        internal UiRegion UiRegion
        {
            get { return this.region; }
        }

        internal Exposure Exposure
        {
            get { return this.exposure; }
        }

        /// <summary>The current items. The array is replaced, never changed in place.</summary>
        internal SurfaceItem[] Items
        {
            get { return this.items; }
        }

        internal void SetItems(IList<SurfaceItem> list)
        {
            SurfaceItem[] copy = new SurfaceItem[list.Count];
            list.CopyTo(copy, 0);
            this.items = copy;
            this.pressed = null;
            this.HideCaret();
            this.Invalidate();
        }

        /// <summary>
        /// Shows a blinking text caret, as a click on an editable box does. It is drawn only:
        /// the accessibility tree never sees it, so the blink does not read as a change.
        /// </summary>
        internal void ShowCaret(Rectangle line)
        {
            this.caret = line;
            this.caretShown = true;
            if (this.caretTimer == null)
            {
                int blink = SystemInformation.CaretBlinkTime;
                this.caretTimer = new Timer();
                this.caretTimer.Interval = blink > 0 ? blink : 530;
                this.caretTimer.Tick += delegate
                {
                    this.caretShown = !this.caretShown;
                    this.Invalidate(this.caret);
                };
            }
            this.caretTimer.Stop();
            this.caretTimer.Start();
            this.Invalidate(this.caret);
        }

        internal void HideCaret()
        {
            if (this.caret.IsEmpty)
            {
                return;
            }
            if (this.caretTimer != null)
            {
                this.caretTimer.Stop();
            }
            this.Invalidate(this.caret);
            this.caret = Rectangle.Empty;
            this.caretShown = false;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(this.BackColor);
            foreach (SurfaceItem item in this.items)
            {
                if (item.Paint != null)
                {
                    item.Paint(e.Graphics, item.Bounds);
                }
                else
                {
                    this.PaintDefault(e.Graphics, item);
                }
            }
            if (this.caretShown && !this.caret.IsEmpty)
            {
                using (SolidBrush brush = new SolidBrush(this.ForeColor))
                {
                    e.Graphics.FillRectangle(brush, this.caret);
                }
            }
        }

        private void PaintDefault(Graphics graphics, SurfaceItem item)
        {
            Color back = item.Back;
            Color fore = item.Fore.IsEmpty ? this.ForeColor : item.Fore;
            if (!item.Enabled)
            {
                back = Ui.DisabledBack;
                fore = Ui.DisabledText;
            }
            if (!back.IsEmpty)
            {
                using (SolidBrush brush = new SolidBrush(back))
                {
                    graphics.FillRectangle(brush, item.Bounds);
                }
            }
            if (item.Border)
            {
                using (Pen pen = new Pen(item.Kind == ItemKind.Cell ? Ui.CellLine : Ui.Line))
                {
                    graphics.DrawRectangle(
                        pen, item.Bounds.X, item.Bounds.Y, item.Bounds.Width - 1, item.Bounds.Height - 1);
                }
            }
            if (item.Kind == ItemKind.Check)
            {
                PaintCheck(graphics, item);
                return;
            }
            if (item.DropArrow)
            {
                PaintDropArrow(graphics, item.Bounds, fore);
            }
            if (item.Text.Length > 0)
            {
                Rectangle inner = item.Align == ContentAlignment.MiddleCenter
                    ? item.Bounds : Rectangle.Inflate(item.Bounds, -Ui.Px(6), 0);
                if (item.DropArrow)
                {
                    inner.Width -= Ui.Px(12);
                }
                TextRenderer.DrawText(
                    graphics, item.Text, item.Bold ? Ui.Bold : this.Font, inner, fore, TextFlags(item.Align));
            }
        }

        private static void PaintDropArrow(Graphics graphics, Rectangle bounds, Color color)
        {
            int half = Ui.Px(4);
            int centerX = bounds.Right - Ui.Px(11);
            int centerY = bounds.Top + bounds.Height / 2;
            using (Pen pen = new Pen(color, Math.Max(1.5f, Ui.Scale)))
            {
                graphics.DrawLines(pen, new[]
                {
                    new Point(centerX - half, centerY - half / 2),
                    new Point(centerX, centerY + half / 2),
                    new Point(centerX + half, centerY - half / 2)
                });
            }
        }

        private static void PaintCheck(Graphics graphics, SurfaceItem item)
        {
            int size = Ui.Px(13);
            Rectangle box = new Rectangle(
                item.Bounds.X + (item.Bounds.Width - size) / 2,
                item.Bounds.Y + (item.Bounds.Height - size) / 2, size, size);
            graphics.FillRectangle(Brushes.White, box);
            using (Pen edge = new Pen(Ui.Line))
            {
                graphics.DrawRectangle(edge, box);
            }
            if (item.Checked)
            {
                using (Pen mark = new Pen(Ui.Accent, Math.Max(2f, Ui.Scale * 1.6f)))
                {
                    graphics.DrawLines(mark, new[]
                    {
                        new Point(box.Left + size / 5, box.Top + size / 2),
                        new Point(box.Left + size * 2 / 5, box.Bottom - size / 4),
                        new Point(box.Right - size / 6, box.Top + size / 4)
                    });
                }
            }
        }

        private static TextFormatFlags TextFlags(ContentAlignment align)
        {
            TextFormatFlags flags = TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine
                | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix;
            if (align == ContentAlignment.MiddleCenter)
            {
                return flags | TextFormatFlags.HorizontalCenter;
            }
            if (align == ContentAlignment.MiddleRight)
            {
                return flags | TextFormatFlags.Right;
            }
            return flags | TextFormatFlags.Left;
        }

        private SurfaceItem Find(Point point)
        {
            SurfaceItem[] current = this.items;
            for (int index = current.Length - 1; index >= 0; index--)
            {
                SurfaceItem item = current[index];
                if (item.Parts != null)
                {
                    for (int part = item.Parts.Length - 1; part >= 0; part--)
                    {
                        if (item.Parts[part].Hit(point))
                        {
                            return item.Parts[part];
                        }
                    }
                }
                if (item.Hit(point))
                {
                    return item;
                }
            }
            return null;
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            this.HideCaret();
            this.pressed = this.Find(e.Location);
            base.OnMouseDown(e);
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            SurfaceItem target = this.Find(e.Location);
            SurfaceItem wasPressed = this.pressed;
            this.pressed = null;
            base.OnMouseUp(e);
            if (e.Button == MouseButtons.Left)
            {
                if (target != null && target == wasPressed && target.Enabled && target.Click != null)
                {
                    target.Click();
                }
            }
            else if (e.Button == MouseButtons.Right)
            {
                Point screen = this.PointToScreen(e.Location);
                if (target != null && target.RightClick != null)
                {
                    target.RightClick(screen);
                }
                else if (this.BackgroundRightClick != null)
                {
                    this.BackgroundRightClick(screen);
                }
            }
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WmGetObject && m.LParam == UiaRootObjectId && this.exposure != Exposure.Pixel)
            {
                if (this.provider == null)
                {
                    this.provider = new SurfaceRootProvider(this, m.HWnd);
                }
                m.Result = AutomationInteropProvider.ReturnRawElementProvider(
                    m.HWnd, m.WParam, m.LParam, this.provider);
                return;
            }
            base.WndProc(ref m);
        }

        protected override void OnHandleDestroyed(EventArgs e)
        {
            this.provider = null;
            if (this.caretTimer != null)
            {
                this.caretTimer.Dispose();
                this.caretTimer = null;
            }
            base.OnHandleDestroyed(e);
        }
    }
}
