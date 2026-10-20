using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace Project21.LocalFutLauncher
{
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            bool smokeTest = args.Length == 1 && String.Equals(args[0], "--smoke-test", StringComparison.OrdinalIgnoreCase);
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try
            {
                LauncherForm form = new LauncherForm(smokeTest);
                if (smokeTest)
                    form.Shown += delegate { form.BeginInvoke((MethodInvoker)delegate { form.Close(); }); };
                Application.Run(form);
            }
            catch (Exception exception)
            {
                MessageBox.Show(exception.Message, "FIFA 21 LocalFUT Launcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Environment.ExitCode = 1;
            }
        }
    }

    internal sealed class LauncherForm : Form
    {
        private readonly string root;
        private readonly string launchScript;
        private readonly string stopScript;
        private readonly string settingsPath;
        private readonly LauncherSettingsStore settingsStore;
        private readonly bool smokeTest;
        private readonly string runtimePath;
        private readonly Color blue = LauncherTheme.Accent;
        private readonly Color green = Color.FromArgb(105, 194, 149);
        private readonly Color red = Color.FromArgb(235, 128, 128);
        private readonly Color amber = Color.FromArgb(215, 173, 103);

        private Label statusLabel;
        private Label detailLabel;
        private ProgressTrack progressBar;
        private Label gamePathLabel;
        private Button browseButton;
        private TextBox outputBox;
        private Button startButton;
        private Button stopButton;
        private Button logsButton;
        private Button settingsButton;
        private Process launcherProcess;
        private Timer runtimeTimer;
        private long redirectorLogOffset;
        private bool monitorRuntime;
        private bool stopping;
        private string workerLogPath;
        private long workerLogOffset;
        private string workerLogPending = "";

        internal LauncherForm(bool smokeTest)
        {
            this.smokeTest = smokeTest;
            root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            launchScript = Path.Combine(root, "Launch-LocalFUT.ps1");
            stopScript = Path.Combine(root, "Stop-LocalFUT.ps1");
            settingsStore = new LauncherSettingsStore(LauncherSettingsStore.DefaultRoot());
            string recoveredSettings = settingsStore.PrepareForSetup();
            settingsStore.InitializeRuntimeConfig(Path.Combine(root, "runtime-source", "localfut21", "config.example.json"));
            settingsPath = settingsStore.SettingsPath;
            runtimePath = Path.Combine(root, "runtime");

            InitializeWindow();
            InitializeControls();
            runtimeTimer = new Timer();
            runtimeTimer.Interval = 1000;
            runtimeTimer.Tick += RuntimeTimerTick;
            runtimeTimer.Start();
            RefreshGamePath();
            SetStage("Ready to start", 0, LauncherTheme.Text, "Sign in to EA App and close FIFA 21 before starting your local session.");
            AppendLog("Waiting to start. Settings are saved on this PC only.", false);
            if (recoveredSettings != null) AppendLog("Invalid settings were preserved at " + recoveredSettings + ". Please select your FIFA installation again.", true);
        }

        private void InitializeWindow()
        {
            Text = "FIFA 21 LocalFUT Launcher";
            ClientSize = new Size(920, 730);
            MinimumSize = SizeFromClientSize(new Size(860, 700));
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = LauncherTheme.Background;
            ForeColor = LauncherTheme.Text;
            ShowIcon = false;
            Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            AutoScaleMode = AutoScaleMode.Dpi;
        }

        protected override void OnHandleCreated(EventArgs args)
        {
            base.OnHandleCreated(args);
            LauncherTheme.DarkTitleBar(this);
        }

        private void InitializeControls()
        {
            Controls.Add(MakeLabel("FIFA 21 LocalFUT", 28, 26, 520, 36, 22, LauncherTheme.Text));
            settingsButton = CreateButton("Settings", "settings", false, new Point(764, 26), new Size(128, 40));
            settingsButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            settingsButton.Click += SettingsButtonClick;
            Controls.Add(settingsButton);

            SurfacePanel installation = Card(28, 82, 864, 84);
            installation.Controls.Add(new MaterialGlyph("description") { Location = new Point(20, 24), Size = new Size(30, 30) });
            installation.Controls.Add(MakeLabel("Game installation", 66, 14, 500, 24, 12, LauncherTheme.Text));
            gamePathLabel = MakeLabel("", 66, 42, 640, 25, 10.5F, LauncherTheme.Muted);
            gamePathLabel.AutoEllipsis = true;
            gamePathLabel.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            installation.Controls.Add(gamePathLabel);
            browseButton = CreateButton("Browse", null, false, new Point(738, 23), new Size(106, 38));
            browseButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            browseButton.Click += BrowseGameClick;
            installation.Controls.Add(browseButton);

            startButton = CreateButton("Start LocalFUT", "play_arrow", true, new Point(28, 182), new Size(222, 46));
            startButton.Font = new Font("Segoe UI", 12F);
            startButton.Click += StartButtonClick;
            Controls.Add(startButton);
            stopButton = CreateButton("Stop services", "stop", false, new Point(264, 182), new Size(194, 46));
            stopButton.Font = new Font("Segoe UI", 12F);
            stopButton.Click += StopButtonClick;
            Controls.Add(stopButton);
            SurfacePanel status = Card(28, 244, 864, 102);
            statusLabel = MakeLabel("", 20, 16, 820, 26, 12, LauncherTheme.Text);
            statusLabel.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            status.Controls.Add(statusLabel);
            detailLabel = MakeLabel("", 20, 44, 820, 32, 9.5F, LauncherTheme.Muted);
            detailLabel.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            status.Controls.Add(detailLabel);
            progressBar = new ProgressTrack();
            progressBar.Location = new Point(20, 84);
            progressBar.Size = new Size(824, 4);
            progressBar.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            status.Controls.Add(progressBar);

            SurfacePanel activity = Card(28, 362, 864, 318);
            activity.Anchor |= AnchorStyles.Bottom;
            activity.Controls.Add(MakeLabel("Activity", 18, 16, 300, 24, 10.5F, LauncherTheme.Text));
            logsButton = CreateButton("Open logs", "folder_open", false, new Point(714, 10), new Size(132, 32));
            logsButton.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            logsButton.Click += LogsButtonClick;
            activity.Controls.Add(logsButton);
            outputBox = new TextBox();
            outputBox.Location = new Point(18, 52);
            outputBox.Size = new Size(828, 248);
            outputBox.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            outputBox.BackColor = LauncherTheme.Surface;
            outputBox.ForeColor = LauncherTheme.Muted;
            outputBox.Font = new Font("Consolas", 10F, FontStyle.Regular, GraphicsUnit.Point);
            outputBox.Multiline = true;
            outputBox.ReadOnly = true;
            outputBox.BorderStyle = BorderStyle.None;
            outputBox.WordWrap = true;
            outputBox.ScrollBars = ScrollBars.Vertical;
            outputBox.AccessibleName = "Live launcher activity";
            activity.Controls.Add(outputBox);
            Label footer = MakeLabel("Admin approval is requested only when starting or stopping services.", 30, 700, 850, 20, 9, LauncherTheme.Muted);
            footer.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            Controls.Add(footer);
        }

        private SurfacePanel Card(int left, int top, int width, int height)
        {
            SurfacePanel panel = new SurfacePanel { Location = new Point(left, top), Size = new Size(width, height), Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
            Controls.Add(panel);
            return panel;
        }

        private static Label MakeLabel(string text, int left, int top, int width, int height, float size, Color color)
        {
            return new Label { Text = text, Location = new Point(left, top), Size = new Size(width, height), Font = new Font("Segoe UI", size), ForeColor = color };
        }

        private Button CreateButton(string text, string icon, bool primary, Point location, Size size)
        {
            Button button = new MaterialButton { Text = text, Location = location, Size = size };
            LauncherTheme.StyleButton(button, icon, primary);
            return button;
        }

        private void StartButtonClick(object sender, EventArgs eventArgs)
        {
            if (launcherProcess != null && !launcherProcess.HasExited)
            {
                MessageBox.Show("A launch is already in progress.", Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            if (!File.Exists(launchScript))
            {
                ShowMissingFile(launchScript);
                return;
            }

            if (EnsureGameSettings()) BeginWorkflow(false);
        }

        private void BeginWorkflow(bool stop)
        {
            try
            {
                stopping = stop;
                outputBox.Clear();
                string logs = Path.Combine(settingsStore.Root, "logs");
                Directory.CreateDirectory(logs);
                workerLogPath = Path.Combine(logs, "launcher-" + Guid.NewGuid().ToString("N") + ".log");
                File.WriteAllText(workerLogPath, "");
                workerLogOffset = 0;
                workerLogPending = "";
                redirectorLogOffset = File.Exists(Path.Combine(runtimePath, "redirector.log")) ? new FileInfo(Path.Combine(runtimePath, "redirector.log")).Length : 0;
                monitorRuntime = !stop;
                startButton.Enabled = stopButton.Enabled = settingsButton.Enabled = browseButton.Enabled = false;
                SetStage(stop ? "Stopping" : "Starting", 5, blue, "Approve the UAC prompt for local services. Settings remain with your Windows user.");
                AppendLog("User settings: " + settingsPath, false);
                AppendLog("Launch log: " + workerLogPath, false);
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
                info.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File " + Quote(stop ? stopScript : launchScript)
                    + " -LauncherLogPath " + Quote(workerLogPath);
                if (!stop) info.Arguments += " -UserConfigRoot " + Quote(settingsStore.Root);
                info.WorkingDirectory = root;
                info.UseShellExecute = true;
                info.Verb = "runas";
                info.WindowStyle = ProcessWindowStyle.Hidden;
                launcherProcess = new Process();
                launcherProcess.StartInfo = info;
                launcherProcess.EnableRaisingEvents = true;
                launcherProcess.Exited += LauncherProcessExited;
                launcherProcess.Start();
            }
            catch (Exception exception)
            {
                startButton.Enabled = stopButton.Enabled = settingsButton.Enabled = browseButton.Enabled = true;
                monitorRuntime = false;
                if (launcherProcess != null) { launcherProcess.Dispose(); launcherProcess = null; }
                Win32Exception nativeError = exception as Win32Exception;
                if (nativeError != null && nativeError.NativeErrorCode == 1223)
                {
                    SetStage("Cancelled", 0, amber, "Administrator permission was cancelled. Your settings are saved; no workflow was started.");
                    return;
                }
                SetStage("Could not start", 0, red, exception.Message);
                AppendLog(exception.ToString(), true);
            }
        }

        private void ConsumeLine(string line, bool isError)
        {
            if (String.IsNullOrWhiteSpace(line) || IsDisposed)
            {
                return;
            }

            if (InvokeRequired)
            {
                BeginInvoke((MethodInvoker)delegate { ConsumeLine(line, isError); });
                return;
            }
            AppendLog(line, isError);
            string normalized = line.ToLowerInvariant();
            if (normalized.Contains("lsx:"))
            {
                if (normalized.Contains("ea app"))
                {
                    SetStage("EA App LSX selected", 25, blue, "EA App owns port 3216 and will launch the licensed FIFA 21 copy.");
                }
                else if (normalized.Contains("replacement"))
                {
                    SetStage("Replacement LSX selected", 25, blue, "The local FIFA 21 server owns port 3216 for this launch.");
                }
                else
                {
                    SetStage("LSX mode selected", 25, blue, "The configured Origin/EA launch boundary is ready.");
                }
            }
            if (normalized.Contains("server:"))
                SetStage("Local services ready", 45, blue, "Blaze and FUT listeners are ready.");
            if (normalized.Contains("game:"))
                SetStage("FIFA 21 detected", 75, blue, "Waiting for the reversible trust bridge.");
            if (normalized.Contains("trust bridge:"))
                SetStage("Trust bridge checked", 90, blue, "Checking local redirector routing.");
            if (normalized.Contains("connection:") && normalized.Contains("local routing armed"))
                SetStage("Waiting for FIFA TLS", 90, blue, "Local routing is armed. Waiting for FIFA to accept the redirector certificate...");
            if (normalized.Contains("error:"))
                SetStage("Blocked", progressBar.Value, red, "Read the final error below, then open logs if more detail is needed.");
        }

        private void LauncherProcessExited(object sender, EventArgs eventArgs)
        {
            int exitCode = -1;
            try
            {
                Process completed = (Process)sender;
                completed.WaitForExit();
                exitCode = completed.ExitCode;
            }
            catch
            {
            }

            if (IsDisposed)
            {
                return;
            }

            BeginInvoke((MethodInvoker)delegate
            {
                ReadWorkerLog(true);
                startButton.Enabled = stopButton.Enabled = settingsButton.Enabled = browseButton.Enabled = true;
                if (exitCode == 0)
                {
                    if (stopping)
                    {
                        monitorRuntime = false;
                        SetStage("Stopped", 0, amber, "Local services were stopped and the hosts redirect was removed.");
                        AppendLog("Stop workflow completed.", false);
                        return;
                    }
                    if (progressBar.Value < 100)
                    {
                        SetStage("Waiting for FIFA", Math.Max(progressBar.Value, 90), blue, "Use the online/FUT entry; this window is monitoring the TLS redirector.");
                    }
                    AppendLog("Launcher exited successfully.", false);
                }
                else
                {
                    monitorRuntime = false;
                    SetStage("Launch failed", progressBar.Value, red, "Open logs or correct the displayed prerequisite, then retry.");
                    AppendLog("Launcher exited with code " + exitCode + ".", true);
                }
            });
        }

        private void StopButtonClick(object sender, EventArgs eventArgs)
        {
            if (!File.Exists(stopScript))
            {
                ShowMissingFile(stopScript);
                return;
            }

            if (launcherProcess != null && !launcherProcess.HasExited)
            {
                MessageBox.Show("Wait for the current start/stop workflow to finish before stopping services.", Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            BeginWorkflow(true);
        }

        private void LogsButtonClick(object sender, EventArgs eventArgs)
        {
            string logs = Path.Combine(settingsStore.Root, "logs");
            Directory.CreateDirectory(logs);
            Process.Start("explorer.exe", Quote(logs));
        }

        private void SettingsButtonClick(object sender, EventArgs eventArgs)
        {
            EditSettings();
        }

        private void RefreshGamePath()
        {
            string game = settingsStore.Load()["gamePath"] as string;
            gamePathLabel.Text = String.IsNullOrWhiteSpace(game) ? "Select FIFA21.exe to get started" : game;
            gamePathLabel.AccessibleName = "Selected FIFA 21 executable: " + gamePathLabel.Text;
        }

        private void BrowseGameClick(object sender, EventArgs eventArgs)
        {
            SelectGameInstallation();
        }

        private bool SelectGameInstallation()
        {
            try
            {
                using (OpenFileDialog picker = new OpenFileDialog())
                {
                    picker.Title = "Choose your FIFA 21 installation — select FIFA21.exe";
                    picker.Filter = "FIFA 21 (FIFA21.exe)|FIFA21.exe";
                    picker.CheckFileExists = true;
                    picker.RestoreDirectory = true;
                    if (picker.ShowDialog(this) != DialogResult.OK)
                    {
                        if (settingsStore.NeedsGameSelection()) SetStage("Choose your FIFA installation", 0, amber, "Select Browse when you are ready. No game or services have been started.");
                        return false;
                    }
                    if (!LauncherSettingsStore.ValidGamePath(picker.FileName)) throw new InvalidDataException("Select an existing FIFA21.exe.");
                    var settings = settingsStore.Load();
                    settings["gamePath"] = picker.FileName;
                    settingsStore.Save(settings);
                    RefreshGamePath();
                    SetStage("Ready to start", 0, LauncherTheme.Text, "Installation saved. Sign in to EA App, then select Start LocalFUT.");
                    return true;
                }
            }
            catch (Exception exception) { MessageBox.Show(this, exception.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning); return false; }
        }

        private bool EditSettings()
        {
            try
            {
                using (LauncherSettingsDialog dialog = new LauncherSettingsDialog(settingsStore))
                {
                    bool saved = dialog.ShowDialog(this) == DialogResult.OK;
                    if (saved) RefreshGamePath();
                    return saved;
                }
            }
            catch (Exception exception)
            {
                MessageBox.Show(this, exception.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return false;
            }
        }

        private bool EnsureGameSettings()
        {
            try
            {
                if (!settingsStore.NeedsGameSelection()) return true;
                SetStage("Choose your FIFA installation", 0, amber, "Select the FIFA21.exe you want to use. This choice stays on your PC.");
                return SelectGameInstallation();
            }
            catch (Exception exception)
            {
                MessageBox.Show(this, exception.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return false;
            }
        }

        protected override void OnShown(EventArgs args)
        {
            base.OnShown(args);
            if (!smokeTest) EnsureGameSettings();
        }

        private void ReadWorkerLog(bool flush)
        {
            if (String.IsNullOrEmpty(workerLogPath) || !File.Exists(workerLogPath)) return;
            try
            {
                using (FileStream stream = new FileStream(workerLogPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    if (stream.Length < workerLogOffset) { workerLogOffset = 0; workerLogPending = ""; }
                    stream.Seek(workerLogOffset, SeekOrigin.Begin);
                    using (StreamReader reader = new StreamReader(stream))
                    {
                        workerLogPending += reader.ReadToEnd();
                        workerLogOffset = stream.Position;
                    }
                }
                int newline;
                while ((newline = workerLogPending.IndexOf('\n')) >= 0)
                {
                    string line = workerLogPending.Substring(0, newline).TrimEnd('\r');
                    workerLogPending = workerLogPending.Substring(newline + 1);
                    ConsumeLine(line, line.IndexOf("ERROR:", StringComparison.OrdinalIgnoreCase) >= 0);
                }
                if (flush && workerLogPending.Length > 0) { ConsumeLine(workerLogPending, false); workerLogPending = ""; }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        private void RuntimeTimerTick(object sender, EventArgs eventArgs)
        {
            ReadWorkerLog(false);
            if (!monitorRuntime)
            {
                return;
            }

            string redirectorLog = Path.Combine(runtimePath, "redirector.log");
            if (!File.Exists(redirectorLog))
            {
                return;
            }

            try
            {
                using (FileStream stream = new FileStream(
                    redirectorLog,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete))
                {
                    if (stream.Length < redirectorLogOffset)
                    {
                        redirectorLogOffset = 0;
                    }
                    if (stream.Length == redirectorLogOffset)
                    {
                        return;
                    }

                    stream.Seek(redirectorLogOffset, SeekOrigin.Begin);
                    using (StreamReader reader = new StreamReader(stream))
                    {
                        string update = reader.ReadToEnd();
                        redirectorLogOffset = stream.Length;
                        string normalized = update.ToLowerInvariant();

                        if (normalized.Contains("certificate_unknown") || normalized.Contains("certificate unknown"))
                        {
                            SetStage("TLS certificate rejected", 90, red, "FIFA reached localhost but rejected the redirector certificate. Blaze and FUT were not reached.");
                            AppendLog("FIFA rejected the local redirector certificate. The failure is before Blaze/POW/FUT.", true);
                        }
                        else if (normalized.Contains("[redirector]"))
                        {
                            SetStage("Redirector accepted", 100, green, "FIFA completed TLS and requested its local Blaze endpoint.");
                            AppendLog("FIFA accepted redirector TLS and requested the local endpoint.", false);
                        }
                        else if (normalized.Contains("[tls] negotiated"))
                        {
                            SetStage("TLS accepted", 95, green, "FIFA accepted the certificate; waiting for the redirector request.");
                            AppendLog("FIFA accepted the local TLS certificate.", false);
                        }
                    }
                }
            }
            catch (IOException)
            {
            }
            catch (UnauthorizedAccessException)
            {
            }
        }

        private void SetStage(string stage, int progress, Color color, string detail)
        {
            statusLabel.Text = stage;
            statusLabel.ForeColor = color;
            detailLabel.Text = detail;
            progressBar.Value = Math.Max(progressBar.Minimum, Math.Min(progressBar.Maximum, progress));
            progressBar.ForeColor = color;
        }

        private void AppendLog(string message, bool isError)
        {
            outputBox.SelectionStart = outputBox.TextLength;
            outputBox.SelectionLength = 0;
            outputBox.AppendText("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + (isError ? "ERROR: " : "") + message + Environment.NewLine);
            outputBox.ScrollToCaret();
        }

        private void ShowMissingFile(string path)
        {
            MessageBox.Show(
                "Required file was not found:\r\n\r\n" + path,
                Text,
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        protected override void OnFormClosing(FormClosingEventArgs eventArgs)
        {
            if (launcherProcess != null && !launcherProcess.HasExited)
            {
                DialogResult answer = MessageBox.Show(
                    "The service workflow is still running. Close only this window?\r\n\r\nClosing this window does not stop the elevated workflow or FIFA.",
                    Text,
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning);
                if (answer == DialogResult.No)
                {
                    eventArgs.Cancel = true;
                    return;
                }
            }
            base.OnFormClosing(eventArgs);
        }
    }
}
