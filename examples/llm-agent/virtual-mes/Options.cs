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
        internal const string ComboAnywhere = "anywhere";
        internal const string ComboArrow = "arrow";

        internal string Profile = Exposures.DefaultProfile;
        internal Dictionary<UiRegion, Exposure> Expose;
        internal int? DelayMs;
        internal int Seed = DefaultSeed;
        internal string LogPath;
        internal string[] Headless;
        internal bool ParseOnly;

        /// <summary>How long the small loading window stays up before the main window, or null for none.</summary>
        internal int? SplashMs;

        /// <summary>The loading window's process starts the main window as a second process and ends.</summary>
        internal bool Launcher;

        /// <summary>The launcher ends with no window when this exe's main window is already open.</summary>
        internal bool SingleInstance;

        /// <summary>The main window shows its menu only after this long, or at once when null.</summary>
        internal int? MenuDelayMs;

        /// <summary>
        /// This process is the main window a launcher started: it never reads VirtualMes.args and
        /// ignores the launch options, so it cannot start another launcher.
        /// </summary>
        internal bool Main;

        /// <summary>Every argument except the launch options, which the launcher hands to the main process.</summary>
        internal List<string> MainArgs = new List<string>();

        /// <summary>True when any start-up option is in use, which turns on the start-up events.</summary>
        internal bool UsesStartup
        {
            get { return this.SplashMs.HasValue || this.MenuDelayMs.HasValue || this.Main; }
        }

        /// <summary>
        /// Where a filter box opens its list: only on its arrow (the default, as in the real
        /// MES), or anywhere on the box (the behaviour before the arrow became the default).
        /// </summary>
        internal string ComboOpen = ComboArrow;

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
                int start = index;
                string flag = args[index];
                index++;
                bool launchOption = flag == "--splash-ms" || flag == "--launcher" || flag == "--single-instance";
                if (flag == "--parse-only")
                {
                    options.ParseOnly = true;
                }
                else if (flag == "--splash-ms")
                {
                    options.SplashMs = NonNegative(Value(args, ref index, flag), flag);
                }
                else if (flag == "--launcher")
                {
                    options.Launcher = true;
                }
                else if (flag == "--single-instance")
                {
                    options.SingleInstance = true;
                }
                else if (flag == "--menu-delay-ms")
                {
                    options.MenuDelayMs = NonNegative(Value(args, ref index, flag), flag);
                }
                else if (flag == "--main")
                {
                    options.Main = true;
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
                else if (flag == "--combo-open")
                {
                    options.ComboOpen = ComboOpenValue(Value(args, ref index, flag));
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
                if (!launchOption && flag != "--main")
                {
                    for (int taken = start; taken < index; taken++)
                    {
                        options.MainArgs.Add(args[taken]);
                    }
                }
            }

            if (options.Main)
            {
                options.SplashMs = null;
                options.Launcher = false;
                options.SingleInstance = false;
            }
            if (options.Launcher && !options.SplashMs.HasValue)
            {
                throw new OptionsException("--launcher needs --splash-ms.");
            }
            if (options.SingleInstance && !options.Launcher)
            {
                throw new OptionsException("--single-instance needs --launcher and --splash-ms.");
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
            root["combo_open"] = this.ComboOpen;
            root["splash_ms"] = this.SplashMs.HasValue ? (object)this.SplashMs.Value : null;
            root["launcher"] = this.Launcher;
            root["single_instance"] = this.SingleInstance;
            root["menu_delay_ms"] = this.MenuDelayMs.HasValue ? (object)this.MenuDelayMs.Value : null;
            root["main"] = this.Main;
            root["main_command_line"] = this.Launcher ? this.MainCommandLine() : null;
            return new JavaScriptSerializer().Serialize(root);
        }

        /// <summary>The arguments the launcher starts the main process with: --main, then every other option.</summary>
        internal string MainCommandLine()
        {
            List<string> quoted = new List<string>();
            quoted.Add("--main");
            foreach (string arg in this.MainArgs)
            {
                quoted.Add(Quote(arg));
            }
            return string.Join(" ", quoted.ToArray());
        }

        /// <summary>
        /// Quotes one argument so the Windows command-line splitting gives it back unchanged:
        /// backslashes count only before a double quote, where they are doubled.
        /// </summary>
        private static string Quote(string arg)
        {
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
            {
                return arg;
            }
            StringBuilder text = new StringBuilder("\"");
            int slashes = 0;
            foreach (char ch in arg)
            {
                if (ch == '\\')
                {
                    slashes++;
                    continue;
                }
                if (ch == '"')
                {
                    text.Append('\\', slashes * 2 + 1);
                }
                else
                {
                    text.Append('\\', slashes);
                }
                slashes = 0;
                text.Append(ch);
            }
            text.Append('\\', slashes * 2);
            text.Append('"');
            return text.ToString();
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

        private static string ComboOpenValue(string text)
        {
            if (text != ComboAnywhere && text != ComboArrow)
            {
                throw new OptionsException("--combo-open is anywhere or arrow, got: " + text);
            }
            return text;
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
                        "Unknown region: " + parts[0] + ". Use menu, filters, grid, icon, radial or arrow.");
                }
                if (!Exposures.TryParseExposure(parts[1], out exposure))
                {
                    throw new OptionsException("Unknown mode: " + parts[1] + ". Use ref, text, bare or pixel.");
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
