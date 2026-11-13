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
        Cell
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

        internal SurfaceRootProvider(Surface surface, IntPtr handle)
        {
            this.surface = surface;
            this.handle = handle;
        }

        internal static UiaShape ShapeOf(Exposure exposure, SurfaceItem item)
        {
            bool hasText = item.Text.Length > 0;
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
                    foreach (SurfaceItem item in source)
                    {
                        UiaShape shape = ShapeOf(this.surface.Exposure, item);
                        if (shape != UiaShape.Hidden)
                        {
                            built.Add(new SurfaceItemProvider(this, null, built.Count, item, shape));
                        }
                    }
                    this.cachedSource = source;
                    this.cachedChildren = built.ToArray();
                }
                return this.cachedChildren;
            }
        }

        internal Exposure Exposure
        {
            get { return this.surface.Exposure; }
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

        public object GetPropertyValue(int propertyId)
        {
            return null;
        }

        public IRawElementProviderSimple HostRawElementProvider
        {
            get { return AutomationInteropProvider.HostProviderFromHandle(this.handle); }
        }

        public IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            SurfaceItemProvider[] children = this.Children();
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
            foreach (SurfaceItemProvider child in this.Children())
            {
                foreach (SurfaceItemProvider part in child.Parts())
                {
                    if (part.BoundingRectangle.Contains(point))
                    {
                        return part;
                    }
                }
                if (child.BoundingRectangle.Contains(point))
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
    /// One exposed item, or one exposed part of an item. Reads go to the surface's current
    /// item with the same id, so state is never stale.
    /// </summary>
    internal sealed class SurfaceItemProvider
        : IRawElementProviderFragment, IInvokeProvider, IToggleProvider, IGridItemProvider
    {
        private readonly SurfaceRootProvider root;
        private readonly SurfaceItemProvider owner;
        private readonly int index;
        private readonly SurfaceItem original;
        private readonly UiaShape shape;
        private readonly SurfaceItemProvider[] parts;

        internal SurfaceItemProvider(
            SurfaceRootProvider root, SurfaceItemProvider owner, int index, SurfaceItem item, UiaShape shape)
        {
            this.root = root;
            this.owner = owner;
            this.index = index;
            this.original = item;
            this.shape = shape;
            List<SurfaceItemProvider> built = new List<SurfaceItemProvider>();
            if (owner == null && item.Parts != null)
            {
                foreach (SurfaceItem part in item.Parts)
                {
                    UiaShape partShape = SurfaceRootProvider.ShapeOf(root.Exposure, part);
                    if (partShape != UiaShape.Hidden)
                    {
                        built.Add(new SurfaceItemProvider(root, this, built.Count, part, partShape));
                    }
                }
            }
            this.parts = built.ToArray();
        }

        internal SurfaceItemProvider[] Parts()
        {
            return this.parts;
        }

        private SurfaceItemProvider[] Siblings()
        {
            return this.owner == null ? this.root.Children() : this.owner.parts;
        }

        private SurfaceItem Current()
        {
            if (this.original.Id == null)
            {
                return this.original;
            }
            if (this.owner != null)
            {
                SurfaceItem[] current = this.owner.Current().Parts;
                if (current != null)
                {
                    foreach (SurfaceItem part in current)
                    {
                        if (part.Id == this.original.Id)
                        {
                            return part;
                        }
                    }
                }
                return this.original;
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
            if (patternId == InvokePatternIdentifiers.Pattern.Id
                && (this.shape == UiaShape.Button || this.shape == UiaShape.Cell))
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
                return item.Text.Length > 0 ? item.Text : (item.Name ?? string.Empty);
            }
            if (propertyId == AutomationElementIdentifiers.AutomationIdProperty.Id)
            {
                return this.shape == UiaShape.Text ? null : item.Id;
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
                return this.shape == UiaShape.Button || this.shape == UiaShape.Cell;
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
            if (this.shape == UiaShape.Button)
            {
                return ControlType.Button.Id;
            }
            if (this.shape == UiaShape.CheckBox)
            {
                return ControlType.CheckBox.Id;
            }
            if (this.shape == UiaShape.Cell)
            {
                return ControlType.DataItem.Id;
            }
            return ControlType.Text.Id;
        }

        public IRawElementProviderSimple HostRawElementProvider
        {
            get { return null; }
        }

        public IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            SurfaceItemProvider[] siblings = this.Siblings();
            if (direction == NavigateDirection.Parent)
            {
                return this.owner != null ? (IRawElementProviderFragment)this.owner : this.root;
            }
            if (direction == NavigateDirection.FirstChild && this.parts.Length > 0)
            {
                return this.parts[0];
            }
            if (direction == NavigateDirection.LastChild && this.parts.Length > 0)
            {
                return this.parts[this.parts.Length - 1];
            }
            if (direction == NavigateDirection.NextSibling && this.index + 1 < siblings.Length)
            {
                return siblings[this.index + 1];
            }
            if (direction == NavigateDirection.PreviousSibling && this.index > 0
                && this.index - 1 < siblings.Length)
            {
                return siblings[this.index - 1];
            }
            return null;
        }

        public int[] GetRuntimeId()
        {
            if (this.owner != null)
            {
                return new[] { AutomationInteropProvider.AppendRuntimeId, this.owner.index + 1, this.index + 1 };
            }
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
    }
}
