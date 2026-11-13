using System;
using System.Collections.Generic;
using System.Globalization;

namespace VirtualMesApp
{
    /// <summary>A table as a screen shows it: column names, then rows of display text.</summary>
    internal sealed class QueryResult
    {
        internal string[] Header;
        internal List<string[]> Rows = new List<string[]>();

        /// <summary>
        /// The upper header row of a two-row header, or null for a one-row header. Each name
        /// covers `GroupSpans` of the columns in `Header`, left to right.
        /// </summary>
        internal string[] GroupNames;
        internal int[] GroupSpans;

        /// <summary>How many leading columns name the row rather than hold numbers.</summary>
        internal int LabelColumns = 1;

        /// <summary>The upper header name over a column, or the column's own name for a one-row header.</summary>
        internal string GroupOf(int column)
        {
            if (this.GroupNames == null)
            {
                return this.Header[column];
            }
            int start = 0;
            for (int group = 0; group < this.GroupNames.Length; group++)
            {
                if (column < start + this.GroupSpans[group])
                {
                    return this.GroupNames[group];
                }
                start += this.GroupSpans[group];
            }
            return this.Header[column];
        }
    }

    /// <summary>
    /// The app's fixed data. Every value is computed from the date, shop,
    /// process and product, so the same filters always give the same rows.
    /// </summary>
    internal static class MesData
    {
        internal const string FrontProcess = "front-process";
        internal const string RollerCheck = "roller-check";
        internal const string DefectByProcess = "defect-by-process";
        internal const string DefectDetail = "defect-detail";
        internal const string ProcessSummary = "process-summary";
        internal const string Subtotal = "소계";

        internal static readonly DateTime Today = new DateTime(2026, 11, 12);
        internal static readonly DateTime FirstDay = new DateTime(2026, 11, 1);
        internal static readonly DateTime LastDay = new DateTime(2026, 11, 11);

        internal static readonly string[] ShopCodes = { "WA0", "WA1", "WA2", "WA3" };
        internal static readonly string[] ShopNames = { "믹싱", "극판", "조립", "화성" };
        internal const string ShopNamePrefix = "#울산 ";

        internal static readonly string[] Products = { "모델 A", "모델 B" };
        internal static readonly string[] Equipment = { "권취 1호", "권취 2호", "권취 3호" };

        private static readonly string[][] Processes =
        {
            new[] { "계량", "혼합" },
            new[] { "코팅", "압연" },
            new[] { "권취", "용접" },
            new[] { "충전", "선별" }
        };

        private static readonly int[] DetailShares = { 43, 30, 17, 10 };

        private sealed class FrontRow
        {
            internal DateTime Date;
            internal int Shop;
            internal string Process;
            internal string Product;
            internal int Quantity;
            internal int Defects;
        }

        internal static string[] ProcessOptions()
        {
            List<string> options = new List<string>();
            options.Add(FilterValues.All);
            foreach (string[] pair in Processes)
            {
                options.AddRange(pair);
            }
            return options.ToArray();
        }

        internal static string[] ProductOptions()
        {
            return WithAll(Products);
        }

        internal static string[] EquipmentOptions()
        {
            return WithAll(Equipment);
        }

        internal static QueryResult Query(string screenId, FilterValues filters)
        {
            DateTime from;
            DateTime to;
            string error;
            if (!filters.TryResolvePeriod(out from, out to, out error))
            {
                throw new OptionsException(error);
            }
            if (screenId == FrontProcess)
            {
                return Front(filters, from, to);
            }
            if (screenId == RollerCheck)
            {
                return Roller(filters, from, to);
            }
            if (screenId == DefectByProcess)
            {
                return DefectSummary(filters, from, to);
            }
            if (screenId == DefectDetail)
            {
                return Detail(filters, from, to);
            }
            if (screenId == ProcessSummary)
            {
                return Summary(filters, from, to);
            }
            throw new OptionsException(
                "Unknown screen: " + screenId + ". Use " + FrontProcess + ", " + RollerCheck + ", "
                + DefectByProcess + ", " + DefectDetail + " or " + ProcessSummary + ".");
        }

        private sealed class SummaryTotals
        {
            internal long Plan;
            internal long Work;
            internal long Defects;
            internal long Loss;
            internal long Escaped;

            internal void Add(SummaryTotals other)
            {
                this.Plan += other.Plan;
                this.Work += other.Work;
                this.Defects += other.Defects;
                this.Loss += other.Loss;
                this.Escaped += other.Escaped;
            }

            internal long Good
            {
                get { return this.Work - this.Defects - this.Loss - this.Escaped; }
            }
        }

