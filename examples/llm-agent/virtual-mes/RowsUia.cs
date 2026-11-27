using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Automation;
using System.Windows.Automation.Provider;

namespace VirtualMesApp
{
    /// <summary>
    /// The Data Panel of a grid shown with the rows exposure: the one child of the table, holding
    /// one Row n per data row in row order. A cell merged over several rows keeps its value in
    /// the first of them; every other row it covers gets a blank cell of the same column, so each
    /// row has a cell in every column. The panel is built whole from one item array and never
    /// changed; a new array builds a new panel.
    /// </summary>
    internal sealed class RowsPanelProvider : IRawElementProviderFragment
    {
        internal const string PanelName = "Data Panel";
        internal const int PanelRuntimeId = 1000000;
        internal const int RowRuntimeBase = 1000000;
        internal const int CellRuntimeBase = 2000000;

        private readonly SurfaceRootProvider root;
        private readonly RowProvider[] rows;

        internal RowsPanelProvider(SurfaceRootProvider root, List<SurfaceItem> cells)
        {
            this.root = root;
            this.rows = this.BuildRows(cells);
        }

        internal SurfaceRootProvider Root
        {
            get { return this.root; }
        }

        internal RowProvider[] Rows
        {
            get { return this.rows; }
        }

        /// <summary>The panel's item with the id, blank cells included, or null.</summary>
        internal SurfaceItem Find(string id)
        {
            foreach (RowProvider row in this.rows)
            {
                foreach (SurfaceItemProvider cell in row.Cells)
                {
                    if (cell.Item.Id == id)
                    {
                        return cell.Item;
                    }
                }
            }
            return null;
        }

        internal SurfaceItemProvider CellAt(System.Windows.Point point)
        {
            foreach (RowProvider row in this.rows)
            {
                foreach (SurfaceItemProvider cell in row.Cells)
                {
                    if (cell.BoundingRectangle.Contains(point))
                    {
                        return cell;
                    }
                }
            }
            return null;
        }

        private RowProvider[] BuildRows(List<SurfaceItem> cells)
        {
            SortedDictionary<int, Dictionary<int, SurfaceItem>> byRow = new SortedDictionary<int, Dictionary<int, SurfaceItem>>();
            Dictionary<int, SurfaceItem> firstOfColumn = new Dictionary<int, SurfaceItem>();
            int columns = 0;
            foreach (SurfaceItem cell in cells)
            {
                if (!byRow.ContainsKey(cell.Row))
                {
                    byRow[cell.Row] = new Dictionary<int, SurfaceItem>();
                }
                byRow[cell.Row][cell.Column] = cell;
                if (!firstOfColumn.ContainsKey(cell.Column))
                {
                    firstOfColumn[cell.Column] = cell;
                }
                columns = Math.Max(columns, cell.Column + 1);
            }
            List<RowProvider> built = new List<RowProvider>();
            int runtime = CellRuntimeBase;
            foreach (KeyValuePair<int, Dictionary<int, SurfaceItem>> entry in byRow)
            {
                RowProvider row = new RowProvider(this, built.Count, entry.Key);
                SurfaceItem sample = null;
                foreach (SurfaceItem item in entry.Value.Values)
                {
                    sample = item;
                    break;
                }
                List<SurfaceItemProvider> rowCells = new List<SurfaceItemProvider>();
                for (int column = 0; column < columns; column++)
                {
                    SurfaceItem item;
                    if (!entry.Value.TryGetValue(column, out item))
                    {
                        if (!firstOfColumn.ContainsKey(column))
                        {
                            continue;
                        }
                        item = Blank(entry.Key, column, firstOfColumn[column], sample);
                    }
                    rowCells.Add(new SurfaceItemProvider(
                        this.root, runtime++, item, UiaShape.RowCell, Exposure.Rows, false, row, rowCells.Count));
                }
                row.SetCells(rowCells.ToArray());
                built.Add(row);
            }
            return built.ToArray();
        }

        /// <summary>A valueless cell for a row a merged cell above covers: the column's place, the row's height.</summary>
        private static SurfaceItem Blank(int row, int column, SurfaceItem columnSample, SurfaceItem rowSample)
        {
            SurfaceItem blank = new SurfaceItem();
            blank.Id = "cell-" + row + "-" + column;
            blank.Kind = ItemKind.Cell;
            blank.Row = row;
            blank.Column = column;
            blank.ColumnName = columnSample.ColumnName;
            blank.Bounds = new Rectangle(
                columnSample.Bounds.X, rowSample.Bounds.Y, columnSample.Bounds.Width, rowSample.Bounds.Height);
            return blank;
        }

        internal static Rectangle Union(IEnumerable<SurfaceItem> items)
        {
            Rectangle union = Rectangle.Empty;
            foreach (SurfaceItem item in items)
            {
                union = union.IsEmpty ? item.Bounds : Rectangle.Union(union, item.Bounds);
            }
            return union;
        }

        private Rectangle ClientBounds()
        {
            List<SurfaceItem> items = new List<SurfaceItem>();
            foreach (RowProvider row in this.rows)
            {
                foreach (SurfaceItemProvider cell in row.Cells)
                {
                    items.Add(cell.Item);
                }
            }
            return Union(items);
        }

        public ProviderOptions ProviderOptions
        {
            get { return ProviderOptions.ServerSideProvider; }
        }

