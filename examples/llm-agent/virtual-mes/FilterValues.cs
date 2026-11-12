using System;
using System.Collections.Generic;
using System.Globalization;
using System.Web.Script.Serialization;

namespace VirtualMesApp
{
    /// <summary>The filter choices a query runs with.</summary>
    internal sealed class FilterValues
    {
        internal const string All = "전체";
        internal const string PeriodYesterday = "전일";
        internal const string PeriodDayBefore = "전전일";
        internal const string PeriodLastWeek = "전주";
        internal const string PeriodCustom = "기간";
        internal const string DateFormat = "yyyy-MM-dd";

        internal static readonly string[] Periods =
        {
            PeriodYesterday, PeriodDayBefore, PeriodLastWeek, PeriodCustom
        };

        internal List<string> Shop = new List<string>(MesData.ShopCodes);
        internal string Process = All;
        internal string Product = All;
        internal string Equipment = All;
        internal string Period = PeriodYesterday;
        internal string From;
        internal string To;
        internal string Row;

        internal FilterValues Clone()
        {
            FilterValues copy = (FilterValues)this.MemberwiseClone();
            copy.Shop = new List<string>(this.Shop);
            return copy;
        }

        /// <summary>
        /// Resolves the period to an inclusive date range. The app's "today"
        /// is fixed, so the same choice always gives the same dates.
        /// </summary>
        internal bool TryResolvePeriod(out DateTime from, out DateTime to, out string error)
        {
            error = null;
            from = MesData.Today.AddDays(-1);
            to = from;
            if (this.Period == PeriodYesterday)
            {
                return true;
            }
            if (this.Period == PeriodDayBefore)
            {
                from = MesData.Today.AddDays(-2);
                to = from;
                return true;
            }
            if (this.Period == PeriodLastWeek)
            {
                int sinceMonday = ((int)MesData.Today.DayOfWeek + 6) % 7;
                from = MesData.Today.AddDays(-sinceMonday - 7);
                to = from.AddDays(6);
                return true;
            }
            if (!TryParseDate(this.From, out from) || !TryParseDate(this.To, out to))
            {
                error = "기간의 시작일과 종료일을 YYYY-MM-DD 형식으로 입력하세요.";
                return false;
            }
            if (from > to)
            {
                error = "시작일이 종료일보다 늦습니다.";
                return false;
            }
            return true;
        }

        internal static bool TryParseDate(string text, out DateTime date)
        {
            return DateTime.TryParseExact(
                text ?? string.Empty, DateFormat, CultureInfo.InvariantCulture, DateTimeStyles.None, out date);
        }

        /// <summary>The values as they appear in the event log and in a headless request.</summary>
        internal Dictionary<string, object> ToDictionary()
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["shop"] = this.Shop.ToArray();
            map["process"] = this.Process;
            map["product"] = this.Product;
            map["equipment"] = this.Equipment;
            map["period"] = this.Period;
            if (this.Period == PeriodCustom)
            {
                map["from"] = this.From;
                map["to"] = this.To;
            }
            return map;
        }

        internal static FilterValues FromJson(string json)
        {
            Dictionary<string, object> map;
            try
            {
                map = new JavaScriptSerializer().DeserializeObject(json) as Dictionary<string, object>;
            }
            catch (ArgumentException)
            {
                map = null;
            }
            catch (InvalidOperationException)
            {
                map = null;
            }
            if (map == null)
            {
                throw new OptionsException("The filter argument must be a JSON object, got: " + json);
            }

            FilterValues filters = new FilterValues();
            foreach (KeyValuePair<string, object> pair in map)
            {
                if (pair.Key == "shop")
                {
                    filters.Shop = ShopList(pair.Value);
                }
                else if (pair.Key == "process")
                {
                    filters.Process = OneOf(pair, MesData.ProcessOptions());
                }
                else if (pair.Key == "product")
                {
                    filters.Product = OneOf(pair, MesData.ProductOptions());
                }
                else if (pair.Key == "equipment")
                {
                    filters.Equipment = OneOf(pair, MesData.EquipmentOptions());
                }
                else if (pair.Key == "period")
                {
                    filters.Period = OneOf(pair, Periods);
                }
                else if (pair.Key == "from")
                {
                    filters.From = Text(pair);
                }
                else if (pair.Key == "to")
                {
                    filters.To = Text(pair);
                }
                else if (pair.Key == "row")
                {
                    filters.Row = OneOf(pair, MesData.ShopNames);
                }
                else
                {
                    throw new OptionsException("Unknown filter: " + pair.Key);
                }
            }

            DateTime from;
            DateTime to;
            string error;
            if (!filters.TryResolvePeriod(out from, out to, out error))
            {
                throw new OptionsException(error);
            }
            return filters;
        }

        private static string Text(KeyValuePair<string, object> pair)
        {
            string text = pair.Value as string;
            if (text == null)
            {
                throw new OptionsException("Filter " + pair.Key + " must be a string.");
            }
            return text;
        }

        private static string OneOf(KeyValuePair<string, object> pair, string[] allowed)
        {
            string text = Text(pair);
            if (Array.IndexOf(allowed, text) < 0)
            {
                throw new OptionsException(
                    "Filter " + pair.Key + " has no value " + text + ". Use one of: " + string.Join(", ", allowed));
            }
            return text;
        }

        private static List<string> ShopList(object value)
        {
            object[] items = value as object[];
            if (items == null)
            {
                throw new OptionsException("Filter shop must be a list of shop codes.");
            }
            List<string> codes = new List<string>();
            foreach (string known in MesData.ShopCodes)
            {
                if (Array.IndexOf(items, known) >= 0)
                {
                    codes.Add(known);
                }
            }
            foreach (object item in items)
            {
                if (Array.IndexOf(MesData.ShopCodes, item as string) < 0)
                {
                    throw new OptionsException(
                        "Unknown shop code: " + item + ". Use " + string.Join(", ", MesData.ShopCodes));
                }
            }
            return codes;
        }
    }
}
