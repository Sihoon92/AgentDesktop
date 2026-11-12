using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>Lays a query result out on a Surface as a header row and data cells.</summary>
    internal static class GridSurface
    {
        /// <summary>
        /// Fills the surface and sizes it to the table, at least as wide as
        /// `availableWidth`. Data cells get the id cell-&lt;row&gt;-&lt;column&gt;, rows
        /// counted from 1. `cellClick` and `rightClick` may be null.
        /// </summary>
        internal static void Fill(
            Surface surface, QueryResult result, int availableWidth, Action<int, int> cellClick,
            Action<Point> rightClick)
        {
            int columns = result.Header.Length;
            int rowHeight = Ui.Px(26);
            int columnWidth = Math.Max(Ui.Px(110), availableWidth / columns);
            List<SurfaceItem> items = new List<SurfaceItem>();

            for (int column = 0; column < columns; column++)
            {
                SurfaceItem head = new SurfaceItem();
                head.Id = "head-" + column;
                head.Text = result.Header[column];
                head.Bold = true;
                head.Back = Ui.HeaderBack;
                head.Border = true;
                head.Align = ContentAlignment.MiddleCenter;
                head.Bounds = new Rectangle(column * columnWidth, 0, columnWidth, rowHeight);
                head.RightClick = rightClick;
                items.Add(head);
            }

            for (int row = 0; row < result.Rows.Count; row++)
            {
                for (int column = 0; column < columns; column++)
                {
                    int rowNumber = row + 1;
                    int columnNumber = column;
                    string text = result.Rows[row][column];
                    SurfaceItem cell = new SurfaceItem();
                    cell.Id = "cell-" + rowNumber + "-" + column;
                    cell.Kind = ItemKind.Cell;
                    cell.Text = text;
                    cell.Row = rowNumber;
                    cell.Column = column;
                    cell.Border = true;
                    cell.Align = IsNumeric(text) ? ContentAlignment.MiddleRight : ContentAlignment.MiddleCenter;
                    cell.Bounds = new Rectangle(
                        column * columnWidth, rowNumber * rowHeight, columnWidth, rowHeight);
                    cell.RightClick = rightClick;
                    if (cellClick != null)
                    {
                        cell.Click = delegate { cellClick(rowNumber, columnNumber); };
                    }
                    items.Add(cell);
                }
            }

            surface.BackgroundRightClick = rightClick;
            surface.Size = new Size(columns * columnWidth, (result.Rows.Count + 1) * rowHeight);
            surface.SetItems(items);
        }

        private static bool IsNumeric(string text)
        {
            if (text.Length == 0)
            {
                return false;
            }
            foreach (char ch in text)
            {
                if (!char.IsDigit(ch) && ch != ',' && ch != '.' && ch != '%')
                {
                    return false;
                }
            }
            return true;
        }
    }
}