        /// <summary>
        /// Per shop: a subtotal row (그룹공정 소계, 공정 blank) first, then one subtotal row per
        /// group process (공정 소계). Every count is a sum over the front-process rows, so a
        /// shop's subtotal is exactly the sum of its group rows.
        /// </summary>
        private static QueryResult Summary(FilterValues filters, DateTime from, DateTime to)
        {
            QueryResult result = new QueryResult();
            result.Header = new[]
            {
                "SHOP", "그룹공정", "공정", "계획", "실적", "달성률", "작업수", "양품수", "양품율",
                "수율(Loss제외)", "불량수", "불량율(PPM)", "LOSS수", "LOSS율(PPM)", "이탈수", "이탈율(PPM)"
            };
            result.GroupNames = new[] { "SHOP", "공정", "계획", "생산", "불량" };
            result.GroupSpans = new[] { 1, 2, 3, 4, 6 };
            result.LabelColumns = 3;
            for (int shop = 0; shop < ShopCodes.Length; shop++)
            {
                if (!filters.Shop.Contains(ShopCodes[shop]))
                {
                    continue;
                }
                SummaryTotals shopTotals = new SummaryTotals();
                List<string[]> groupRows = new List<string[]>();
                foreach (string process in Processes[shop])
                {
                    SummaryTotals totals = GroupTotals(shop, process, from, to);
                    shopTotals.Add(totals);
                    groupRows.Add(SummaryRow(ShopNames[shop], process, Subtotal, totals));
                }
                result.Rows.Add(SummaryRow(ShopNames[shop], Subtotal, string.Empty, shopTotals));
                result.Rows.AddRange(groupRows);
            }
            return result;
        }

        /// <summary>The front-process rows behind one summary row: a whole shop when `group` is 소계.</summary>
        internal static QueryResult SummaryDetail(FilterValues filters, string shopName, string group)
        {
            DateTime from;
            DateTime to;
            string error;
            if (!filters.TryResolvePeriod(out from, out to, out error))
            {
                throw new OptionsException(error);
            }
            List<string> only = new List<string>();
            only.Add(ShopCodes[Array.IndexOf(ShopNames, shopName)]);
            string process = group == Subtotal ? FilterValues.All : group;
            QueryResult result = new QueryResult();
            result.Header = new[] { "일자", "공정", "제품", "작업수", "불량수" };
            foreach (FrontRow row in FrontRows(only, process, FilterValues.All, from, to))
            {
                result.Rows.Add(new[] { Day(row.Date), row.Process, row.Product, Count(row.Quantity), Count(row.Defects) });
            }
            return result;
        }

        private static SummaryTotals GroupTotals(int shop, string process, DateTime from, DateTime to)
        {
            SummaryTotals totals = new SummaryTotals();
            List<string> only = new List<string>();
            only.Add(ShopCodes[shop]);
            int processIndex = shop * 2 + Array.IndexOf(Processes[shop], process);
            foreach (FrontRow row in FrontRows(only, process, FilterValues.All, from, to))
            {
                totals.Plan += (row.Quantity * 21 / 20 + 99) / 100 * 100;
                totals.Work += row.Quantity;
                totals.Defects += row.Defects;
                totals.Loss += row.Quantity * 3 / 1000 + (row.Date.Day + processIndex) % 5;
                totals.Escaped += row.Quantity / 2500 + (3 * row.Date.Day + processIndex) % 3;
            }
            return totals;
        }

        private static string[] SummaryRow(string shop, string group, string process, SummaryTotals totals)
        {
            long done = totals.Work - totals.Escaped;
            return new[]
            {
                shop, group, process,
                Count(totals.Plan), Count(done), Percent(done, totals.Plan, "0.0"),
                Count(totals.Work), Count(totals.Good), Percent(totals.Good, totals.Work, "0.00"),
                Percent(totals.Good, totals.Work - totals.Loss, "0.00"),
                Count(totals.Defects), Ppm(totals.Defects, totals.Work),
                Count(totals.Loss), Ppm(totals.Loss, totals.Work),
                Count(totals.Escaped), Ppm(totals.Escaped, totals.Work)
            };
        }

        private static string Percent(long part, long whole, string format)
        {
            double rate = whole <= 0 ? 0.0 : part * 100.0 / whole;
            return rate.ToString(format, CultureInfo.InvariantCulture) + "%";
        }

        private static string Ppm(long part, long whole)
        {
            long ppm = whole <= 0 ? 0 : (long)Math.Round(part * 1000000.0 / whole, MidpointRounding.AwayFromZero);
            return ppm.ToString("N0", CultureInfo.InvariantCulture);
        }

        private static QueryResult Front(FilterValues filters, DateTime from, DateTime to)
        {
            QueryResult result = new QueryResult();
            result.Header = new[] { "일자", "Shop", "공정", "제품", "생산수", "불량수", "불량률" };
            foreach (FrontRow row in FrontRows(filters.Shop, filters.Process, filters.Product, from, to))
            {
                result.Rows.Add(new[]
                {
                    Day(row.Date), ShopNames[row.Shop], row.Process, row.Product,
                    Count(row.Quantity), Count(row.Defects), Rate(row.Defects, row.Quantity)
                });
            }
            return result;
        }

