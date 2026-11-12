using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace VirtualMesApp
{
    /// <summary>
    /// Appends one JSON object per line. Only a test harness reads it: nothing
    /// in the app's windows shows its content or its path.
    /// </summary>
    internal sealed class EventLog
    {
        private readonly string path;
        private readonly object gate = new object();
        private readonly JavaScriptSerializer serializer = new JavaScriptSerializer();
        private int sequence;

        private EventLog(string path)
        {
            this.path = path;
        }

        /// <summary>Returns a log that writes to the path, or one that writes nothing when the path is null.</summary>
        internal static EventLog Open(string pathOrNull)
        {
            if (pathOrNull == null)
            {
                return new EventLog(null);
            }
            try
            {
                string full = Path.GetFullPath(pathOrNull);
                Directory.CreateDirectory(Path.GetDirectoryName(full));
                using (new FileStream(full, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
                {
                }
                return new EventLog(full);
            }
            catch (Exception error)
            {
                if (error is IOException || error is UnauthorizedAccessException
                    || error is ArgumentException || error is NotSupportedException)
                {
                    throw new OptionsException("Cannot write the event log " + pathOrNull + ": " + error.Message);
                }
                throw;
            }
        }

        /// <summary>Builds the field map from alternating names and values.</summary>
        internal static Dictionary<string, object> Fields(params object[] pairs)
        {
            Dictionary<string, object> fields = new Dictionary<string, object>();
            for (int index = 0; index + 1 < pairs.Length; index += 2)
            {
                fields[(string)pairs[index]] = pairs[index + 1];
            }
            return fields;
        }

        internal void Write(string eventName, IDictionary<string, object> fields)
        {
            if (this.path == null)
            {
                return;
            }
            lock (this.gate)
            {
                this.sequence++;
                Dictionary<string, object> line = new Dictionary<string, object>();
                line["seq"] = this.sequence;
                line["time"] = DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz", CultureInfo.InvariantCulture);
                line["event"] = eventName;
                if (fields != null)
                {
                    foreach (KeyValuePair<string, object> pair in fields)
                    {
                        line[pair.Key] = pair.Value;
                    }
                }
                byte[] bytes = new UTF8Encoding(false).GetBytes(this.serializer.Serialize(line) + "\n");
                using (FileStream stream = new FileStream(
                    this.path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
            }
        }
    }
}
