using System;
using System.Drawing;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>
    /// A message window the app builds itself: a title, one or more lines of text, an optional
    /// system icon and one 확인 button. Shown modal and owned, it stands in for a Win32 message
    /// box (#32770) the way the company MES's own dialogs do: its window class is the app's.
    /// </summary>
    internal sealed class MessageForm : Form
    {
        internal const string OkText = "확인";

        internal MessageForm(string title, string text, MessageBoxIcon icon)
        {
            this.Text = title;
            this.AutoScaleMode = AutoScaleMode.None;
            this.Font = Ui.Font;
            this.BackColor = Ui.PanelBack;
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MinimizeBox = false;
            this.MaximizeBox = false;
            this.ShowInTaskbar = false;
            this.StartPosition = FormStartPosition.CenterParent;

            Icon shown = IconOf(icon);
            int left = Ui.Px(16);
            if (shown != null)
            {
                PictureBox picture = new PictureBox();
                picture.Image = shown.ToBitmap();
                picture.SizeMode = PictureBoxSizeMode.CenterImage;
                picture.SetBounds(Ui.Px(16), Ui.Px(16), Ui.Px(36), Ui.Px(36));
                this.Controls.Add(picture);
                left = Ui.Px(60);
            }

            Size measured = TextRenderer.MeasureText(text, Ui.Font);
            Label message = new Label();
            message.Text = text;
            message.ForeColor = Ui.Text;
            message.AutoSize = false;
            message.SetBounds(left, Ui.Px(22), measured.Width + Ui.Px(8), measured.Height + Ui.Px(4));
            this.Controls.Add(message);

            int width = Math.Max(Ui.Px(280), message.Right + Ui.Px(20));
            int buttonTop = Math.Max(Ui.Px(60), message.Bottom + Ui.Px(18));
            Button ok = new Button();
            ok.Name = "message-ok";
            ok.Text = OkText;
            ok.DialogResult = DialogResult.OK;
            ok.SetBounds(width - Ui.Px(92), buttonTop, Ui.Px(76), Ui.Px(28));
            this.Controls.Add(ok);
            this.AcceptButton = ok;
            this.CancelButton = ok;
            this.ClientSize = new Size(width, ok.Bottom + Ui.Px(14));
        }

        private static Icon IconOf(MessageBoxIcon icon)
        {
            if (icon == MessageBoxIcon.Error)
            {
                return SystemIcons.Error;
            }
            if (icon == MessageBoxIcon.Warning)
            {
                return SystemIcons.Warning;
            }
            if (icon == MessageBoxIcon.Information)
            {
                return SystemIcons.Information;
            }
            return null;
        }
    }

    /// <summary>
    /// The app's alert windows. With --dialogs messagebox (the default) an alert is a Win32
    /// message box; with --dialogs form it is an owned modal MessageForm with the same title,
    /// text and button. The save dialog is not an alert and stays the standard one.
    /// </summary>
    internal static class Alerts
    {
        internal static void Show(IWin32Window owner, Options options, string text, string title, MessageBoxIcon icon)
        {
            if (options.Dialogs == Options.DialogsForm)
            {
                using (MessageForm form = new MessageForm(title, text, icon))
                {
                    form.ShowDialog(owner);
                }
                return;
            }
            MessageBox.Show(owner, text, title, MessageBoxButtons.OK, icon);
        }
    }
}
