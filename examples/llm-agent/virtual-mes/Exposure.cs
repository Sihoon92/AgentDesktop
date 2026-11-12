using System;
using System.Collections.Generic;

namespace VirtualMesApp
{
    /// <summary>A group of controls whose accessibility exposure is chosen together.</summary>
    internal enum UiRegion
    {
        Menu,
        Filters,
        Grid,
        Icon,
        Radial
    }

    /// <summary>
    /// How a region appears in the accessibility tree. Ref: operable named
    /// elements. Text: named text with bounds and nothing operable. Pixel:
    /// one nameless image.
    /// </summary>
    internal enum Exposure
    {
        Ref,
        Text,
        Pixel
    }

    internal static class Exposures
    {
        internal static readonly UiRegion[] Regions =
        {
            UiRegion.Menu, UiRegion.Filters, UiRegion.Grid, UiRegion.Icon, UiRegion.Radial
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
            foreach (Exposure candidate in new[] { Exposure.Ref, Exposure.Text, Exposure.Pixel })
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

        /// <summary>The icon carries no text, so it cannot be exposed as text.</summary>
        internal static bool IsAllowed(UiRegion region, Exposure exposure)
        {
            return !(region == UiRegion.Icon && exposure == Exposure.Text);
        }

        /// <summary>Returns the profile's exposure per region, or null for an unknown profile.</summary>
        internal static Dictionary<UiRegion, Exposure> Profile(string name)
        {
            if (name == "mes-like")
            {
                return Build(Exposure.Ref, Exposure.Text, Exposure.Text, Exposure.Pixel, Exposure.Pixel);
            }
            if (name == "easy")
            {
                return Build(Exposure.Ref, Exposure.Ref, Exposure.Ref, Exposure.Ref, Exposure.Ref);
            }
            if (name == "hard")
            {
                return Build(Exposure.Text, Exposure.Pixel, Exposure.Pixel, Exposure.Pixel, Exposure.Pixel);
            }
            return null;
        }

        private static Dictionary<UiRegion, Exposure> Build(
            Exposure menu, Exposure filters, Exposure grid, Exposure icon, Exposure radial)
        {
            Dictionary<UiRegion, Exposure> map = new Dictionary<UiRegion, Exposure>();
            map[UiRegion.Menu] = menu;
            map[UiRegion.Filters] = filters;
            map[UiRegion.Grid] = grid;
            map[UiRegion.Icon] = icon;
            map[UiRegion.Radial] = radial;
            return map;
        }
    }
}
