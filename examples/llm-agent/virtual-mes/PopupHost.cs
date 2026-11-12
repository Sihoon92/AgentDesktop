using System;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// A borderless top-level window that floats over its owner without taking
    /// activation: menu lists, dropdown lists and the radial menu. While one is
    /// open, a mouse press anywhere outside it closes it, and both that press
    /// and its release are swallowed, so the control underneath does not also
    /// react.
    /// </summary>
    internal sealed class PopupHost : Form
    {
        internal const string ReasonChoice = "choice";
        internal const string ReasonOutsideClick = "outside_click";
        internal const string ReasonEscape = "escape";
        internal const string ReasonDeactivated = "deactivated";

        private const int WsExNoActivate = 0x08000000;

        private static readonly List<PopupHost> Open = new List<PopupHost>();
        private static readonly OutsideClickFilter Filter = new OutsideClickFilter();
        private static bool filterInstalled;

        private readonly Surface surface;
        private readonly Form anchor;
        private readonly Action<string> onClosed;
        private bool closed;

        private PopupHost(Form anchor, UiRegion region, Exposure exposure, Action<string> onClosed)
        {
            this.anchor = anchor;
            this.onClosed = onClosed;
            this.FormBorderStyle = FormBorderStyle.None;
            this.StartPosition = FormStartPosition.Manual;
            this.ShowInTaskbar = false;
            this.AutoScaleMode = AutoScaleMode.None;
            this.BackColor = Ui.Line;
            this.Padding = new Padding(1);
            this.surface = new Surface(region, exposure);
            this.surface.Dock = DockStyle.Fill;
            this.Controls.Add(this.surface);
        }

        /// <summary>
        /// Opens a popup at a screen position. Any popup already open closes
        /// first, as if the user had clicked outside it.
        /// </summary>
        internal static PopupHost Show(
            Form owner, Rectangle screenBounds, UiRegion region, Exposure exposure, string title,
            Action<string> onClosed)
        {
            CloseAll(ReasonOutsideClick);
            PopupHost popup = new PopupHost(owner, region, exposure, onClosed);
            popup.Text = title;
            popup.Bounds = screenBounds;
            if (!filterInstalled)
            {
                Application.AddMessageFilter(Filter);
                filterInstalled = true;
            }
            Open.Add(popup);
            owner.Deactivate += popup.OnAnchorDeactivated;
            popup.Show(owner);
            return popup;
        }

        internal static void CloseAll(string reason)
        {
            foreach (PopupHost popup in Open.ToArray())
            {
                popup.Close(reason);
            }
        }

        internal Surface Surface
        {
            get { return this.surface; }
        }

        internal void Close(string reason)
        {
            if (this.closed)
            {
                return;
            }
            this.closed = true;
            Open.Remove(this);
            this.anchor.Deactivate -= this.OnAnchorDeactivated;
            this.Close();
            if (this.onClosed != null)
            {
                this.onClosed(reason);
            }
        }

        protected override bool ShowWithoutActivation
        {
            get { return true; }
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams parameters = base.CreateParams;
                parameters.ExStyle |= WsExNoActivate;
                return parameters;
            }
        }

        private void OnAnchorDeactivated(object sender, EventArgs e)
        {
            this.CloseUnlessStillOurs();
        }

        protected override void OnDeactivate(EventArgs e)
        {
            base.OnDeactivate(e);
            this.CloseUnlessStillOurs();
        }

        /// <summary>
        /// Closes the popup when activation has left it and its anchor. The
        /// check runs after activation settles: an automation tool can make
        /// the popup itself the active window before it clicks in it, and
        /// that must not close it.
        /// </summary>
        private void CloseUnlessStillOurs()
        {
            if (this.closed || !this.anchor.IsHandleCreated)
            {
                return;
            }
            this.anchor.BeginInvoke(new MethodInvoker(delegate
            {
                Form active = Form.ActiveForm;
                if (!this.closed && active != this && active != this.anchor)
                {
                    this.Close(ReasonDeactivated);
                }
            }));
        }

        private bool Owns(IntPtr window)
        {
            return window == this.Handle || IsChild(this.Handle, window);
        }

        [DllImport("user32.dll")]
        private static extern bool IsChild(IntPtr parent, IntPtr window);

        private sealed class OutsideClickFilter : IMessageFilter
        {
            private const int WmKeyDown = 0x0100;
            private const int VkEscape = 0x1B;
            private const int WmLeftDown = 0x0201;
            private const int WmLeftUp = 0x0202;
            private const int WmRightDown = 0x0204;
            private const int WmRightUp = 0x0205;
            private const int WmMiddleDown = 0x0207;
            private const int WmMiddleUp = 0x0208;
            private const int WmNcLeftDown = 0x00A1;
            private const int WmNcLeftUp = 0x00A2;
            private const int WmNcRightDown = 0x00A4;
            private const int WmNcRightUp = 0x00A5;

            private int swallowUp;

            public bool PreFilterMessage(ref Message m)
            {
                if (m.Msg == WmKeyDown && (int)m.WParam == VkEscape && Open.Count > 0)
                {
                    CloseAll(ReasonEscape);
                    return true;
                }
                int release = ReleaseFor(m.Msg);
                if (release == 0)
                {
                    if (this.swallowUp != 0 && m.Msg == this.swallowUp)
                    {
                        this.swallowUp = 0;
                        return true;
                    }
                    return false;
                }
                this.swallowUp = 0;
                if (Open.Count == 0)
                {
                    return false;
                }
                foreach (PopupHost popup in Open)
                {
                    if (popup.Owns(m.HWnd))
                    {
                        return false;
                    }
                }
                this.swallowUp = release;
                CloseAll(ReasonOutsideClick);
                return true;
            }

            /// <summary>For a button-down message, the matching button-up message; otherwise zero.</summary>
            private static int ReleaseFor(int message)
            {
                switch (message)
                {
                    case WmLeftDown: return WmLeftUp;
                    case WmRightDown: return WmRightUp;
                    case WmMiddleDown: return WmMiddleUp;
                    case WmNcLeftDown: return WmNcLeftUp;
                    case WmNcRightDown: return WmNcRightUp;
                    default: return 0;
                }
            }
        }
    }
}
