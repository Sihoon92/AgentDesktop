using System;

namespace VirtualMesApp
{
    /// <summary>What one screen consists of. The screens differ only by this data.</summary>
    internal sealed class ScreenSpec
    {
        internal const string KindList = "list";
        internal const string KindMatrix = "matrix";
        internal const string KindEmpty = "empty";
        internal const string KindGrouped = "grouped";

        internal const string FilterShop = "shop";
        internal const string FilterProcess = "process";
        internal const string FilterProduct = "product";
        internal const string FilterEquipment = "equipment";
        internal const string FilterPeriod = "period";

        internal string Id;
        internal string Title;
        internal string Kind;
        internal bool HasIcon;
        internal string[] Filters = new string[0];
        internal bool[] ShopBoxEnabled = { true, true, true };

        internal ScreenSpec(string id, string title, string kind)
        {
            this.Id = id;
            this.Title = title;
            this.Kind = kind;
        }
    }

    internal static class ScreenSpecs
    {
        internal const string RearProcess = "rear-process";
        internal const string EquipmentStatus = "equipment-status";

        /// <summary>The screens in the order the 생산운영 list shows them.</summary>
        internal static readonly ScreenSpec[] All = Build();

        internal static ScreenSpec Find(string id)
        {
            foreach (ScreenSpec spec in All)
            {
                if (spec.Id == id)
                {
                    return spec;
                }
            }
            throw new ArgumentException("Unknown screen: " + id);
        }

        private static ScreenSpec[] Build()
        {
            ScreenSpec front = new ScreenSpec(MesData.FrontProcess, "전공정 생산현황", ScreenSpec.KindList);
            front.HasIcon = true;
            front.Filters = new[]
            {
                ScreenSpec.FilterShop, ScreenSpec.FilterProcess, ScreenSpec.FilterProduct, ScreenSpec.FilterPeriod
            };

            ScreenSpec rear = new ScreenSpec(RearProcess, "후공정 생산현황", ScreenSpec.KindEmpty);

            ScreenSpec defect = new ScreenSpec(MesData.DefectByProcess, "공정별 불량현황", ScreenSpec.KindMatrix);
            defect.Filters = new[] { ScreenSpec.FilterProduct, ScreenSpec.FilterPeriod };

            ScreenSpec roller = new ScreenSpec(MesData.RollerCheck, "권취 Roller 점검일시", ScreenSpec.KindList);
            roller.Filters = new[]
            {
                ScreenSpec.FilterShop, ScreenSpec.FilterEquipment, ScreenSpec.FilterPeriod
            };
            roller.ShopBoxEnabled = new[] { false, false, true };

            ScreenSpec equipment = new ScreenSpec(EquipmentStatus, "설비 가동현황", ScreenSpec.KindEmpty);

            ScreenSpec summary = new ScreenSpec(MesData.ProcessSummary, "공정별 생산집계", ScreenSpec.KindGrouped);
            summary.HasIcon = true;
            summary.Filters = new[] { ScreenSpec.FilterShop, ScreenSpec.FilterPeriod };

            return new[] { front, rear, defect, roller, equipment, summary };
        }
    }
}
