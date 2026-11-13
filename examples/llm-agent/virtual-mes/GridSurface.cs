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

        /// <summary>
        /// Lays out a table with a two-row header and named rows. An upper header cell spans the
        /// lower columns it groups; an upper name over a single column of the same name fills
        /// both header rows. A run of rows with the same first-column name shares one tall
        /// cell. Data cells keep the id cell-&lt;row&gt;-&lt;column&gt;; only the number columns
        /// call `cellClick`.
        /// </summary>
        internal static void FillGrouped(
            Surface surface, QueryResult result, int availableWidth, Action<int, int> cellClick,
            Action<Point> rightClick)
        {
            int rowHeight = Ui.Px(26);
            int[] lefts;
            int[] widths = GroupedWidths(result, availableWidth, out lefts);
            int columns = widths.Length;
            int totalWidth = lefts[columns - 1] + widths[columns - 1];
            List<SurfaceItem> items = new List<SurfaceItem>();

            int start = 0;
            for (int group = 0; group < result.GroupNames.Length; group++)
            {
                int span = result.GroupSpans[group];
                string name = result.GroupNames[group];
                bool alone = span == 1 && name == result.Header[start];
                int right = lefts[start + span - 1] + widths[start + span - 1];
                Rectangle bounds = new Rectangle(lefts[start], 0, right - lefts[start], alone ? 2 * rowHeight : rowHeight);
                items.Add(HeaderItem("head-top-" + group, name, bounds, rightClick));
                for (int column = start; column < start + span && !alone; column++)
                {
                    Rectangle lower = new Rectangle(lefts[column], rowHeight, widths[column], rowHeight);
                    items.Add(HeaderItem("head-" + column, result.Header[column], lower, rightClick));
                }
                start += span;
            }

            int top = 2 * rowHeight;
            for (int row = 0; row < result.Rows.Count; row++)
            {
                int rowNumber = row + 1;
                string[] values = result.Rows[row];
                bool firstOfRun = row == 0 || result.Rows[row - 1][0] != values[0];
                if (firstOfRun)
                {
                    int run = 1;
                    while (row + run < result.Rows.Count && result.Rows[row + run][0] == values[0])
                    {
                        run++;
                    }
                    Rectangle tall = new Rectangle(lefts[0], top + row * rowHeight, widths[0], run * rowHeight);
                    items.Add(DataCell(rowNumber, 0, values[0], tall, rightClick));
                }
                for (int column = 1; column < columns; column++)
                {
                    Rectangle bounds = new Rectangle(lefts[column], top + row * rowHeight, widths[column], rowHeight);
                    SurfaceItem cell = DataCell(rowNumber, column, values[column], bounds, rightClick);
                    if (cellClick != null && column >= result.LabelColumns)
                    {
                        int columnNumber = column;
                        cell.Click = delegate { cellClick(rowNumber, columnNumber); };
                    }
                    items.Add(cell);
                }
            }

            surface.BackgroundRightClick = rightClick;
            surface.Size = new Size(totalWidth, top + result.Rows.Count * rowHeight);
            surface.SetItems(items);
        }

        /// <summary>Each column as wide as its header or widest value, widened together to fill the panel.</summary>
        private static int[] GroupedWidths(QueryResult result, int availableWidth, out int[] lefts)
        {
            int columns = result.Header.Length;
            int[] widths = new int[columns];
            int total = 0;
            for (int column = 0; column < columns; column++)
            {
                int widest = TextRenderer.MeasureText(result.Header[column], Ui.Bold).Width;
                foreach (string[] row in result.Rows)
                {
                    widest = Math.Max(widest, TextRenderer.MeasureText(row[column], Ui.Font).Width);
                }
                widths[column] = Math.Max(Ui.Px(56), widest + Ui.Px(14));
                total += widths[column];
            }
            int extra = Math.Max(0, availableWidth - total) / columns;
            lefts = new int[columns];
            int left = 0;
            for (int column = 0; column < columns; column++)
            {
                widths[column] += extra;
                lefts[column] = left;
                left += widths[column];
            }
            return widths;
        }

        private static SurfaceItem HeaderItem(string id, string text, Rectangle bounds, Action<Point> rightClick)
        {
            SurfaceItem head = new SurfaceItem();
            head.Id = id;
            head.Text = text;
            head.Bold = true;
            head.Back = Ui.HeaderBack;
            head.Border = true;
            head.Align = ContentAlignment.MiddleCenter;
            head.Bounds = bounds;
            head.RightClick = rightClick;
            return head;
        }

        private static SurfaceItem DataCell(int row, int column, string text, Rectangle bounds, Action<Point> rightClick)
        {
            SurfaceItem cell = new SurfaceItem();
            cell.Id = "cell-" + row + "-" + column;
            cell.Kind = ItemKind.Cell;
            cell.Text = text;
            cell.Row = row;
            cell.Column = column;
            cell.Border = true;
            cell.Align = IsNumeric(text) ? ContentAlignment.MiddleRight : ContentAlignment.MiddleCenter;
            cell.Bounds = bounds;
            cell.RightClick = rightClick;
            return cell;
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
