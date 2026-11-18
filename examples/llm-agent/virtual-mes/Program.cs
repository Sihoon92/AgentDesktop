using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace VirtualMesApp
{
    internal static class Program
    {
        internal const int ExitOk = 0;
        internal const int ExitFailed = 1;
        internal const int ExitBadInput = 2;

        [STAThread]
        private static int Main(string[] args)
        {
            Options options;
            try
            {
                options = Options.Parse(args, AppDomain.CurrentDomain.BaseDirectory);
            }
            catch (OptionsException error)
            {
                WriteLine(Console.OpenStandardError(), error.Message);
                return ExitBadInput;
            }

            if (options.ParseOnly)
            {
                WriteLine(Console.OpenStandardOutput(), options.ToJson());
                return ExitOk;
            }
            if (options.Headless != null)
            {
                return ExportHeadless(options.Headless[0], options.Headless[1], options.Headless[2]);
            }

            EventLog log;
            try
            {
                log = EventLog.Open(options.LogPath);
            }
            catch (OptionsException error)
            {
                WriteLine(Console.OpenStandardError(), error.Message);
                return ExitBadInput;
            }

            if (options.Main)
            {
                log.ContinueNumbering();
            }

            Ui.Initialize();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (options.SingleInstance && MainInstance.IsOpen())
            {
                log.Write("launcher_skipped", EventLog.Fields(
                    "pid", Process.GetCurrentProcess().Id, "reason", "main_running"));
                return ExitOk;
            }
            if (options.SplashMs.HasValue)
            {
                Application.Run(new SplashForm(log, options.SplashMs.Value));
            }
            if (options.Launcher)
            {
                return StartMain(options);
            }
            using (Mutex held = MainInstance.Hold())
            {
                Application.Run(new MainForm(new Session(options, log)));
                GC.KeepAlive(held);
            }
            return ExitOk;
        }

        /// <summary>Starts this exe again as the main window, with every option but the launch ones.</summary>
        private static int StartMain(Options options)
        {
            ProcessStartInfo start = new ProcessStartInfo(Application.ExecutablePath, options.MainCommandLine());
            start.UseShellExecute = false;
            try
            {
                using (Process.Start(start))
                {
                }
                return ExitOk;
            }
            catch (Win32Exception error)
            {
                WriteLine(Console.OpenStandardError(), "Could not start the main window: " + error.Message);
                return ExitFailed;
            }
        }

        /// <summary>Writes one screen's result for the given filters without creating a window.</summary>
        private static int ExportHeadless(string screenId, string filterJson, string outputPath)
        {
            try
            {
                QueryResult result = MesData.Query(screenId, FilterValues.FromJson(filterJson));
                XlsxWriter.Write(outputPath, result);
                return ExitOk;
            }
            catch (OptionsException error)
            {
                WriteLine(Console.OpenStandardError(), error.Message);
                return ExitBadInput;
            }
            catch (IOException error)
            {
                WriteLine(Console.OpenStandardError(), "Could not write " + outputPath + ": " + error.Message);
                return ExitFailed;
            }
            catch (UnauthorizedAccessException error)
            {
                WriteLine(Console.OpenStandardError(), "Could not write " + outputPath + ": " + error.Message);
                return ExitFailed;
            }
        }

        /// <summary>
        /// Writes UTF-8 straight to the handle. A windowed exe has no console,
        /// so the console encoding cannot be set and would otherwise be the
        /// ANSI code page.
        /// </summary>
        internal static void WriteLine(Stream stream, string text)
        {
            byte[] bytes = new UTF8Encoding(false).GetBytes(text + Environment.NewLine);
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush();
        }
    }
}
