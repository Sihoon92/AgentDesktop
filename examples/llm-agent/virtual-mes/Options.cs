using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace VirtualMesApp
{
    /// <summary>Invalid command-line input. The process exits with code 2.</summary>
    internal sealed class OptionsException : Exception
    {
        internal OptionsException(string message) : base(message)
        {
        }
    }

    internal sealed class Options
    {
        internal const string ArgsFileName = "VirtualMes.args";
        internal const int DefaultSeed = 20261112;

        internal string Profile = Exposures.DefaultProfile;
        internal Dictionary<UiRegion, Exposure> Expose;
        internal int? DelayMs;
        internal int Seed = DefaultSeed;
        internal string LogPath;
        internal string[] Headless;
        internal bool ParseOnly;

        /// <summary>
        /// Parses the command line. With no arguments at all, the first line of
        /// VirtualMes.args beside the exe is used instead, so a launcher that
        /// cannot pass arguments still selects a profile and a log.
        /// </summary>
        internal static Options Parse(string[] args, string exeDirectory)
        {
            if (args.Length == 0)
            {
                string file = Path.Combine(exeDirectory, ArgsFileName);
                if (File.Exists(file))
                {
                    args = SplitLine(FirstLine(File.ReadAllText(file, Encoding.UTF8)));
                }
            }

            Options options = new Options();
            List<KeyValuePair<UiRegion, Exposure>> overrides = new List<KeyValuePair<UiRegion, Exposure>>();
            int index = 0;
            while (index < args.Length)
            {
                string flag = args[index];
                index++;
                if (flag == "--parse-only")
                {
                    options.ParseOnly = true;
                }
                else if (flag == "--profile")
                {
                    options.Profile = Value(args, ref index, flag);
                }
                else if (flag == "--expose")
                {
                    ParseExpose(Value(args, ref index, flag), overrides);
                }
                else if (flag == "--delay-ms")
                {
                    options.DelayMs = NonNegative(Value(args, ref index, flag), flag);
                }
                else if (flag == "--seed")
                {
                    options.Seed = Integer(Value(args, ref index, flag), flag);
                }
                else if (flag == "--log")
                {
                    options.LogPath = Value(args, ref index, flag);
                }
                else if (flag == "--export-headless")
                {
                    options.Headless = new[]
                    {
                        Value(args, ref index, flag), Value(args, ref index, flag), Value(args, ref index, flag)
                    };
                }
                else
                {
                    throw new OptionsException("Unknown option: " + flag);
                }
            }

            options.Expose = Exposures.Profile(options.Profile);
            if (options.Expose == null)
            {
                throw new OptionsException(
                    "Unknown profile: " + options.Profile + ". Use mes-like, easy or hard.");
            }
            foreach (KeyValuePair<UiRegion, Exposure> pair in overrides)
            {
                options.Expose[pair.Key] = pair.Value;
            }
            return options;
        }

        internal string ToJson()
        {
            Dictionary<string, object> expose = new Dictionary<string, object>();
            foreach (UiRegion region in Exposures.Regions)
            {
                expose[Exposures.Name(region)] = Exposures.Name(this.Expose[region]);
            }
            Dictionary<string, object> root = new Dictionary<string, object>();
            root["profile"] = this.Profile;
            root["expose"] = expose;
            root["seed"] = this.Seed;
            root["delay_ms"] = this.DelayMs.HasValue ? (object)this.DelayMs.Value : null;
            root["log"] = this.LogPath;
            return new JavaScriptSerializer().Serialize(root);
        }

        private static string Value(string[] args, ref int index, string flag)
        {
            if (index >= args.Length)
            {
                throw new OptionsException("Missing value for " + flag);
            }
            string value = args[index];
            index++;
            return value;
        }

        private static int Integer(string text, string flag)
        {
            int value;
            if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
            {
                throw new OptionsException(flag + " needs a whole number, got: " + text);
            }
            return value;
        }

        private static int NonNegative(string text, string flag)
        {
            int value = Integer(text, flag);
            if (value < 0)
            {
                throw new OptionsException(flag + " cannot be negative, got: " + text);
            }
            return value;
        }

        private static void ParseExpose(string list, List<KeyValuePair<UiRegion, Exposure>> overrides)
        {
            foreach (string entry in list.Split(','))
            {
                string[] parts = entry.Split('=');
                UiRegion region;
                Exposure exposure;
                if (parts.Length != 2)
                {
                    throw new OptionsException("--expose entries look like region=mode, got: " + entry);
                }
                if (!Exposures.TryParseRegion(parts[0], out region))
                {
                    throw new OptionsException(
                        "Unknown region: " + parts[0] + ". Use menu, filters, grid, icon or radial.");
                }
                if (!Exposures.TryParseExposure(parts[1], out exposure))
                {
                    throw new OptionsException("Unknown mode: " + parts[1] + ". Use ref, text or pixel.");
                }
                if (!Exposures.IsAllowed(region, exposure))
                {
                    throw new OptionsException(
                        "UiRegion " + parts[0] + " does not support mode " + parts[1] + ".");
                }
                overrides.Add(new KeyValuePair<UiRegion, Exposure>(region, exposure));
            }
        }

        private static string FirstLine(string text)
        {
            string trimmed = text.TrimStart('﻿');
            int end = trimmed.IndexOfAny(new[] { '\r', '\n' });
            return end < 0 ? trimmed : trimmed.Substring(0, end);
        }

        /// <summary>Splits on spaces, keeping double-quoted runs together and dropping the quotes.</summary>
        private static string[] SplitLine(string line)
        {
            List<string> parts = new List<string>();
            StringBuilder current = new StringBuilder();
            bool quoted = false;
            bool started = false;
            foreach (char ch in line)
            {
                if (ch == '"')
                {
                    quoted = !quoted;
                    started = true;
                }
                else if (ch == ' ' && !quoted)
                {
                    if (started)
                    {
                        parts.Add(current.ToString());
                        current.Length = 0;
                        started = false;
                    }
                }
                else
                {
                    current.Append(ch);
                    started = true;
                }
            }
            if (started)
            {
                parts.Add(current.ToString());
            }
            return parts.ToArray();
        }
    }
}
