using System;
using System.Globalization;

namespace VirtualMesApp
{
    /// <summary>
    /// The fixed AutomationIds of the menu. A bare or idonly menu item's id is a number that
    /// depends only on its place: the category's place in the menu bar, or the screen's place in
    /// the list. It is the same on every screen and every run. The areas holding the items have
    /// fixed numbers too, unless --menu-container-id off removes them.
    /// </summary>
    internal static class MenuIds
    {
        internal const int FirstBarId = 8001;
        internal const int FirstListId = 8101;
        internal const string BarArea = "8000";
        internal const string ListArea = "8100";
        internal const string OtherArea = "8200";

        /// <summary>True for the exposures whose menu items carry numeric ids.</summary>
        internal static bool Numeric(Exposure exposure)
        {
            return exposure == Exposure.Bare || exposure == Exposure.IdOnly;
        }

        /// <summary>
        /// The AutomationId a menu bar item shows: "menu-N" for the named exposures, else
        /// FirstBarId plus its place. With --menu-dup-id same the second item shows the first
        /// item's id.
        /// </summary>
        internal static string BarItem(int place, Exposure exposure, string dupId)
        {
            int shown = place == 1 && dupId == Options.MenuDupSame ? 0 : place;
            if (!Numeric(exposure))
            {
                return "menu-" + shown;
            }
            return Number(FirstBarId + shown);
        }

        /// <summary>
        /// The AutomationId a menu list entry shows: "screen-ID" for the named exposures, else
        /// FirstListId plus its row.
        /// </summary>
        internal static string ListItem(int row, string screenId, Exposure exposure)
        {
            if (!Numeric(exposure))
            {
                return "screen-" + screenId;
            }
            return Number(FirstListId + row);
        }

        /// <summary>
        /// The AutomationId of an area that holds menu items: its number, or empty when the
        /// option removes it.
        /// </summary>
        internal static string Area(string number, Options options)
        {
            return options.MenuContainerIds ? number : string.Empty;
        }

        private static string Number(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
