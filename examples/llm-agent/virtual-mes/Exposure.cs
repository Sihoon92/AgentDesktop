using System;
using System.Collections.Generic;

namespace VirtualMesApp
{
    /// <summary>
    /// A group of controls whose accessibility exposure is chosen together. Arrow is the arrow
    /// button at the right end of each filter box, chosen apart from the boxes themselves.
    /// </summary>
    internal enum UiRegion
    {
        Menu,
        Filters,
        Grid,
        Icon,
        Radial,
        Arrow
    }

    /// <summary>
    /// How a region appears in the accessibility tree. Ref: operable named
    /// elements. Text: named text with bounds and nothing operable. Bare:
    /// operable buttons with no name, told apart only by a numeric
    /// AutomationId and their place. IdOnly: images with no name and no
    /// pattern, so nothing gives them a ref; only a numeric AutomationId and
    /// their place tell them apart, and only a real click reaches them.
    /// NoAction: named buttons, so the CLI gives them a ref, that advertise no Invoke pattern;
    /// only a real click reaches them.
    /// Rows: the grid as the company MES shows it: the table holds a Data Panel, the panel one
    /// Row n per data row, and each row its cells, named "&lt;column header&gt; row&lt;k&gt;" with the
    /// shown text as their Value. No header cells, and no GridItem or TableItem pattern.
    /// Pixel: one nameless image.
    /// </summary>
    internal enum Exposure
    {
        Ref,
        Text,
        Bare,
        IdOnly,
        NoAction,
        Rows,
        Pixel
    }

    internal static class Exposures
    {
        internal static readonly UiRegion[] Regions =
        {
            UiRegion.Menu, UiRegion.Filters, UiRegion.Grid, UiRegion.Icon, UiRegion.Radial, UiRegion.Arrow
        };

        internal const string DefaultProfile = "mes-like";

        internal static string Name(UiRegion region)
        {
            return region.ToString().ToLowerInvariant();
        }

        internal static string Name(Exposure exposure)
        {
            return exposure.ToString().ToLowerInvariant();
        }

        internal static bool TryParseRegion(string text, out UiRegion region)
        {
            foreach (UiRegion candidate in Regions)
            {
                if (Name(candidate) == text)
                {
                    region = candidate;
                    return true;
                }
            }
            region = UiRegion.Menu;
            return false;
        }

        internal static bool TryParseExposure(string text, out Exposure exposure)
        {
            Exposure[] all =
            {
                Exposure.Ref, Exposure.Text, Exposure.Bare, Exposure.IdOnly, Exposure.NoAction, Exposure.Rows,
                Exposure.Pixel
            };
            foreach (Exposure candidate in all)
            {
                if (Name(candidate) == text)
                {
                    exposure = candidate;
                    return true;
                }
            }
            exposure = Exposure.Ref;
            return false;
        }

        /// <summary>
        /// The icon and the arrows carry no text, so neither can be exposed as text. Only the
        /// menu and the arrows can be bare, only the menu can be idonly or noaction, and only the
        /// grid can be rows.
        /// </summary>
        internal static bool IsAllowed(UiRegion region, Exposure exposure)
        {
            if (exposure == Exposure.Rows)
            {
                return region == UiRegion.Grid;
            }
            if (exposure == Exposure.IdOnly || exposure == Exposure.NoAction)
            {
                return region == UiRegion.Menu;
            }
            if (region == UiRegion.Arrow)
            {
                return exposure != Exposure.Text;
            }
            if (exposure == Exposure.Bare)
            {
                return region == UiRegion.Menu;
            }
            return !(region == UiRegion.Icon && exposure == Exposure.Text);
        }

        /// <summary>Returns the profile's exposure per region, or null for an unknown profile.</summary>
        internal static Dictionary<UiRegion, Exposure> Profile(string name)
        {
            if (name == "mes-like")
            {
                return Build(Exposure.Ref, Exposure.Text, Exposure.Text, Exposure.Pixel, Exposure.Pixel, Exposure.Bare);
            }
            if (name == "easy")
            {
                return Build(Exposure.Ref, Exposure.Ref, Exposure.Ref, Exposure.Ref, Exposure.Ref, Exposure.Ref);
            }
            if (name == "hard")
            {
                return Build(
                    Exposure.Text, Exposure.Pixel, Exposure.Pixel, Exposure.Pixel, Exposure.Pixel, Exposure.Pixel);
            }
            return null;
        }

        private static Dictionary<UiRegion, Exposure> Build(
            Exposure menu, Exposure filters, Exposure grid, Exposure icon, Exposure radial, Exposure arrow)
        {
            Dictionary<UiRegion, Exposure> map = new Dictionary<UiRegion, Exposure>();
            map[UiRegion.Menu] = menu;
            map[UiRegion.Filters] = filters;
            map[UiRegion.Grid] = grid;
            map[UiRegion.Icon] = icon;
            map[UiRegion.Radial] = radial;
            map[UiRegion.Arrow] = arrow;
            return map;
        }
    }
}
