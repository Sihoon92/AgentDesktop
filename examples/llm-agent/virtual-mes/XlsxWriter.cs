using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Security;
using System.Text;

namespace VirtualMesApp
{
    /// <summary>Writes a table as a one-sheet .xlsx workbook, every cell an inline string.</summary>
    internal static class XlsxWriter
    {
        private const string ContentTypes =
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            + "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
            + "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
            + "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
            + "<Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>"
            + "<Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>"
            + "</Types>";

        private const string RootRels =
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            + "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/>"
            + "</Relationships>";

        private const string Workbook =
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            + "<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" "
            + "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
            + "<sheets><sheet name=\"Sheet1\" sheetId=\"1\" r:id=\"rId1\"/></sheets>"
            + "</workbook>";

        private const string WorkbookRels =
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            + "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/>"
            + "</Relationships>";

        /// <summary>
        /// Writes beside the target first and moves into place, so a failure
        /// leaves the target untouched. Throws IOException or
        /// UnauthorizedAccessException when the path cannot be written.
        /// </summary>
        internal static void Write(string path, QueryResult result)
        {
            string full = Path.GetFullPath(path);
            string temp = full + ".writing-" + Guid.NewGuid().ToString("N");
            try
            {
                using (FileStream stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write))
                using (ZipArchive archive = new ZipArchive(stream, ZipArchiveMode.Create))
                {
                    Add(archive, "[Content_Types].xml", ContentTypes);
                    Add(archive, "_rels/.rels", RootRels);
                    Add(archive, "xl/workbook.xml", Workbook);
                    Add(archive, "xl/_rels/workbook.xml.rels", WorkbookRels);
                    Add(archive, "xl/worksheets/sheet1.xml", Sheet(result));
                }
                if (File.Exists(full))
                {
                    File.Delete(full);
                }
                File.Move(temp, full);
            }
            finally
            {
                if (File.Exists(temp))
                {
                    File.Delete(temp);
                }
            }
        }

        private static void Add(ZipArchive archive, string name, string content)
        {
            ZipArchiveEntry entry = archive.CreateEntry(name);
            using (Stream stream = entry.Open())
            {
                byte[] bytes = new UTF8Encoding(false).GetBytes(content);
                stream.Write(bytes, 0, bytes.Length);
            }
        }

        private static string Sheet(QueryResult result)
        {
            StringBuilder xml = new StringBuilder();
            xml.Append("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>");
            xml.Append("<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>");
            List<string[]> rows = new List<string[]>();
            rows.Add(result.Header);
            rows.AddRange(result.Rows);
            for (int row = 0; row < rows.Count; row++)
            {
                string rowNumber = (row + 1).ToString(CultureInfo.InvariantCulture);
                xml.Append("<row r=\"").Append(rowNumber).Append("\">");
                for (int column = 0; column < rows[row].Length; column++)
                {
                    xml.Append("<c r=\"").Append(ColumnName(column)).Append(rowNumber);
                    xml.Append("\" t=\"inlineStr\"><is><t xml:space=\"preserve\">");
                    xml.Append(SecurityElement.Escape(rows[row][column]));
                    xml.Append("</t></is></c>");
                }
                xml.Append("</row>");
            }
            xml.Append("</sheetData></worksheet>");
            return xml.ToString();
        }

        private static string ColumnName(int index)
        {
            string name = string.Empty;
            for (int rest = index; rest >= 0; rest = rest / 26 - 1)
            {
                name = (char)('A' + rest % 26) + name;
            }
            return name;
        }
    }
}
