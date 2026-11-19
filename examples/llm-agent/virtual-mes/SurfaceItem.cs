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

        /// <summary>The AutomationId the item shows when it differs from Id, such as a fixed
        /// number or an id two items share. Id stays unique, so reads find the right item.</summary>
        internal string AutomationId;
        internal Rectangle Bounds;
        internal string Text = string.Empty;

        /// <summary>The accessible name of an item that shows no text, such as an arrow glyph.
        /// Only the ref exposure reveals it; the text exposure shows visible text only.</summary>
        internal string Name;

        /// <summary>
        /// Smaller items inside this one, such as a box's arrow. A part is drawn after its owner
        /// only when it has its own Paint; clicks hit it before the owner. In the accessibility
        /// tree a part is a sibling right after its owner, so it shows even when the owner does
        /// not.
        /// </summary>
        internal SurfaceItem[] Parts;

        /// <summary>
        /// This item's own exposure, for an item whose accessibility is chosen apart from its
        /// surface's region, such as a filter box's arrow. Null follows the surface.
        /// </summary>
        internal Exposure? Exposure;
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