        private static QueryResult Roller(FilterValues filters, DateTime from, DateTime to)
        {
            QueryResult result = new QueryResult();
            result.Header = new[] { "점검일시", "Shop", "설비", "Roller", "점검자", "결과" };
            foreach (DateTime date in Days(from, to))
            {
                for (int shop = 0; shop < ShopCodes.Length; shop++)
                {
                    if (!filters.Shop.Contains(ShopCodes[shop]))
                    {
                        continue;
                    }
                    for (int unit = 0; unit < Equipment.Length; unit++)
                    {
                        if (filters.Equipment != FilterValues.All && filters.Equipment != Equipment[unit])
                        {
                            continue;
                        }
                        result.Rows.Add(new[]
                        {
                            Day(date) + " 08:" + Number(10 + 5 * unit),
                            ShopCodes[shop],
                            Equipment[unit],
                            "R-0" + Number(unit + 1),
                            "점검자 " + Number(1 + (date.Day + shop) % 3),
                            (date.Day + shop + unit) % 7 == 0 ? "교체" : "정상"
                        });
                    }
                }
            }
            return result;
        }

        private static QueryResult DefectSummary(FilterValues filters, DateTime from, DateTime to)
        {
            QueryResult result = new QueryResult();
            result.Header = new[] { "공정", "생산수", "불량수", "불량률" };
            for (int shop = 0; shop < ShopNames.Length; shop++)
            {
                int quantity;
                int defects;
                Totals(shop, filters.Product, from, to, out quantity, out defects);
                result.Rows.Add(new[] { ShopNames[shop], Count(quantity), Count(defects), Rate(defects, quantity) });
            }
            return result;
        }

        private static QueryResult Detail(FilterValues filters, DateTime from, DateTime to)
        {
            int shop = Array.IndexOf(ShopNames, filters.Row);
            if (shop < 0)
            {
                throw new OptionsException(
                    "Screen " + DefectDetail + " needs the filter row, one of: " + string.Join(", ", ShopNames));
            }
            int quantity;
            int total;
            Totals(shop, filters.Product, from, to, out quantity, out total);

            int[] counts = new int[DetailShares.Length];
            int assigned = 0;
            for (int index = 0; index < DetailShares.Length; index++)
            {
                counts[index] = total * DetailShares[index] / 100;
                assigned += counts[index];
            }
            counts[0] += total - assigned;

            QueryResult result = new QueryResult();
            result.Header = new[] { "불량 유형", "수량", "비율" };
            for (int index = 0; index < counts.Length; index++)
            {
                double share = total == 0 ? 0.0 : counts[index] * 100.0 / total;
                result.Rows.Add(new[]
                {
                    "유형 " + Number(index + 1),
                    Count(counts[index]),
                    share.ToString("0.0", CultureInfo.InvariantCulture) + "%"
                });
            }
            return result;
        }

        private static void Totals(
            int shop, string product, DateTime from, DateTime to, out int quantity, out int defects)
        {
            quantity = 0;
            defects = 0;
            List<string> only = new List<string>();
            only.Add(ShopCodes[shop]);
            foreach (FrontRow row in FrontRows(only, FilterValues.All, product, from, to))
            {
                quantity += row.Quantity;
                defects += row.Defects;
            }
        }

        private static IEnumerable<FrontRow> FrontRows(
            List<string> shops, string process, string product, DateTime from, DateTime to)
        {
            foreach (DateTime date in Days(from, to))
            {
                for (int shop = 0; shop < ShopCodes.Length; shop++)
                {
                    if (!shops.Contains(ShopCodes[shop]))
                    {
                        continue;
                    }
                    for (int step = 0; step < Processes[shop].Length; step++)
                    {
                        string processName = Processes[shop][step];
                        if (process != FilterValues.All && process != processName)
                        {
                            continue;
                        }
                        int processIndex = shop * 2 + step;
                        for (int model = 0; model < Products.Length; model++)
                        {
                            if (product != FilterValues.All && product != Products[model])
                            {
                                continue;
                            }
                            FrontRow row = new FrontRow();
                            row.Date = date;
                            row.Shop = shop;
                            row.Process = processName;
                            row.Product = Products[model];
                            row.Quantity = 10000 + 137 * date.Day + 911 * shop + 53 * processIndex - 2040 * model;
                            row.Defects = 20 + (7 * date.Day + 13 * processIndex + 5 * model + 3 * shop) % 40;
                            yield return row;
                        }
                    }
                }
            }
        }

        private static IEnumerable<DateTime> Days(DateTime from, DateTime to)
        {
            DateTime first = from < FirstDay ? FirstDay : from;
            DateTime last = to > LastDay ? LastDay : to;
            for (DateTime date = first; date <= last; date = date.AddDays(1))
            {
                yield return date;
            }
        }

        private static string[] WithAll(string[] values)
        {
            List<string> options = new List<string>();
            options.Add(FilterValues.All);
            options.AddRange(values);
            return options.ToArray();
        }

        private static string Day(DateTime date)
        {
            return date.ToString(FilterValues.DateFormat, CultureInfo.InvariantCulture);
        }

        private static string Number(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }

        private static string Count(long value)
        {
            return value.ToString("N0", CultureInfo.InvariantCulture);
        }

        private static string Rate(int defects, int quantity)
        {
            double rate = quantity == 0 ? 0.0 : defects * 100.0 / quantity;
            return rate.ToString("0.00", CultureInfo.InvariantCulture) + "%";
        }
    }
}
