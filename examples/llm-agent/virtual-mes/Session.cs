using System;
using System.Collections.Generic;

namespace VirtualMesApp
{
    /// <summary>
    /// The app's state and the only writer of the event log. The windows change
    /// state through these methods, so every change is recorded once.
    /// </summary>
    internal sealed class Session
    {
        private readonly Options options;
        private readonly EventLog log;

        internal ScreenSpec Screen;
        internal FilterValues Filters = new FilterValues();
        internal QueryResult Result;

        /// <summary>The filters `Result` was queried with. Later filter changes do not touch it.</summary>
        internal FilterValues ResultFilters;

        internal Session(Options options, EventLog log)
        {
            this.options = options;
            this.log = log;

            Dictionary<string, object> expose = new Dictionary<string, object>();
            foreach (UiRegion region in Exposures.Regions)
            {
                expose[Exposures.Name(region)] = Exposures.Name(options.Expose[region]);
            }
            this.log.Write("app_started", EventLog.Fields(
                "profile", options.Profile, "expose", expose, "seed", options.Seed,
                "combo_open", options.ComboOpen));
        }

        internal Options Options
        {
            get { return this.options; }
        }

        internal Exposure ExposureOf(UiRegion region)
        {
            return this.options.Expose[region];
        }

        /// <summary>True when filter boxes open their list only from the arrow at their right end.</summary>
        internal bool CombosOpenByArrowOnly
        {
            get { return this.options.ComboOpen == Options.ComboArrow; }
        }

        internal void ComboBodyClicked(string filter)
        {
            this.log.Write("combo_body_clicked", EventLog.Fields("screen", this.Screen.Id, "filter", filter));
        }

        internal void ComboArrowClicked(string filter)
        {
            this.log.Write("combo_arrow_clicked", EventLog.Fields("screen", this.Screen.Id, "filter", filter));
        }

        internal void OpenMenu(string category)
        {
            this.log.Write("menu_opened", EventLog.Fields("category", category));
        }

        /// <summary>Stores one filter's new value and records it. Shop boxes 1 and 2 do not filter data.</summary>
        internal void ChangeFilter(string filter, object value, string closedBy)
        {
            if (filter == "shop-3")
            {
                this.Filters.Shop = new List<string>((string[])value);
            }
            else if (filter == ScreenSpec.FilterProcess)
            {
                this.Filters.Process = (string)value;
            }
            else if (filter == ScreenSpec.FilterProduct)
            {
                this.Filters.Product = (string)value;
            }
            else if (filter == ScreenSpec.FilterEquipment)
            {
                this.Filters.Equipment = (string)value;
            }
            else if (filter == ScreenSpec.FilterPeriod)
            {
                this.Filters.Period = (string)value;
            }
            else if (filter == "from")
            {
                this.Filters.From = (string)value;
            }
            else if (filter == "to")
            {
                this.Filters.To = (string)value;
            }
            this.log.Write("filter_changed", EventLog.Fields(
                "screen", this.Screen.Id, "filter", filter, "value", value, "closed_by", closedBy));
        }

        internal void StartQuery(FilterValues filters, string startedBy, int delayMs)
        {
            this.log.Write("query_started", EventLog.Fields(
                "screen", this.Screen.Id, "filters", filters.ToDictionary(),
                "started_by", startedBy, "delay_ms", delayMs));
        }

        internal void FinishQuery(QueryResult result, FilterValues filters)
        {
            this.Result = result;
            this.ResultFilters = filters;
            this.log.Write("query_finished", EventLog.Fields("screen", this.Screen.Id, "rows", result.Rows.Count));
        }

        internal void RejectQuery(string reason)
        {
            this.log.Write("query_rejected", EventLog.Fields("screen", this.Screen.Id, "reason", reason));
        }

        internal void ExportChoice(string route, string choice)
        {
            this.log.Write("export_choice", EventLog.Fields("route", route, "choice", choice));
        }

        internal void ExportSaved(string path, string source, int rows)
        {
            this.log.Write("export_saved", EventLog.Fields(
                "path", path, "source", source, "rows", rows, "screen", this.Screen.Id,
                "filters", (this.ResultFilters ?? this.Filters).ToDictionary()));
        }

        internal void ExportFailed(string path, string reason)
        {
            this.log.Write("export_failed", EventLog.Fields("path", path, "reason", reason));
        }

        internal void ExportRejected(string reason)
        {
            this.log.Write("export_rejected", EventLog.Fields("reason", reason));
        }

        internal void RadialOpened(int stage, string source)
        {
            this.log.Write("radial_opened", EventLog.Fields("stage", stage, "source", source));
        }

        internal void RadialClosed(int stage, string reason)
        {
            this.log.Write("radial_closed", EventLog.Fields("stage", stage, "reason", reason));
        }

        internal void CellPopupOpened(string row, string column)
        {
            this.log.Write("cell_popup_opened", EventLog.Fields(
                "screen", this.Screen.Id, "row", row, "column", column));
        }

        /// <summary>Records a number cell of a grouped table by its row names and its two header names.</summary>
        internal void CellClicked(string[] row, string[] column)
        {
            this.log.Write("cell_clicked", EventLog.Fields(
                "screen", this.Screen.Id, "row", row, "column", column));
        }

        internal void OpenScreen(string screenId)
        {
            this.Screen = ScreenSpecs.Find(screenId);
            this.Filters = new FilterValues();
            this.Result = null;
            this.ResultFilters = null;
            this.log.Write("screen_opened", EventLog.Fields("screen", this.Screen.Id, "title", this.Screen.Title));
        }
    }
}
