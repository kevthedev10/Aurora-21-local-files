using System;
using System.Drawing;
using System.Collections.Generic;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using System.Xml;
using Media = System.Windows.Media;

namespace Project21.LocalFutLauncher
{
    internal static class LauncherTheme
    {
        internal static readonly Color Background = Color.FromArgb(21, 23, 27);
        internal static readonly Color Surface = Color.FromArgb(29, 32, 38);
        internal static readonly Color Border = Color.FromArgb(49, 54, 63);
        internal static readonly Color Text = Color.FromArgb(232, 235, 241);
        internal static readonly Color Muted = Color.FromArgb(159, 167, 181);
        internal static readonly Color Accent = Color.FromArgb(96, 141, 232);

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr handle, int attribute, ref int value, int size);

        internal static void DarkTitleBar(Form form)
        {
            try
            {
                int enabled = 1;
                if (DwmSetWindowAttribute(form.Handle, 20, ref enabled, 4) != 0)
                    DwmSetWindowAttribute(form.Handle, 19, ref enabled, 4);
            }
            catch (DllNotFoundException) { }
            catch (EntryPointNotFoundException) { }
        }

        // Only our embedded, path-based 24x24 Material SVGs are accepted.
        internal static GraphicsPath LoadIcon(string name)
        {
            using (Stream stream = typeof(LauncherTheme).Assembly.GetManifestResourceStream("Material." + name + ".svg"))
            {
                if (stream == null) throw new InvalidDataException("Missing embedded Material icon: " + name);
                XmlDocument document = new XmlDocument();
                document.XmlResolver = null;
                using (XmlReader reader = XmlReader.Create(stream, new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null })) document.Load(reader);
                GraphicsPath result = new GraphicsPath(FillMode.Winding);
                foreach (XmlElement element in document.GetElementsByTagName("path"))
                {
                    if (element.GetAttribute("fill") == "none") continue;
                    Media.Geometry geometry = (Media.Geometry)new Media.GeometryConverter().ConvertFromInvariantString("F1 " + element.GetAttribute("d"));
                    foreach (Media.PathFigure figure in geometry.GetFlattenedPathGeometry(0.002, Media.ToleranceType.Absolute).Figures)
                    {
                        List<PointF> points = new List<PointF>();
                        points.Add(new PointF((float)figure.StartPoint.X, (float)figure.StartPoint.Y));
                        foreach (Media.PathSegment segment in figure.Segments)
                        {
                            Media.PolyLineSegment poly = segment as Media.PolyLineSegment;
                            Media.LineSegment line = segment as Media.LineSegment;
                            if (poly != null) foreach (System.Windows.Point point in poly.Points) points.Add(new PointF((float)point.X, (float)point.Y));
                            else if (line != null) points.Add(new PointF((float)line.Point.X, (float)line.Point.Y));
                        }
                        if (points.Count > 1) { result.StartFigure(); result.AddLines(points.ToArray()); if (figure.IsClosed) result.CloseFigure(); }
                    }
                }
                return result;
            }
        }

        internal static void DrawIcon(Graphics graphics, GraphicsPath path, RectangleF bounds, Color color)
        {
            GraphicsState state = graphics.Save();
            graphics.TranslateTransform(bounds.X, bounds.Y);
            graphics.ScaleTransform(bounds.Width / 24F, bounds.Height / 24F);
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (Brush brush = new SolidBrush(color)) graphics.FillPath(brush, path);
            graphics.Restore(state);
        }

        internal static void StyleButton(Button button, string icon, bool primary)
        {
            button.BackColor = primary ? Color.FromArgb(65, 102, 201) : Surface;
            button.ForeColor = Text;
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderSize = primary ? 0 : 1;
            button.FlatAppearance.BorderColor = Border;
            button.FlatAppearance.MouseOverBackColor = primary ? Color.FromArgb(124, 161, 238) : Color.FromArgb(40, 44, 52);
            button.Font = new Font("Segoe UI", 10.5F);
            button.Cursor = Cursors.Hand;
            button.UseVisualStyleBackColor = false;
            if (icon != null)
            {
                MaterialButton material = button as MaterialButton;
                if (material != null) material.SetIcon(icon);
            }
        }
    }

    internal sealed class MaterialButton : Button
    {
        private GraphicsPath icon;
        private bool hovering;
        private bool pressed;
        internal MaterialButton() { DoubleBuffered = true; }
        internal void SetIcon(string name) { if (icon != null) icon.Dispose(); icon = LauncherTheme.LoadIcon(name); }
        protected override void OnMouseEnter(EventArgs args) { hovering = true; Invalidate(); base.OnMouseEnter(args); }
        protected override void OnMouseLeave(EventArgs args) { hovering = false; pressed = false; Invalidate(); base.OnMouseLeave(args); }
        protected override void OnMouseDown(MouseEventArgs args) { pressed = true; Invalidate(); base.OnMouseDown(args); }
        protected override void OnMouseUp(MouseEventArgs args) { pressed = false; Invalidate(); base.OnMouseUp(args); }
        protected override void OnPaintBackground(PaintEventArgs args) { args.Graphics.Clear(Parent == null ? LauncherTheme.Background : Parent.BackColor); }
        protected override void OnPaint(PaintEventArgs args)
        {
            float scale = args.Graphics.DpiX / 96F;
            args.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            Color fill = !Enabled ? LauncherTheme.Surface : hovering && !pressed ? FlatAppearance.MouseOverBackColor : BackColor;
            using (GraphicsPath outline = RoundedRectangle(new RectangleF(0.5F, 0.5F, Width - 1, Height - 1), 5 * scale))
            {
                using (Brush brush = new SolidBrush(fill)) args.Graphics.FillPath(brush, outline);
                if (FlatAppearance.BorderSize > 0) using (Pen pen = new Pen(LauncherTheme.Border)) args.Graphics.DrawPath(pen, outline);
            }
            Color color = Enabled ? ForeColor : LauncherTheme.Muted;
            int textWidth = TextRenderer.MeasureText(Text, Font, Size.Empty, TextFormatFlags.NoPadding).Width;
            float iconWidth = icon == null ? 0 : 20 * scale;
            float gap = icon == null ? 0 : 10 * scale;
            float left = (Width - textWidth - iconWidth - gap) / 2F;
            if (icon != null) LauncherTheme.DrawIcon(args.Graphics, icon, new RectangleF(left, (Height - iconWidth) / 2, iconWidth, iconWidth), color);
            TextRenderer.DrawText(args.Graphics, Text, Font, new Rectangle((int)(left + iconWidth + gap), 0, textWidth + 2, Height), color, TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding);
            if (Focused && ShowFocusCues) ControlPaint.DrawFocusRectangle(args.Graphics, new Rectangle(4, 4, Width - 8, Height - 8), color, fill);
        }
        internal static GraphicsPath RoundedRectangle(RectangleF bounds, float radius) {
            float diameter = radius * 2;
            GraphicsPath path = new GraphicsPath();
            path.AddArc(bounds.Left, bounds.Top, diameter, diameter, 180, 90);
            path.AddArc(bounds.Right - diameter, bounds.Top, diameter, diameter, 270, 90);
            path.AddArc(bounds.Right - diameter, bounds.Bottom - diameter, diameter, diameter, 0, 90);
            path.AddArc(bounds.Left, bounds.Bottom - diameter, diameter, diameter, 90, 90);
            path.CloseFigure(); return path;
        }
        protected override void Dispose(bool disposing) { if (disposing && icon != null) { icon.Dispose(); icon = null; } base.Dispose(disposing); }
    }

    internal sealed class MaterialGlyph : Control
    {
        private readonly GraphicsPath icon;
        internal MaterialGlyph(string name) { icon = LauncherTheme.LoadIcon(name); SetStyle(ControlStyles.SupportsTransparentBackColor, true); BackColor = Color.Transparent; ForeColor = LauncherTheme.Muted; }
        protected override void OnPaint(PaintEventArgs args) { base.OnPaint(args); LauncherTheme.DrawIcon(args.Graphics, icon, ClientRectangle, ForeColor); }
        protected override void Dispose(bool disposing) { if (disposing) icon.Dispose(); base.Dispose(disposing); }
    }

    internal sealed class SurfacePanel : Panel
    {
        internal SurfacePanel()
        {
            BackColor = LauncherTheme.Surface;
            DoubleBuffered = true;
            ResizeRedraw = true;
        }
        protected override void OnPaint(PaintEventArgs args)
        {
            base.OnPaint(args);
            args.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (GraphicsPath outline = MaterialButton.RoundedRectangle(new RectangleF(0.5F, 0.5F, Width - 1, Height - 1), 5 * args.Graphics.DpiX / 96F))
            using (Brush brush = new SolidBrush(BackColor))
            using (Pen pen = new Pen(LauncherTheme.Border)) { args.Graphics.FillPath(brush, outline); args.Graphics.DrawPath(pen, outline); }
        }
        protected override void OnPaintBackground(PaintEventArgs args) { args.Graphics.Clear(Parent == null ? LauncherTheme.Background : Parent.BackColor); }
    }

    internal sealed class ProgressTrack : Control
    {
        private int value;
        internal int Minimum { get { return 0; } }
        internal int Maximum { get { return 100; } }
        internal int Value { get { return value; } set { this.value = Math.Max(0, Math.Min(100, value)); Invalidate(); } }
        internal ProgressTrack() { DoubleBuffered = true; ResizeRedraw = true; BackColor = LauncherTheme.Border; ForeColor = LauncherTheme.Accent; }
        protected override void OnPaint(PaintEventArgs args)
        {
            base.OnPaint(args);
            using (Brush brush = new SolidBrush(ForeColor)) args.Graphics.FillRectangle(brush, 0, 0, Width * value / 100, Height);
        }
    }
}
