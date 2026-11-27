using System;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Windows.Forms;

namespace VirtualMesApp
{
    /// <summary>The steps from "export this table" to a file on disk.</summary>
    internal static class ExportFlow
    {
        internal const string RouteIcon = "icon";
        internal const string RouteRadial = "radial";
        internal const string SourcePanel = "panel";
        internal const string SourceCellPopup = "cell_popup";
        internal const string NoDataMessage = "조회된 데이터가 없습니다";

        private const string ChoiceSave = "save";
        private const string ChoiceOpen = "open";
        private const string ChoiceCancel = "cancel";

        /// <summary>The icon route: save, open or cancel, then the save dialog.</summary>
        internal static void FromIcon(Form owner, Session session, string defaultName)
        {
            QueryResult result = session.Result;
            if (result == null)
            {
                RejectNoData(owner, session);
                return;
            }
            string choice;
            using (ChoiceDialog dialog = new ChoiceDialog())
            {
                DialogResult answer = dialog.ShowDialog(owner);
                choice = answer == DialogResult.Yes ? ChoiceSave
                    : answer == DialogResult.No ? ChoiceOpen : ChoiceCancel;
            }
            session.ExportChoice(RouteIcon, choice);
            if (choice == ChoiceSave)
            {
                SaveAs(owner, session, result, SourcePanel, defaultName);
            }
            else if (choice == ChoiceOpen)
            {
                string folder = Path.Combine(Path.GetTempPath(), "VirtualMes");
                string name = session.Screen.Id + "-"
                    + DateTime.Now.Ticks.ToString(CultureInfo.InvariantCulture) + ".xlsx";
                Write(owner, session, Path.Combine(folder, name), result, SourcePanel);
            }
        }

        internal static void RejectNoData(Form owner, Session session)
        {
            session.ExportRejected("no_data");
            Alerts.Show(owner, session.Options, NoDataMessage, "엑셀 저장", MessageBoxIcon.Information);
        }

        /// <summary>Asks for a path with the standard save dialog and writes the table there.</summary>
        internal static void SaveAs(
            Form owner, Session session, QueryResult result, string source, string defaultName)
        {
            string path;
            using (SaveFileDialog dialog = new SaveFileDialog())
            {
                dialog.Title = "다른 이름으로 저장";
                dialog.Filter = "Excel (*.xlsx)|*.xlsx";
                dialog.DefaultExt = "xlsx";
                dialog.AddExtension = true;
                dialog.OverwritePrompt = true;
                dialog.FileName = defaultName;
                if (dialog.ShowDialog(owner) != DialogResult.OK)
                {
                    return;
                }
                path = dialog.FileName;
            }
            Write(owner, session, path, result, source);
        }

        private static void Write(Form owner, Session session, string path, QueryResult result, string source)
        {
            string failure = null;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path)));
                XlsxWriter.Write(path, result);
            }
            catch (IOException error)
            {
                failure = error.Message;
            }
            catch (UnauthorizedAccessException error)
            {
                failure = error.Message;
            }
            if (failure == null)
            {
                session.ExportSaved(path, source, result.Rows.Count);
                return;
            }
            session.ExportFailed(path, failure);
            Alerts.Show(
                owner, session.Options, "파일을 저장하지 못했습니다." + Environment.NewLine + failure, "엑셀 저장",
                MessageBoxIcon.Error);
        }

        /// <summary>Draws the small Excel icon: a green square with a white X.</summary>
        internal static void PaintIcon(Graphics graphics, Rectangle bounds)
        {
            int size = Math.Min(bounds.Width, bounds.Height) - Ui.Px(8);
            Rectangle box = new Rectangle(
                bounds.X + (bounds.Width - size) / 2, bounds.Y + (bounds.Height - size) / 2, size, size);
            using (SolidBrush fill = new SolidBrush(Ui.ExcelGreen))
            using (Pen mark = new Pen(Color.White, Math.Max(2f, Ui.Scale * 1.8f)))
            {
                graphics.FillRectangle(fill, box);
                int inset = size / 4;
                graphics.DrawLine(mark, box.Left + inset, box.Top + inset, box.Right - inset, box.Bottom - inset);
                graphics.DrawLine(mark, box.Right - inset, box.Top + inset, box.Left + inset, box.Bottom - inset);
            }
        }

        private sealed class ChoiceDialog : Form
        {
            internal ChoiceDialog()
            {
                this.Text = "파일 다운로드";
                this.AutoScaleMode = AutoScaleMode.None;
                this.Font = Ui.Font;
                this.FormBorderStyle = FormBorderStyle.FixedDialog;
                this.MinimizeBox = false;
                this.MaximizeBox = false;
                this.ShowInTaskbar = false;
                this.StartPosition = FormStartPosition.CenterParent;
                this.ClientSize = new Size(Ui.Px(340), Ui.Px(110));

                Label question = new Label();
                question.Text = "이 파일을 저장하거나 여시겠습니까?";
                question.SetBounds(Ui.Px(16), Ui.Px(16), Ui.Px(308), Ui.Px(24));
                this.Controls.Add(question);

                Button save = this.Choice("choice-save", "저장", DialogResult.Yes, 0);
                this.Choice("choice-open", "열기", DialogResult.No, 1);
                Button cancel = this.Choice("choice-cancel", "취소", DialogResult.Cancel, 2);
                this.AcceptButton = save;
                this.CancelButton = cancel;
            }

            private Button Choice(string name, string text, DialogResult result, int slot)
            {
                Button button = new Button();
                button.Name = name;
                button.Text = text;
                button.DialogResult = result;
                button.SetBounds(Ui.Px(100) + slot * Ui.Px(78), Ui.Px(66), Ui.Px(72), Ui.Px(28));
                this.Controls.Add(button);
                return button;
            }
        }
    }
}
