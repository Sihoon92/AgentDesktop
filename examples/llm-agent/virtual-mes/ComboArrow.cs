using System;
using System.Drawing;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// The look and the fixed ids of a filter box that opens only from its arrow: a white
    /// input-like body and, at its right end, a separate square button with the arrow glyph.
    /// </summary>
    internal static class ComboArrow
    {
        /// <summary>
        /// The filter keys in a fixed order. A bare arrow's AutomationId is FirstBareId plus the
        /// key's place in this list, so each box keeps its number on every screen and every run.
        /// </summary>
        internal static readonly string[] Keys =
        {
            "shop-1", "shop-2", "shop-3", "process", "product", "equipment", "period"
        };

        internal const int FirstBareId = 7001;

        private static readonly Color ButtonFace = Color.FromArgb(225, 230, 237);
        private static readonly Color ButtonPressed = Color.FromArgb(196, 205, 217);

        /// <summary>The arrow's AutomationId: numeric when bare, else "box id-arrow".</summary>
        internal static string ArrowId(string boxId, string filterKey, Exposure exposure)
        {
            if (exposure != Exposure.Bare)
            {
                return boxId + "-arrow";
            }
            int place = Array.IndexOf(Keys, filterKey);
            if (place < 0)
            {
                throw new ArgumentException("No fixed arrow id for filter " + filterKey);
            }
            return (FirstBareId + place).ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        /// <summary>The arrow square: the box's right end, one box height wide.</summary>
        internal static Rectangle ArrowBounds(Rectangle box)
        {
            return new Rectangle(box.Right - box.Height, box.Top, box.Height, box.Height);
        }

        /// <summary>Draws the body like an input box, its text kept clear of the arrow button.</summary>
        internal static void PaintBody(Graphics graphics, SurfaceItem box, Font font)
        {
            Rectangle bounds = box.Bounds;
            Color back = box.Enabled ? Color.White : Ui.DisabledBack;
            Color fore = box.Enabled ? Ui.Text : Ui.DisabledText;
            using (SolidBrush brush = new SolidBrush(back))
            {
                graphics.FillRectangle(brush, bounds);
            }
            using (Pen pen = new Pen(Ui.Line))
            {
                graphics.DrawRectangle(pen, bounds.X, bounds.Y, bounds.Width - 1, bounds.Height - 1);
            }
            Rectangle inner = Rectangle.Inflate(bounds, -Ui.Px(6), 0);
            inner.Width -= bounds.Height;
            TextRenderer.DrawText(graphics, box.Text, font, inner, fore,
                TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis
                | TextFormatFlags.NoPrefix | TextFormatFlags.Left);
        }

        /// <summary>Draws the arrow button: a raised face, sunk by a pixel while pressed. A disabled
        /// button never looks pressed.</summary>
        internal static void PaintButton(Graphics graphics, Rectangle bounds, bool enabled, bool pressed)
        {
            pressed = pressed && enabled;
            Color face = !enabled ? Ui.DisabledBack : (pressed ? ButtonPressed : ButtonFace);
            using (SolidBrush brush = new SolidBrush(face))
            {
                graphics.FillRectangle(brush, bounds);
            }
            using (Pen edge = new Pen(Ui.Line))
            {
                graphics.DrawRectangle(edge, bounds.X, bounds.Y, bounds.Width - 1, bounds.Height - 1);
            }
            if (enabled && !pressed)
            {
                using (Pen light = new Pen(Color.White))
                {
                    graphics.DrawLine(light, bounds.X + 1, bounds.Y + 1, bounds.Right - 2, bounds.Y + 1);
                    graphics.DrawLine(light, bounds.X + 1, bounds.Y + 1, bounds.X + 1, bounds.Bottom - 2);
                }
            }
            int shift = pressed ? Math.Max(1, Ui.Px(1)) : 0;
            int half = Ui.Px(4);
            int centerX = bounds.Left + bounds.Width / 2 + shift;
            int centerY = bounds.Top + bounds.Height / 2 + shift;
            Color glyph = enabled ? Ui.Text : Ui.DisabledText;
            using (Pen pen = new Pen(glyph, Math.Max(1.5f, Ui.Scale)))
            {
                graphics.DrawLines(pen, new[]
                {
                    new Point(centerX - half, centerY - half / 2),
                    new Point(centerX, centerY + half / 2),
                    new Point(centerX + half, centerY - half / 2)
                });
            }
        }
    }
}
