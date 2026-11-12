using System;
using System.Drawing;
using System.Runtime.InteropServices;

namespace VirtualMesApp
{
    /// <summary>Sizes, fonts and colours shared by every window.</summary>
    internal static class Ui
    {
        internal static float Scale = 1f;
        internal static Font Font;
        internal static Font Bold;

        internal static readonly Color TitleBack = Color.FromArgb(34, 52, 77);
        internal static readonly Color Accent = Color.FromArgb(52, 80, 122);
        internal static readonly Color WindowBack = Color.FromArgb(233, 237, 242);
        internal static readonly Color PanelBack = Color.FromArgb(246, 248, 250);
        internal static readonly Color HeaderBack = Color.FromArgb(221, 227, 236);
        internal static readonly Color StatusBack = Color.FromArgb(213, 219, 228);
        internal static readonly Color Line = Color.FromArgb(135, 147, 165);
        internal static readonly Color CellLine = Color.FromArgb(207, 214, 224);
        internal static readonly Color Text = Color.FromArgb(31, 39, 51);
        internal static readonly Color DisabledBack = Color.FromArgb(223, 227, 233);
        internal static readonly Color DisabledText = Color.FromArgb(86, 96, 112);
        internal static readonly Color Highlight = Color.FromArgb(223, 232, 245);
        internal static readonly Color ExcelGreen = Color.FromArgb(29, 111, 66);
        internal static readonly Color ExportOrange = Color.FromArgb(181, 101, 29);

        [DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        /// <summary>
        /// Declares the process system-DPI aware before any window exists, so
        /// accessibility bounds are physical pixels, then measures the scale.
        /// </summary>
        internal static void Initialize()
        {
            SetProcessDPIAware();
            using (Graphics graphics = Graphics.FromHwnd(IntPtr.Zero))
            {
                Scale = graphics.DpiX / 96f;
            }
            Font = new Font("Malgun Gothic", 9f, FontStyle.Regular, GraphicsUnit.Point);
            Bold = new Font("Malgun Gothic", 9f, FontStyle.Bold, GraphicsUnit.Point);
        }

        /// <summary>Converts a size at 100% scaling to device pixels.</summary>
        internal static int Px(int logical)
        {
            return (int)Math.Round(logical * Scale);
        }
    }
}
