using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// The round menu a right-click opens. Stage 1 offers export and three
    /// other entries; export turns it, in place, into stage 2 with pdf, excel
    /// and html. Every label is painted, never a control.
    /// </summary>
    internal static class RadialMenu
    {
        private sealed class Slice
        {
            internal string Label;
            internal float Start;
            internal float Sweep;
            internal Color Fill;
            internal Action Click;
        }

        internal static void Show(
            Form owner, Point screenCenter, Session session, Func<QueryResult> source, string sourceName,
            string defaultName)
        {
            int diameter = Ui.Px(170);
            int stage = 1;
            PopupHost popup = PopupHost.Show(
                owner,
                new Rectangle(screenCenter.X - diameter / 2, screenCenter.Y - diameter / 2, diameter, diameter),
                UiRegion.Radial, session.ExposureOf(UiRegion.Radial), "메뉴",
                delegate(string reason) { session.RadialClosed(stage, reason); });
            popup.Padding = Padding.Empty;
            using (GraphicsPath circle = new GraphicsPath())
            {
                circle.AddEllipse(0, 0, diameter, diameter);
                popup.Region = new System.Drawing.Region(circle);
            }

            Action<string> dismiss = delegate(string label)
            {
                session.ExportChoice(ExportFlow.RouteRadial, label);
                popup.Close(PopupHost.ReasonChoice);
            };

            List<Slice> second = new List<Slice>();
            second.Add(NewSlice("excel", 270f, 120f, Ui.ExcelGreen, delegate
            {
                dismiss("excel");
                owner.BeginInvoke(new MethodInvoker(delegate
                {
                    QueryResult result = source();
                    if (result == null)
                    {
                        ExportFlow.RejectNoData(owner, session);
                    }
                    else
                    {
                        ExportFlow.SaveAs(owner, session, result, sourceName, defaultName);
                    }
                }));
            }));
            second.Add(NewSlice("html", 30f, 120f, Ui.Accent, delegate { dismiss("html"); }));
            second.Add(NewSlice("pdf", 150f, 120f, Ui.Accent, delegate { dismiss("pdf"); }));

            List<Slice> first = new List<Slice>();
            first.Add(NewSlice("export", 225f, 90f, Ui.ExportOrange, delegate
            {
                stage = 2;
                session.RadialOpened(stage, sourceName);
                popup.Surface.SetItems(Items(second, diameter));
            }));
            first.Add(NewSlice("copy", 315f, 90f, Ui.Accent, delegate { dismiss("copy"); }));
            first.Add(NewSlice("print", 45f, 90f, Ui.Accent, delegate { dismiss("print"); }));
            first.Add(NewSlice("close", 135f, 90f, Ui.Accent, delegate { dismiss("close"); }));

            session.RadialOpened(stage, sourceName);
            popup.Surface.SetItems(Items(first, diameter));
        }

        private static Slice NewSlice(string label, float start, float sweep, Color fill, Action click)
        {
            Slice slice = new Slice();
            slice.Label = label;
            slice.Start = start;
            slice.Sweep = sweep;
            slice.Fill = fill;
            slice.Click = click;
            return slice;
        }

        private static List<SurfaceItem> Items(List<Slice> slices, int diameter)
        {
            float radius = diameter / 2f;
            float hub = Ui.Px(20);
            PointF center = new PointF(radius, radius);
            Rectangle disc = new Rectangle(0, 0, diameter, diameter);
            List<SurfaceItem> items = new List<SurfaceItem>();

            foreach (Slice each in slices)
            {
                Slice slice = each;
                double middle = (slice.Start + slice.Sweep / 2.0) * Math.PI / 180.0;
                PointF labelCenter = new PointF(
                    center.X + (float)Math.Cos(middle) * radius * 0.6f,
                    center.Y + (float)Math.Sin(middle) * radius * 0.6f);
                Rectangle labelBox = new Rectangle(
                    (int)labelCenter.X - Ui.Px(30), (int)labelCenter.Y - Ui.Px(12), Ui.Px(60), Ui.Px(24));

                SurfaceItem item = new SurfaceItem();
                item.Id = "radial-" + slice.Label;
                item.Kind = ItemKind.Graphic;
                item.Text = slice.Label;
                item.Bounds = labelBox;
                item.Click = slice.Click;
                item.Contains = delegate(Point point)
                {
                    float dx = point.X - center.X;
                    float dy = point.Y - center.Y;
                    double distance = Math.Sqrt(dx * dx + dy * dy);
                    if (distance < hub || distance > radius)
                    {
                        return false;
                    }
                    double angle = Math.Atan2(dy, dx) * 180.0 / Math.PI;
                    double offset = ((angle - slice.Start) % 360.0 + 360.0) % 360.0;
                    return offset < slice.Sweep;
                };
                item.Paint = delegate(Graphics graphics, Rectangle bounds)
                {
                    graphics.SmoothingMode = SmoothingMode.AntiAlias;
                    using (SolidBrush fill = new SolidBrush(slice.Fill))
                    using (Pen edge = new Pen(Color.White, Math.Max(2f, Ui.Scale * 2f)))
                    {
                        graphics.FillPie(fill, disc, slice.Start, slice.Sweep);
                        graphics.DrawPie(edge, disc, slice.Start, slice.Sweep);
                    }
                    TextRenderer.DrawText(
                        graphics, slice.Label, Ui.Bold, bounds, Color.White,
                        TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter
                        | TextFormatFlags.SingleLine | TextFormatFlags.NoPrefix);
                };
                items.Add(item);
            }

            SurfaceItem center0 = new SurfaceItem();
            center0.Kind = ItemKind.Graphic;
            center0.Bounds = new Rectangle((int)(radius - hub), (int)(radius - hub), (int)(2 * hub), (int)(2 * hub));
            center0.Paint = delegate(Graphics graphics, Rectangle bounds)
            {
                graphics.SmoothingMode = SmoothingMode.AntiAlias;
                graphics.FillEllipse(Brushes.White, bounds);
            };
            items.Add(center0);
            return items;
        }
    }
}
