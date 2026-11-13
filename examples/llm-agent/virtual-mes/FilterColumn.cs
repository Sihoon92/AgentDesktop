using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// The filter column of one screen: filter names and boxes drawn on a
    /// Surface, the two date inputs and the query button as standard controls,
    /// and the dropdown lists the boxes open.
    /// </summary>
    internal sealed class FilterColumn
    {
        internal const string ClosedBySelect = "select_button";
        internal const string ClosedByItem = "item_click";
        internal const string ClosedByTyped = "typed";
        internal const string EmptyDate = "0000-00-00";

        private sealed class MultiBox
        {
            internal string Key;
            internal string[] Codes;
            internal string[] Names;
            internal List<string> Selected;
            internal bool Enabled;
        }

        private readonly Session session;
        private readonly ScreenSpec spec;
        private readonly Form frame;
        private readonly Panel host;
        private readonly Surface surface;
        private readonly Dictionary<string, Rectangle> boxBounds = new Dictionary<string, Rectangle>();
        private readonly MultiBox[] shopBoxes;
        private TextBox fromBox;
        private TextBox toBox;
        private Button queryButton;

        internal event EventHandler QueryRequested;

        internal FilterColumn(Session session, ScreenSpec spec, Form frame, Panel host)
        {
            this.session = session;
            this.spec = spec;
            this.frame = frame;
            this.host = host;
            this.shopBoxes = this.BuildShopBoxes();

            this.surface = new Surface(UiRegion.Filters, session.ExposureOf(UiRegion.Filters));
            this.surface.Dock = DockStyle.Fill;
            this.surface.BackColor = Ui.PanelBack;
            host.Controls.Add(this.surface);

            if (spec.Kind != ScreenSpec.KindEmpty)
            {
                this.AddQueryButton();
            }
            if (this.Has(ScreenSpec.FilterPeriod))
            {
                this.AddDateBoxes();
            }
            this.Rebuild();
        }

        /// <summary>Copies typed dates into the filters, recording a change for each one that differs.</summary>
        internal void CommitDates()
        {
            if (this.fromBox == null)
            {
                return;
            }
            this.CommitDate("from", this.fromBox.Text, this.session.Filters.From);
            this.CommitDate("to", this.toBox.Text, this.session.Filters.To);
        }

        private void CommitDate(string key, string typed, string stored)
        {
            if (!this.fromBox.Enabled || typed == (stored ?? EmptyDate))
            {
                return;
            }
            this.session.ChangeFilter(key, typed, ClosedByTyped);
        }

        private bool Has(string filter)
        {
            return Array.IndexOf(this.spec.Filters, filter) >= 0;
        }

        private MultiBox[] BuildShopBoxes()
        {
            MultiBox division = new MultiBox();
            division.Key = "shop-1";
            division.Codes = new[] { "BA0" };
            division.Names = new[] { "전지" };

            MultiBox site = new MultiBox();
            site.Key = "shop-2";
            site.Codes = new[] { "UA0" };
            site.Names = new[] { "#울산" };

            MultiBox shop = new MultiBox();
            shop.Key = "shop-3";
            shop.Codes = MesData.ShopCodes;
            shop.Names = new string[MesData.ShopNames.Length];
            for (int index = 0; index < shop.Names.Length; index++)
            {
                shop.Names[index] = MesData.ShopNamePrefix + MesData.ShopNames[index];
            }

            MultiBox[] boxes = { division, site, shop };
            for (int index = 0; index < boxes.Length; index++)
            {
                boxes[index].Selected = new List<string>(boxes[index].Codes);
                boxes[index].Enabled = this.spec.ShopBoxEnabled[index];
            }
            return boxes;
        }

        private static string Summary(MultiBox box)
        {
            if (box.Selected.Count == box.Codes.Length)
            {
                return FilterValues.All;
            }
            if (box.Selected.Count == 0)
            {
                return "선택 없음";
            }
            if (box.Selected.Count == 1)
            {
                return box.Names[Array.IndexOf(box.Codes, box.Selected[0])];
            }
            return box.Selected.Count + "개";
        }

        private void AddQueryButton()
        {
            this.queryButton = new Button();
            this.queryButton.Name = "query-button";
            this.queryButton.Text = "조회(F5)";
            this.queryButton.Font = Ui.Bold;
            this.queryButton.Size = new Size(this.host.Width - Ui.Px(20), Ui.Px(30));
            this.queryButton.Location = new Point(Ui.Px(10), this.host.Height - Ui.Px(40));
            this.queryButton.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Bottom;
            this.queryButton.Click += delegate
            {
                if (this.QueryRequested != null)
                {
                    this.QueryRequested(this, EventArgs.Empty);
                }
            };
            this.host.Controls.Add(this.queryButton);
            this.queryButton.BringToFront();
        }

        private void AddDateBoxes()
        {
            this.fromBox = this.DateBox("date-from", "시작일");
            this.toBox = this.DateBox("date-to", "종료일");
        }

        private TextBox DateBox(string name, string accessibleName)
        {
            TextBox box = new TextBox();
            box.Name = name;
            box.AccessibleName = accessibleName;
            box.Font = Ui.Font;
            box.Text = EmptyDate;
            box.Enabled = false;
            box.Leave += delegate { this.CommitDates(); };
            this.host.Controls.Add(box);
            box.BringToFront();
            return box;
        }

        private void Rebuild()
        {
            List<SurfaceItem> items = new List<SurfaceItem>();
            this.boxBounds.Clear();
            int left = Ui.Px(10);
            int width = this.host.Width - Ui.Px(20);
            int top = Ui.Px(10);
            foreach (string filter in this.spec.Filters)
            {
                items.Add(Label("label-" + filter, LabelText(filter), new Rectangle(left, top, width, Ui.Px(18))));
                top += Ui.Px(20);
                if (filter == ScreenSpec.FilterShop)
                {
                    int gap = Ui.Px(4);
                    int each = (width - 2 * gap) / 3;
                    for (int index = 0; index < this.shopBoxes.Length; index++)
                    {
                        MultiBox box = this.shopBoxes[index];
                        Rectangle bounds = new Rectangle(left + index * (each + gap), top, each, Ui.Px(24));
                        SurfaceItem item = this.Box("filter-" + box.Key, Summary(box), bounds);
                        item.Enabled = box.Enabled;
                        this.WireOpening(item, box.Key, delegate { this.OpenMulti(box); });
                        items.Add(item);
                    }
                }
                else
                {
                    string key = filter;
                    Rectangle bounds = new Rectangle(left, top, width, Ui.Px(24));
                    SurfaceItem item = this.Box("filter-" + filter, this.Value(filter), bounds);
                    this.WireOpening(item, key, delegate { this.OpenSingle(key); });
                    items.Add(item);
                }
                top += Ui.Px(24);
                if (filter == ScreenSpec.FilterPeriod)
                {
                    top += Ui.Px(4);
                    int tilde = Ui.Px(16);
                    int each = (width - tilde) / 2;
                    this.fromBox.SetBounds(left, top, each, Ui.Px(24));
                    this.toBox.SetBounds(left + each + tilde, top, width - each - tilde, Ui.Px(24));
                    SurfaceItem between = Label("label-tilde", "~", new Rectangle(left + each, top, tilde, Ui.Px(24)));
                    between.Align = ContentAlignment.MiddleCenter;
                    items.Add(between);
                    bool custom = this.session.Filters.Period == FilterValues.PeriodCustom;
                    this.fromBox.Enabled = custom;
                    this.toBox.Enabled = custom;
                    top += Ui.Px(24);
                }
                top += Ui.Px(10);
            }
            this.surface.SetItems(items);
        }

        /// <summary>
        /// Makes the box open its list. By default a click anywhere on it opens the list. When
        /// boxes open by the arrow only, a click on the body puts a text caret in the box and
        /// opens nothing, and only the arrow square at the right end opens the list. The arrow
        /// is a part of the box: a child button in the ref exposure, absent from the others.
        /// </summary>
        private void WireOpening(SurfaceItem item, string filter, Action open)
        {
            if (!this.session.CombosOpenByArrowOnly)
            {
                item.Click = open;
                return;
            }
            Rectangle body = item.Bounds;
            string text = item.Text;
            item.Click = delegate
            {
                this.session.ComboBodyClicked(filter);
                this.surface.ShowCaret(CaretLine(body, text));
            };
            SurfaceItem arrow = new SurfaceItem();
            arrow.Id = item.Id + "-arrow";
            arrow.Kind = ItemKind.Button;
            arrow.Name = "열기";
            arrow.Enabled = item.Enabled;
            arrow.Bounds = new Rectangle(body.Right - body.Height, body.Top, body.Height, body.Height);
            arrow.Click = delegate
            {
                this.session.ComboArrowClicked(filter);
                open();
            };
            item.Parts = new[] { arrow };
        }

        /// <summary>A one-pixel-wide caret just after the box's text, kept clear of the arrow.</summary>
        private static Rectangle CaretLine(Rectangle body, string text)
        {
            Size measured = TextRenderer.MeasureText(text, Ui.Font, Size.Empty, TextFormatFlags.NoPadding);
            int x = Math.Min(body.Left + Ui.Px(6) + measured.Width + Ui.Px(1), body.Right - body.Height - Ui.Px(2));
            return new Rectangle(x, body.Top + Ui.Px(4), Math.Max(1, Ui.Px(1)), body.Height - Ui.Px(8));
        }

        private static string LabelText(string filter)
        {
            if (filter == ScreenSpec.FilterShop)
            {
                return "Shop";
            }
            if (filter == ScreenSpec.FilterProcess)
            {
                return "공정";
            }
            if (filter == ScreenSpec.FilterProduct)
            {
                return "제품";
            }
            if (filter == ScreenSpec.FilterEquipment)
            {
                return "설비";
            }
            return "기간";
        }

        private static SurfaceItem Label(string id, string text, Rectangle bounds)
        {
            SurfaceItem item = new SurfaceItem();
            item.Id = id;
            item.Text = text;
            item.Bold = true;
            item.Bounds = bounds;
            return item;
        }

        private SurfaceItem Box(string id, string text, Rectangle bounds)
        {
            SurfaceItem item = new SurfaceItem();
            item.Id = id;
            item.Kind = ItemKind.Button;
            item.Text = text;
            item.Back = Color.White;
            item.Border = true;
            item.DropArrow = true;
            item.Bounds = bounds;
            this.boxBounds[id] = bounds;
            return item;
        }

        private string Value(string filter)
        {
            FilterValues filters = this.session.Filters;
            if (filter == ScreenSpec.FilterProcess)
            {
                return filters.Process;
            }
            if (filter == ScreenSpec.FilterProduct)
            {
                return filters.Product;
            }
            if (filter == ScreenSpec.FilterEquipment)
            {
                return filters.Equipment;
            }
            return filters.Period;
        }

        private static string[] Options(string filter)
        {
            if (filter == ScreenSpec.FilterProcess)
            {
                return MesData.ProcessOptions();
            }
            if (filter == ScreenSpec.FilterProduct)
            {
                return MesData.ProductOptions();
            }
            if (filter == ScreenSpec.FilterEquipment)
            {
                return MesData.EquipmentOptions();
            }
            return FilterValues.Periods;
        }

        private Rectangle Below(string boxId, int width, int height)
        {
            Rectangle box = this.boxBounds[boxId];
            Point origin = this.surface.PointToScreen(new Point(box.Left, box.Bottom));
            return new Rectangle(origin.X, origin.Y, Math.Max(width, box.Width), height);
        }

        private void OpenSingle(string filter)
        {
            string[] options = Options(filter);
            int rowHeight = Ui.Px(26);
            Rectangle bounds = this.Below("filter-" + filter, 0, options.Length * rowHeight + 2);
            PopupHost popup = PopupHost.Show(
                this.frame, bounds, UiRegion.Filters, this.session.ExposureOf(UiRegion.Filters), "목록", null);

            List<SurfaceItem> items = new List<SurfaceItem>();
            for (int index = 0; index < options.Length; index++)
            {
                string option = options[index];
                SurfaceItem item = new SurfaceItem();
                item.Id = "option-" + index;
                item.Kind = ItemKind.Button;
                item.Text = option;
                item.Back = option == this.Value(filter) ? Ui.Highlight : Color.White;
                item.Bounds = new Rectangle(0, index * rowHeight, bounds.Width - 2, rowHeight);
                item.Click = delegate
                {
                    popup.Close(PopupHost.ReasonChoice);
                    if (option != this.Value(filter))
                    {
                        this.session.ChangeFilter(filter, option, ClosedByItem);
                    }
                    this.Rebuild();
                };
                items.Add(item);
            }
            popup.Surface.SetItems(items);
        }

        private void OpenMulti(MultiBox box)
        {
            List<string> pending = new List<string>(box.Selected);
            int topHeight = Ui.Px(34);
            int rowHeight = Ui.Px(28);
            Rectangle bounds = this.Below(
                "filter-" + box.Key, Ui.Px(300), topHeight + box.Codes.Length * rowHeight + 2);

            PopupHost popup = null;
            Action<string> closed = delegate(string reason)
            {
                if (reason != PopupHost.ReasonEscape)
                {
                    this.Apply(box, pending, reason);
                }
            };
            popup = PopupHost.Show(
                this.frame, bounds, UiRegion.Filters, this.session.ExposureOf(UiRegion.Filters), "목록", closed);

            Action render = null;
            render = delegate
            {
                List<SurfaceItem> items = new List<SurfaceItem>();
                int inner = bounds.Width - 2;
                SurfaceItem band = new SurfaceItem();
                band.Back = Ui.HeaderBack;
                band.Bounds = new Rectangle(0, 0, inner, topHeight);
                items.Add(band);
                items.Add(this.TopButton("multi-all", "All", 0, delegate
                {
                    pending.Clear();
                    pending.AddRange(box.Codes);
                    render();
                }));
                items.Add(this.TopButton("multi-select", "Select", 1, delegate
                {
                    popup.Close(ClosedBySelect);
                }));
                for (int index = 0; index < box.Codes.Length; index++)
                {
                    string code = box.Codes[index];
                    int y = topHeight + index * rowHeight;
                    Action toggle = delegate
                    {
                        if (!pending.Remove(code))
                        {
                            pending.Add(code);
                        }
                        render();
                    };
                    SurfaceItem check = new SurfaceItem();
                    check.Id = "row-" + code;
                    check.Kind = ItemKind.Check;
                    check.Text = code;
                    check.Checked = pending.Contains(code);
                    check.Bounds = new Rectangle(0, y, Ui.Px(34), rowHeight);
                    check.Click = toggle;
                    items.Add(check);

                    SurfaceItem codeLabel = new SurfaceItem();
                    codeLabel.Id = "row-code-" + code;
                    codeLabel.Text = code;
                    codeLabel.Bounds = new Rectangle(Ui.Px(34), y, Ui.Px(56), rowHeight);
                    codeLabel.Click = toggle;
                    items.Add(codeLabel);

                    SurfaceItem nameLabel = new SurfaceItem();
                    nameLabel.Id = "row-name-" + code;
                    nameLabel.Text = box.Names[index];
                    nameLabel.Bounds = new Rectangle(Ui.Px(90), y, inner - Ui.Px(90), rowHeight);
                    nameLabel.Click = toggle;
                    items.Add(nameLabel);
                }
                popup.Surface.SetItems(items);
            };
            render();
        }

        private SurfaceItem TopButton(string id, string text, int slot, Action click)
        {
            SurfaceItem item = new SurfaceItem();
            item.Id = id;
            item.Kind = ItemKind.Button;
            item.Text = text;
            item.Back = Color.White;
            item.Border = true;
            item.Align = ContentAlignment.MiddleCenter;
            item.Bounds = new Rectangle(Ui.Px(8) + slot * Ui.Px(72), Ui.Px(6), Ui.Px(64), Ui.Px(22));
            item.Click = click;
            return item;
        }

        /// <summary>Applies the checked rows in list order and records the change when the selection differs.</summary>
        private void Apply(MultiBox box, List<string> pending, string closedBy)
        {
            List<string> ordered = new List<string>();
            foreach (string code in box.Codes)
            {
                if (pending.Contains(code))
                {
                    ordered.Add(code);
                }
            }
            bool changed = ordered.Count != box.Selected.Count;
            for (int index = 0; !changed && index < ordered.Count; index++)
            {
                changed = ordered[index] != box.Selected[index];
            }
            box.Selected = ordered;
            if (changed)
            {
                this.session.ChangeFilter(box.Key, ordered.ToArray(), closedBy);
            }
            this.Rebuild();
        }
    }
}
