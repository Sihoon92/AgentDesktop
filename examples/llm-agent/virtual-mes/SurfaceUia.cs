using System;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Automation;
using System.Windows.Automation.Provider;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>How one item appears to UI Automation.</summary>
    internal enum UiaShape
    {
        Hidden,
        Text,
        Button,
        CheckBox,
        Cell,

        /// <summary>An image with an AutomationId and nothing else: no name and no pattern, so no
        /// ref. A real click still reaches the item, which the surface hit-tests itself.</summary>
        Image,

        /// <summary>A named button that advertises no pattern: the CLI gives it a ref for its
        /// role, but neither Invoke nor anything else can press it. Only a real click does.</summary>
        InertButton,

        /// <summary>A data cell of the rows exposure: a DataItem in its Row, named by its column
        /// header and row, with Invoke and a read-only Value but no GridItem or TableItem.</summary>
        RowCell
    }

    /// <summary>
    /// UI Automation root for a Surface. UI Automation calls providers on its
    /// own threads, so everything here reads the item array (replaced whole,
    /// never changed in place) and the window handle, never the control.
    /// </summary>
    internal sealed class SurfaceRootProvider : IRawElementProviderFragmentRoot
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct NativePoint
        {
            internal int X;
            internal int Y;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct NativeRect
        {
            internal int Left;
            internal int Top;
            internal int Right;
            internal int Bottom;
        }

        private const uint GaRoot = 2;

        [DllImport("user32.dll")]
        private static extern bool ClientToScreen(IntPtr handle, ref NativePoint point);

        [DllImport("user32.dll")]
        private static extern bool GetClientRect(IntPtr handle, out NativeRect rect);

        [DllImport("user32.dll")]
        private static extern IntPtr GetParent(IntPtr handle);

        [DllImport("user32.dll")]
        private static extern IntPtr GetAncestor(IntPtr handle, uint flags);

        [DllImport("user32.dll")]
        private static extern bool IsWindowEnabled(IntPtr handle);

        private readonly Surface surface;
        private readonly IntPtr handle;
        private readonly object gate = new object();
        private SurfaceItem[] cachedSource;
        private SurfaceItemProvider[] cachedChildren = new SurfaceItemProvider[0];
        private RowsPanelProvider cachedPanel;

        internal SurfaceRootProvider(Surface surface, IntPtr handle)
        {
            this.surface = surface;
            this.handle = handle;
        }

        internal static UiaShape ShapeOf(Exposure exposure, SurfaceItem item)
        {
            bool hasText = item.Text.Length > 0;
            if (exposure == Exposure.Bare)
            {
                return item.Kind == ItemKind.Button || item.Kind == ItemKind.Cell ? UiaShape.Button : UiaShape.Hidden;
            }
            if (exposure == Exposure.IdOnly)
            {
                return item.Kind == ItemKind.Button || item.Kind == ItemKind.Cell ? UiaShape.Image : UiaShape.Hidden;
            }
            if (exposure == Exposure.NoAction)
            {
                UiaShape named = ShapeOf(Exposure.Ref, item);
                return named == UiaShape.Button || named == UiaShape.Cell ? UiaShape.InertButton : named;
            }
            if (exposure == Exposure.Rows)
            {
                if (item.Kind == ItemKind.Cell)
                {
                    return UiaShape.RowCell;
                }
                return item.Header ? UiaShape.Hidden : ShapeOf(Exposure.Text, item);
            }
            if (exposure == Exposure.Text)
            {
                return hasText && item.Kind != ItemKind.Check ? UiaShape.Text : UiaShape.Hidden;
            }
            if (exposure == Exposure.Ref)
            {
                if (item.Kind == ItemKind.Check)
                {
                    return UiaShape.CheckBox;
                }
                if (!hasText && string.IsNullOrEmpty(item.Name))
                {
                    return UiaShape.Hidden;
                }
                if (item.Kind == ItemKind.Label)
                {
                    return UiaShape.Text;
                }
                return item.Kind == ItemKind.Cell ? UiaShape.Cell : UiaShape.Button;
            }
            return UiaShape.Hidden;
        }

        internal SurfaceItemProvider[] Children()
        {
            SurfaceItem[] source = this.surface.Items;
            lock (this.gate)
            {
                if (!object.ReferenceEquals(source, this.cachedSource))
                {
                    List<SurfaceItemProvider> built = new List<SurfaceItemProvider>();
                    List<SurfaceItem> rowCells = new List<SurfaceItem>();
                    foreach (SurfaceItem item in source)
                    {
                        this.AddChild(built, rowCells, item, false);
                        if (item.Parts != null)
                        {
                            foreach (SurfaceItem part in item.Parts)
                            {
                                this.AddChild(built, rowCells, part, true);
                            }
                        }
                    }
                    this.cachedSource = source;
                    this.cachedChildren = built.ToArray();
                    this.cachedPanel = rowCells.Count > 0 ? new RowsPanelProvider(this, rowCells) : null;
                }
                return this.cachedChildren;
            }
        }

        /// <summary>The Data Panel of a grid shown with the rows exposure, or null.</summary>
        internal RowsPanelProvider Panel()
        {
            this.Children();
            lock (this.gate)
            {
                return this.cachedPanel;
            }
        }

        /// <summary>The surface's direct children: the exposed items, then the Data Panel if any.</summary>
        internal IRawElementProviderFragment[] TopChildren()
        {
            SurfaceItemProvider[] items = this.Children();
            RowsPanelProvider panel = this.Panel();
            List<IRawElementProviderFragment> top = new List<IRawElementProviderFragment>(items);
            if (panel != null)
            {
                top.Add(panel);
            }
            return top.ToArray();
        }

        /// <summary>Adds the item as the next child when its exposure shows it; a rows cell goes
        /// to `rowCells` for the Data Panel instead.</summary>
        private void AddChild(List<SurfaceItemProvider> built, List<SurfaceItem> rowCells, SurfaceItem item, bool isPart)
        {
            Exposure exposure = item.Exposure ?? this.surface.Exposure;
            UiaShape shape = ShapeOf(exposure, item);
            if (shape == UiaShape.RowCell)
            {
                rowCells.Add(item);
            }
            else if (shape != UiaShape.Hidden)
            {
                built.Add(new SurfaceItemProvider(this, built.Count, item, shape, exposure, isPart));
            }
        }

        internal System.Windows.Rect ScreenRect(Rectangle client)
        {
            NativePoint origin = new NativePoint();
            origin.X = client.X;
            origin.Y = client.Y;
            ClientToScreen(this.handle, ref origin);
            return new System.Windows.Rect(origin.X, origin.Y, client.Width, client.Height);
        }

        /// <summary>
        /// True while the top-level window refuses input, as it does under a
        /// modal window. A real click cannot reach the surface then, so an
        /// automation call must not either.
        /// </summary>
        internal bool Blocked()
        {
            IntPtr top = GetAncestor(this.handle, GaRoot);
            return top != IntPtr.Zero && !IsWindowEnabled(top);
        }

        /// <summary>True when no part of the rectangle shows through the surface's parent, as after scrolling.</summary>
        internal bool Hidden(Rectangle client)
        {
            IntPtr parent = GetParent(this.handle);
            NativeRect area;
            if (parent == IntPtr.Zero || !GetClientRect(parent, out area))
            {
                return false;
            }
            NativePoint origin = new NativePoint();
            ClientToScreen(parent, ref origin);
            System.Windows.Rect visible = new System.Windows.Rect(
                origin.X, origin.Y, Math.Max(0, area.Right - area.Left), Math.Max(0, area.Bottom - area.Top));
            return !visible.IntersectsWith(this.ScreenRect(client));
        }

        /// <summary>Runs the action on the UI thread and returns at once, so an action that opens a modal window cannot block the caller.</summary>
        internal void Post(Action action)
        {
            this.surface.BeginInvoke(new MethodInvoker(delegate { action(); }));
        }

        /// <summary>Runs the action on the UI thread and waits, so the caller reads the new state.</summary>
        internal void Send(Action action)
        {
            this.surface.Invoke(new MethodInvoker(delegate { action(); }));
        }

        public ProviderOptions ProviderOptions
        {
            get { return ProviderOptions.ServerSideProvider; }
        }

        public object GetPatternProvider(int patternId)
        {
            return null;
        }

        /// <summary>
        /// Everything comes from the window's default provider, except the AutomationId of a
        /// surface that sets its own: a value here wins over the default provider's, and an
        /// empty one hides the default's.
        /// </summary>
        public object GetPropertyValue(int propertyId)
        {
            if (propertyId == AutomationElementIdentifiers.AutomationIdProperty.Id)
            {
                return this.surface.AreaId;
            }
            if (propertyId == AutomationElementIdentifiers.ControlTypeProperty.Id && this.Panel() != null)
            {
                return ControlType.Table.Id;
            }
            return null;
        }

        public IRawElementProviderSimple HostRawElementProvider
        {
            get { return AutomationInteropProvider.HostProviderFromHandle(this.handle); }
        }

        public IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            IRawElementProviderFragment[] children = this.TopChildren();
            if (children.Length == 0)
            {
                return null;
            }
            if (direction == NavigateDirection.FirstChild)
            {
                return children[0];
            }
            if (direction == NavigateDirection.LastChild)
            {
                return children[children.Length - 1];
            }
            return null;
        }

        public int[] GetRuntimeId()
        {
            return null;
        }

        public System.Windows.Rect BoundingRectangle
        {
            get { return System.Windows.Rect.Empty; }
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
            get { return this; }
        }

        public IRawElementProviderFragment ElementProviderFromPoint(double x, double y)
        {
            System.Windows.Point point = new System.Windows.Point(x, y);
            RowsPanelProvider panel = this.Panel();
            SurfaceItemProvider cell = panel != null ? panel.CellAt(point) : null;
            if (cell != null)
            {
                return cell;
            }
            SurfaceItemProvider[] children = this.Children();
            foreach (SurfaceItemProvider child in children)
            {
                if (child.IsPart && child.BoundingRectangle.Contains(point))
                {
                    return child;
                }
            }
            foreach (SurfaceItemProvider child in children)
            {
                if (!child.IsPart && child.BoundingRectangle.Contains(point))
                {
                    return child;
                }
            }
            return this;
        }

        public IRawElementProviderFragment GetFocus()
        {
            return null;
        }
    }

    /// <summary>
    /// One exposed item, or one exposed part of an item, as a child of the surface. Reads go
    /// to the surface's current item with the same id, so state is never stale.
    /// </summary>
    internal sealed class SurfaceItemProvider
        : IRawElementProviderFragment, IInvokeProvider, IToggleProvider, IGridItemProvider, IValueProvider
    {
        private readonly SurfaceRootProvider root;
        private readonly int index;
        private readonly SurfaceItem original;
        private readonly UiaShape shape;
        private readonly Exposure exposure;
        private readonly bool isPart;
        private readonly RowProvider row;
        private readonly int position;

        internal SurfaceItemProvider(
            SurfaceRootProvider root, int index, SurfaceItem item, UiaShape shape, Exposure exposure, bool isPart)
            : this(root, index, item, shape, exposure, isPart, null, index)
        {
        }

        /// <summary>A cell of the rows exposure: `row` is its parent, `position` its place in the
        /// row, and `index` a number no other element of the surface uses, for its runtime id.</summary>
        internal SurfaceItemProvider(
            SurfaceRootProvider root, int index, SurfaceItem item, UiaShape shape, Exposure exposure, bool isPart,
            RowProvider row, int position)
        {
            this.root = root;
            this.index = index;
            this.original = item;
            this.shape = shape;
            this.exposure = exposure;
            this.isPart = isPart;
            this.row = row;
            this.position = position;
        }

        /// <summary>The item this provider was built for.</summary>
        internal SurfaceItem Item
        {
            get { return this.original; }
        }

        private bool Invokable
        {
            get { return this.shape == UiaShape.Button || this.shape == UiaShape.Cell || this.shape == UiaShape.RowCell; }
        }

        /// <summary>True for a part of another item, such as a box's arrow.</summary>
        internal bool IsPart
        {
            get { return this.isPart; }
        }

        private SurfaceItem Current()
        {
            if (this.original.Id == null)
            {
                return this.original;
            }
            if (this.row != null)
            {
                RowsPanelProvider panel = this.root.Panel();
                SurfaceItem found = panel != null ? panel.Find(this.original.Id) : null;
                return found ?? this.original;
            }
            foreach (SurfaceItemProvider sibling in this.root.Children())
            {
                if (sibling.original.Id == this.original.Id)
                {
                    return sibling.original;
                }
            }
            return this.original;
        }

        public ProviderOptions ProviderOptions
        {
            get { return ProviderOptions.ServerSideProvider; }
        }

        public object GetPatternProvider(int patternId)
        {
            if (patternId == InvokePatternIdentifiers.Pattern.Id && this.Invokable)
            {
                return this;
            }
            if (patternId == ValuePatternIdentifiers.Pattern.Id && this.shape == UiaShape.RowCell)
            {
                return this;
            }
            if (patternId == TogglePatternIdentifiers.Pattern.Id && this.shape == UiaShape.CheckBox)
            {
                return this;
            }
            if (patternId == GridItemPatternIdentifiers.Pattern.Id && this.shape == UiaShape.Cell)
            {
                return this;
            }
            return null;
        }

        public object GetPropertyValue(int propertyId)
        {
            SurfaceItem item = this.Current();
            if (propertyId == AutomationElementIdentifiers.ControlTypeProperty.Id)
            {
                return this.ControlTypeId();
            }
            if (propertyId == AutomationElementIdentifiers.NameProperty.Id)
            {
                if (this.exposure == Exposure.Bare || this.exposure == Exposure.IdOnly)
                {
                    return string.Empty;
                }
                if (this.shape == UiaShape.RowCell)
                {
                    return (item.ColumnName ?? string.Empty) + " row" + (item.Row - 1);
                }
                return item.Text.Length > 0 ? item.Text : (item.Name ?? string.Empty);
            }
            if (propertyId == AutomationElementIdentifiers.AutomationIdProperty.Id)
            {
                return this.shape == UiaShape.Text ? null : (item.AutomationId ?? item.Id);
            }
            if (propertyId == AutomationElementIdentifiers.IsEnabledProperty.Id)
            {
                return item.Enabled && !this.root.Blocked();
            }
            if (propertyId == AutomationElementIdentifiers.IsOffscreenProperty.Id)
            {
                return this.root.Hidden(item.Bounds);
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
            if (propertyId == AutomationElementIdentifiers.IsInvokePatternAvailableProperty.Id)
            {
                return this.Invokable;
            }
            if (propertyId == AutomationElementIdentifiers.IsValuePatternAvailableProperty.Id)
            {
                return this.shape == UiaShape.RowCell;
            }
            if (propertyId == AutomationElementIdentifiers.IsTogglePatternAvailableProperty.Id)
            {
                return this.shape == UiaShape.CheckBox;
            }
            if (propertyId == AutomationElementIdentifiers.IsGridItemPatternAvailableProperty.Id)
            {
                return this.shape == UiaShape.Cell;
            }
            return null;
        }

        private int ControlTypeId()
        {
            if (this.shape == UiaShape.Button || this.shape == UiaShape.InertButton)
            {
                return ControlType.Button.Id;
            }
            if (this.shape == UiaShape.CheckBox)
            {
                return ControlType.CheckBox.Id;
            }
            if (this.shape == UiaShape.Cell || this.shape == UiaShape.RowCell)
            {
                return ControlType.DataItem.Id;
            }
            if (this.shape == UiaShape.Image)
            {
                return ControlType.Image.Id;
            }
            return ControlType.Text.Id;
        }

        public IRawElementProviderSimple HostRawElementProvider
        {
            get { return null; }
        }

        public IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            if (direction == NavigateDirection.Parent)
            {
                return this.row != null ? (IRawElementProviderFragment)this.row : this.root;
            }
            IRawElementProviderFragment[] siblings = this.row != null ? this.row.Cells : this.root.TopChildren();
            if (direction == NavigateDirection.NextSibling && this.position + 1 < siblings.Length)
            {
                return siblings[this.position + 1];
            }
            if (direction == NavigateDirection.PreviousSibling && this.position > 0
                && this.position - 1 < siblings.Length)
            {
                return siblings[this.position - 1];
            }
            return null;
        }

        public int[] GetRuntimeId()
        {
            return new[] { AutomationInteropProvider.AppendRuntimeId, this.index + 1 };
        }

        public System.Windows.Rect BoundingRectangle
        {
            get { return this.root.ScreenRect(this.Current().Bounds); }
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

        public void Invoke()
        {
            SurfaceItem item = this.Current();
            if (!item.Enabled || this.root.Blocked())
            {
                throw new ElementNotEnabledException();
            }
            if (item.Click != null)
            {
                this.root.Post(item.Click);
            }
        }

        public void Toggle()
        {
            SurfaceItem item = this.Current();
            if (!item.Enabled || this.root.Blocked())
            {
                throw new ElementNotEnabledException();
            }
            if (item.Click != null)
            {
                this.root.Send(item.Click);
            }
        }

        public ToggleState ToggleState
        {
            get { return this.Current().Checked ? ToggleState.On : ToggleState.Off; }
        }

        public int Row
        {
            get { return this.Current().Row; }
        }

        public int Column
        {
            get { return this.Current().Column; }
        }

        public int RowSpan
        {
            get { return 1; }
        }

        public int ColumnSpan
        {
            get { return 1; }
        }

        public IRawElementProviderSimple ContainingGrid
        {
            get { return this.root; }
        }

        public string Value
        {
            get { return this.Current().Text; }
        }

        public bool IsReadOnly
        {
            get { return true; }
        }

        public void SetValue(string value)
        {
            throw new InvalidOperationException("A grid cell is read-only");
        }
    }
}
