using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// Marks that this exe's main window is open with a named mutex. The name carries a hash of
    /// the exe's full path, so a copy in another folder is a different system and is not blocked.
    /// </summary>
    internal static class MainInstance
    {
        private const string Prefix = "Local\\VirtualMes-main-";

        internal static string Name
        {
            get
            {
                string path = Path.GetFullPath(Application.ExecutablePath).ToUpperInvariant();
                using (SHA256 sha = SHA256.Create())
                {
                    byte[] hash = sha.ComputeHash(Encoding.UTF8.GetBytes(path));
                    StringBuilder hex = new StringBuilder(Prefix);
                    for (int index = 0; index < 8; index++)
                    {
                        hex.Append(hash[index].ToString("x2"));
                    }
                    return hex.ToString();
                }
            }
        }

        /// <summary>Holds the mark for as long as the returned mutex is not disposed.</summary>
        internal static Mutex Hold()
        {
            return new Mutex(false, Name);
        }

        internal static bool IsOpen()
        {
            Mutex existing;
            try
            {
                if (!Mutex.TryOpenExisting(Name, out existing))
                {
                    return false;
                }
            }
            catch (UnauthorizedAccessException)
            {
                return true;
            }
            existing.Dispose();
            return true;
        }
    }
}
