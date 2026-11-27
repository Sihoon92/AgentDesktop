using System;
using System.Globalization;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// The notices the company MES opens over its main window at start-up: owned modal forms,
    /// one at a time, each opening when the one before it closes. The main window takes no input
    /// until the last one closes. Titles and text share no word with the menu.
    /// </summary>
    internal static class StartupNotices
    {
        internal const string TitlePrefix = "공지 ";
        internal const string BodyPrefix = "시스템 안내 사항 ";

        internal static string Title(int index)
        {
            return TitlePrefix + index.ToString(CultureInfo.InvariantCulture);
        }

        internal static void Show(Form owner, Session session, int count)
        {
            for (int index = 1; index <= count; index++)
            {
                if (owner.IsDisposed)
                {
                    return;
                }
                int shown = index;
                string body = BodyPrefix + index.ToString(CultureInfo.InvariantCulture) + "/"
                    + count.ToString(CultureInfo.InvariantCulture);
                using (MessageForm notice = new MessageForm(Title(index), body, MessageBoxIcon.None))
                {
                    notice.Name = "startup-notice";
                    notice.Shown += delegate { session.NoticeShown(shown, count); };
                    notice.ShowDialog(owner);
                }
                session.NoticeClosed(shown);
            }
        }
    }
}
