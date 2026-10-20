using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace Project21.LocalFutLauncher
{
    public sealed class LauncherSettingsStore
    {
        public string Root { get; private set; }
        public string SettingsPath { get { return Path.Combine(Root, "launcher-settings.json"); } }

        public LauncherSettingsStore(string root)
        {
            if (String.IsNullOrWhiteSpace(root) || !Path.IsPathRooted(root))
                throw new ArgumentException("The user settings folder must be an absolute path.");
            Root = Path.GetFullPath(root);
        }

        public static string DefaultRoot()
        {
            string configured = Environment.GetEnvironmentVariable("LOCALFUT21_USER_CONFIG_ROOT");
            if (!String.IsNullOrWhiteSpace(configured)) return configured;
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Project21", "LocalFUT");
        }

        public static Dictionary<string, object> Defaults()
        {
            return new Dictionary<string, object> {
                { "sourcePath", "" }, { "gamePath", "" }, { "gameReadyDelaySeconds", 5 },
                { "lsxMode", "auto" }, { "launchViaEAApp", true },
                { "eaAppOfferId", "195853" }, { "eaAppLauncherPath", "" }
            };
        }

        public Dictionary<string, object> Load()
        {
            Directory.CreateDirectory(Root);
            if (!File.Exists(SettingsPath))
            {
                try
                {
                    using (FileStream file = new FileStream(SettingsPath, FileMode.CreateNew, FileAccess.Write))
                    using (StreamWriter writer = new StreamWriter(file, new UTF8Encoding(false)))
                        writer.Write(new JavaScriptSerializer().Serialize(Defaults()));
                }
                catch (IOException) { if (!File.Exists(SettingsPath)) throw; }
            }
            Dictionary<string, object> saved;
            try
            {
                saved = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(SettingsPath)) as Dictionary<string, object>;
                if (saved == null) throw new FormatException("Expected a JSON object.");
            }
            catch (Exception exception)
            {
                throw new InvalidDataException("Cannot read " + SettingsPath + ". Correct the JSON or rename the file to reset settings. The file was not overwritten.", exception);
            }
            Dictionary<string, object> result = Defaults();
            foreach (KeyValuePair<string, object> entry in saved) result[entry.Key] = entry.Value;
            return result;
        }

        public void Save(Dictionary<string, object> settings)
        {
            Directory.CreateDirectory(Root);
            string temporary = Path.Combine(Root, "launcher-settings-" + Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                File.WriteAllText(temporary, new JavaScriptSerializer().Serialize(settings), new UTF8Encoding(false));
                if (File.Exists(SettingsPath)) File.Replace(temporary, SettingsPath, null);
                else File.Move(temporary, SettingsPath);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }

        public string PrepareForSetup()
        {
            try { Load(); return null; }
            catch (InvalidDataException exception)
            {
                // Only recover malformed JSON, never an access/IO failure.
                if (!(exception.InnerException is ArgumentException) && !(exception.InnerException is FormatException) && !(exception.InnerException is InvalidOperationException)) throw;
                string backup = Path.Combine(Root, "launcher-settings.invalid-" + Guid.NewGuid().ToString("N") + ".json");
                File.Move(SettingsPath, backup);
                Load();
                return backup;
            }
        }

        public bool NeedsGameSelection()
        {
            object value;
            return !Load().TryGetValue("gamePath", out value) || !ValidGamePath(value as string);
        }

        public void InitializeRuntimeConfig(string examplePath)
        {
            Directory.CreateDirectory(Root);
            string path = Path.Combine(Root, "config.json");
            if (File.Exists(path)) return;
            string template = File.Exists(examplePath) ? File.ReadAllText(examplePath) : "{}";
            if (!(new JavaScriptSerializer().DeserializeObject(template) is Dictionary<string, object>))
                throw new InvalidDataException("The shared runtime config example must contain a JSON object.");
            try
            {
                using (FileStream file = new FileStream(path, FileMode.CreateNew, FileAccess.Write))
                using (StreamWriter writer = new StreamWriter(file, new UTF8Encoding(false))) writer.Write(template);
            }
            catch (IOException) { if (!File.Exists(path)) throw; }
        }

        public static string TextValue(Dictionary<string, object> settings, string key)
        {
            object value;
            if (!settings.TryGetValue(key, out value) || value == null) return "";
            if (!(value is string)) throw new InvalidDataException(key + " must be text.");
            return (string)value;
        }

        public static bool ValidGamePath(string path)
        {
            try { return Path.IsPathRooted(path) && File.Exists(path) && String.Equals(Path.GetFileName(path), "FIFA21.exe", StringComparison.OrdinalIgnoreCase); }
            catch (ArgumentException) { return false; }
        }

    }

    internal sealed class LauncherSettingsDialog : Form
    {
        private readonly LauncherSettingsStore store;
        private readonly Dictionary<string, object> settings;
        private readonly TextBox game = new TextBox();
        private readonly TextBox ea = new TextBox();
        private readonly ComboBox mode = new ComboBox();
        private readonly CheckBox viaEa = new CheckBox();

        internal LauncherSettingsDialog(LauncherSettingsStore store)
        {
            this.store = store;
            settings = store.Load();
            Text = "LocalFUT settings — this Windows user only";
            ClientSize = new Size(720, 330);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterParent;
            MaximizeBox = MinimizeBox = false;
            Font = new Font("Segoe UI", 9F);
            BackColor = LauncherTheme.Background;
            ForeColor = LauncherTheme.Text;
            AutoScaleMode = AutoScaleMode.Dpi;
            ShowIcon = false;
            Controls.Add(new Label { Text = "Choose your installed FIFA21.exe. Settings stay on this PC, outside the repository.", AutoSize = true, Location = new Point(18, 16) });
            AddPathRow("FIFA 21 executable", game, 48, true);
            AddPathRow("EA App launcher (blank = automatic)", ea, 112, false);
            game.Text = settings["gamePath"] as string ?? "";
            ea.Text = LauncherSettingsStore.TextValue(settings, "eaAppLauncherPath");
            mode.SetBounds(480, 180, 210, 28);
            mode.DropDownStyle = ComboBoxStyle.DropDownList;
            mode.Items.AddRange(new object[] { "auto", "ea-app", "replacement" });
            mode.SelectedItem = LauncherSettingsStore.TextValue(settings, "lsxMode");
            if (mode.SelectedIndex < 0) throw new InvalidDataException("lsxMode must be auto, ea-app, or replacement.");
            Controls.Add(mode);
            Controls.Add(new Label { Text = "LSX mode", Location = new Point(410, 184), AutoSize = true });
            viaEa.Text = "Launch through EA App (recommended)";
            viaEa.SetBounds(18, 180, 345, 28);
            if (!(settings["launchViaEAApp"] is bool)) throw new InvalidDataException("launchViaEAApp must be true or false.");
            viaEa.Checked = (bool)settings["launchViaEAApp"];
            Controls.Add(viaEa);
            Controls.Add(new Label { Text = "Settings folder: " + store.Root + "\r\nAdministrator permission is requested only when starting or stopping services.", Location = new Point(18, 218), Size = new Size(680, 50) });
            Button folder = new MaterialButton { Text = "Settings folder", Location = new Point(18, 284), Size = new Size(165, 30) };
            LauncherTheme.StyleButton(folder, "folder_open", false);
            folder.Click += delegate { System.Diagnostics.Process.Start("explorer.exe", "\"" + store.Root.TrimEnd('\\') + "\""); };
            Controls.Add(folder);
            Button save = new Button { Text = "Save", Location = new Point(500, 284), Size = new Size(90, 30) };
            LauncherTheme.StyleButton(save, null, true);
            save.Click += SaveClick;
            Controls.Add(save);
            Button cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, Location = new Point(602, 284), Size = new Size(90, 30) };
            LauncherTheme.StyleButton(cancel, null, false);
            Controls.Add(cancel);
            AcceptButton = save;
            CancelButton = cancel;
            mode.BackColor = LauncherTheme.Surface;
            mode.ForeColor = LauncherTheme.Text;
            mode.FlatStyle = FlatStyle.Flat;
        }

        protected override void OnHandleCreated(EventArgs args) { base.OnHandleCreated(args); LauncherTheme.DarkTitleBar(this); }

        private void AddPathRow(string label, TextBox box, int top, bool isGame)
        {
            Controls.Add(new Label { Text = label, AutoSize = true, Location = new Point(18, top) });
            box.SetBounds(18, top + 23, 566, 26);
            box.BackColor = LauncherTheme.Surface;
            box.ForeColor = LauncherTheme.Text;
            box.BorderStyle = BorderStyle.FixedSingle;
            Controls.Add(box);
            Button browse = new Button { Text = "Browse…", Location = new Point(596, top + 21), Size = new Size(96, 28) };
            LauncherTheme.StyleButton(browse, null, false);
            browse.Click += delegate {
                using (OpenFileDialog picker = new OpenFileDialog())
                {
                    picker.Title = isGame ? "Select your FIFA21.exe" : "Select EALauncher.exe";
                    picker.Filter = isGame ? "FIFA 21 (FIFA21.exe)|FIFA21.exe" : "EA launcher (EALauncher.exe)|EALauncher.exe";
                    picker.CheckFileExists = true;
                    picker.RestoreDirectory = true;
                    if (File.Exists(box.Text)) picker.FileName = box.Text;
                    if (picker.ShowDialog(this) == DialogResult.OK) box.Text = picker.FileName;
                }
            };
            Controls.Add(browse);
        }

        private void SaveClick(object sender, EventArgs args)
        {
            try
            {
                string gamePath = game.Text.Trim().Trim('"');
                string eaPath = ea.Text.Trim().Trim('"');
                if (!LauncherSettingsStore.ValidGamePath(gamePath)) throw new InvalidDataException("Select an existing FIFA21.exe before saving.");
                if (eaPath.Length > 0 && (!Path.IsPathRooted(eaPath) || !File.Exists(eaPath) || !String.Equals(Path.GetFileName(eaPath), "EALauncher.exe", StringComparison.OrdinalIgnoreCase)))
                    throw new InvalidDataException("Select an existing EALauncher.exe, or leave the EA App path blank.");
                settings["gamePath"] = Path.GetFullPath(gamePath);
                settings["eaAppLauncherPath"] = eaPath.Length == 0 ? "" : Path.GetFullPath(eaPath);
                settings["lsxMode"] = (string)mode.SelectedItem;
                settings["launchViaEAApp"] = viaEa.Checked;
                store.Save(settings);
                DialogResult = DialogResult.OK;
                Close();
            }
            catch (Exception exception) { MessageBox.Show(this, exception.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning); }
        }
    }
}
