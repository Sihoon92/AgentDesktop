using System;
using System.Drawing;

namespace VirtualMesApp
{
    internal enum ItemKind
    {
        Label,
        Button,
        Check,
        Cell,
        Graphic
    }

    /// <summary>
    /// One thing a Surface draws and hit-tests. What it looks like on screen
    /// never depends on the exposure; only its accessibility shape does.
    /// </summary>
    internal sealed class SurfaceItem
    {
        internal string Id;
        internal Rectangle Bounds;
        internal string Text = string.Empty;
        internal ItemKind Kind = ItemKind.Label;
        internal bool Enabled = true;
        internal bool Checked;
        internal int Row;
        internal int Column;

        internal Action Click;
        internal Action<Point> RightClick;

        /// <summary>Replaces the default drawing for this item.</summary>
        internal Action<Graphics, Rectangle> Paint;

        /// <summary>Replaces the rectangular hit test, for shapes such as a pie slice.</summary>
        internal Func<Point, bool> Contains;

        internal Color Back = Color.Empty;
        internal Color Fore = Color.Empty;
        internal bool Border;
        internal bool Bold;
        internal bool DropArrow;
        internal ContentAlignment Align = ContentAlignment.MiddleLeft;

        internal bool Hit(Point point)
        {
            return this.Contains != null ? this.Contains(point) : this.Bounds.Contains(point);
        }
    }
}