        public object GetPatternProvider(int patternId)
        {
            return null;
        }

        public object GetPropertyValue(int propertyId)
        {
            return RowsProperties.Value(
                propertyId, ControlType.Pane.Id, PanelName, this.root, this.ClientBounds());
        }

        public IRawElementProviderSimple HostRawElementProvider
        {
            get { return null; }
        }

        public IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            if (direction == NavigateDirection.Parent)
            {
                return this.root;
            }
            if (direction == NavigateDirection.FirstChild && this.rows.Length > 0)
            {
                return this.rows[0];
            }
            if (direction == NavigateDirection.LastChild && this.rows.Length > 0)
            {
                return this.rows[this.rows.Length - 1];
            }
            if (direction == NavigateDirection.PreviousSibling)
            {
                IRawElementProviderFragment[] top = this.root.TopChildren();
                int at = Array.IndexOf(top, this);
                return at > 0 ? top[at - 1] : null;
            }
            return null;
        }

        public int[] GetRuntimeId()
        {
            return new[] { AutomationInteropProvider.AppendRuntimeId, PanelRuntimeId };
        }

        public System.Windows.Rect BoundingRectangle
        {
            get { return this.root.ScreenRect(this.ClientBounds()); }
        }

        public IRawElementProviderSimple[] GetEmbeddedFragmentRoots()
        {
            return null;
        }

        public void SetFocus()
        {
        }

        public IRawElementProviderFragmentRoot FragmentRoot
        {
            get { return this.root; }
        }
    }

    /// <summary>One data row of the rows exposure: a Custom element named Row n, n the grid's row number from 1.</summary>
    internal sealed class RowProvider : IRawElementProviderFragment
    {
        private readonly RowsPanelProvider panel;
        private readonly int position;
        private readonly int number;
        private SurfaceItemProvider[] cells = new SurfaceItemProvider[0];

        internal RowProvider(RowsPanelProvider panel, int position, int number)
        {
            this.panel = panel;
            this.position = position;
            this.number = number;
        }

        internal SurfaceItemProvider[] Cells
        {
            get { return this.cells; }
        }

        internal RowsPanelProvider Panel
        {
            get { return this.panel; }
        }

        internal void SetCells(SurfaceItemProvider[] built)
        {
            this.cells = built;
        }

        private Rectangle ClientBounds()
        {
            List<SurfaceItem> items = new List<SurfaceItem>();
            foreach (SurfaceItemProvider cell in this.cells)
            {
                items.Add(cell.Item);
            }
            return RowsPanelProvider.Union(items);
        }

        public ProviderOptions ProviderOptions
        {
            get { return ProviderOptions.ServerSideProvider; }
        }

        public object GetPatternProvider(int patternId)
        {
            return null;
        }

        public object GetPropertyValue(int propertyId)
        {
            return RowsProperties.Value(
                propertyId, ControlType.Custom.Id, "Row " + this.number, this.panel.Root, this.ClientBounds());
        }

        public IRawElementProviderSimple HostRawElementProvider
        {
            get { return null; }
        }

        public IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            RowProvider[] rows = this.panel.Rows;
            if (direction == NavigateDirection.Parent)
            {
                return this.panel;
            }
            if (direction == NavigateDirection.NextSibling && this.position + 1 < rows.Length)
            {
                return rows[this.position + 1];
            }
            if (direction == NavigateDirection.PreviousSibling && this.position > 0)
            {
                return rows[this.position - 1];
            }
            if (direction == NavigateDirection.FirstChild && this.cells.Length > 0)
            {
                return this.cells[0];
            }
            if (direction == NavigateDirection.LastChild && this.cells.Length > 0)
            {
                return this.cells[this.cells.Length - 1];
            }
            return null;
        }

        public int[] GetRuntimeId()
        {
            return new[] { AutomationInteropProvider.AppendRuntimeId, RowsPanelProvider.RowRuntimeBase + this.number };
        }

        public System.Windows.Rect BoundingRectangle
        {
            get { return this.panel.Root.ScreenRect(this.ClientBounds()); }
        }

        public IRawElementProviderSimple[] GetEmbeddedFragmentRoots()
        {
            return null;
        }

        public void SetFocus()
        {
        }

        public IRawElementProviderFragmentRoot FragmentRoot
        {
            get { return this.panel.Root; }
        }
    }

    /// <summary>The properties the panel and its rows share: a control type, a name, no id, no focus.</summary>
    internal static class RowsProperties
    {
        internal static object Value(
            int propertyId, int controlType, string name, SurfaceRootProvider root, Rectangle client)
        {
            if (propertyId == AutomationElementIdentifiers.ControlTypeProperty.Id)
            {
                return controlType;
            }
            if (propertyId == AutomationElementIdentifiers.NameProperty.Id)
            {
                return name;
            }
            if (propertyId == AutomationElementIdentifiers.IsEnabledProperty.Id)
            {
                return !root.Blocked();
            }
            if (propertyId == AutomationElementIdentifiers.IsOffscreenProperty.Id)
            {
                return root.Hidden(client);
            }
            if (propertyId == AutomationElementIdentifiers.IsKeyboardFocusableProperty.Id)
            {
                return false;
            }
            if (propertyId == AutomationElementIdentifiers.IsControlElementProperty.Id
                || propertyId == AutomationElementIdentifiers.IsContentElementProperty.Id)
            {
                return true;
            }
            return null;
        }
    }
}
