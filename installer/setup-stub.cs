using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

internal static class VidoSwiftSetup
{
    [STAThread]
    private static int Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string temp = Path.Combine(Path.GetTempPath(), "VidoSwift-Setup-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(temp);
            string archivePath = Path.Combine(temp, "payload.zip");
            ExtractEmbeddedPayload(Process.GetCurrentProcess().MainModule.FileName, archivePath);
            string extracted = Path.Combine(temp, "files");
            Directory.CreateDirectory(extracted);
            ExtractSafely(archivePath, extracted);

            string app = Path.Combine(extracted, "VidoSwift.exe");
            string installScript = Path.Combine(extracted, "install.ps1");
            string uninstallScript = Path.Combine(extracted, "uninstall.ps1");
            if (!File.Exists(app) || !File.Exists(installScript) || !File.Exists(uninstallScript))
                throw new InvalidDataException("The VidoSwift setup files are incomplete.");

            using (SetupWizard wizard = new SetupWizard(extracted, installScript, app, Path.Combine(temp, "install-progress.txt")))
            {
                Application.Run(wizard);
                if (!wizard.InstallSucceeded) return wizard.UserCancelled ? 0 : 1;
                if (wizard.LaunchOnFinish)
                {
                    try
                    {
                        Process.Start(new ProcessStartInfo(Path.Combine(wizard.InstallTarget, "VidoSwift.exe"))
                        {
                            WorkingDirectory = wizard.InstallTarget,
                            UseShellExecute = true
                        });
                    }
                    catch (Exception launchError)
                    {
                        MessageBox.Show("VidoSwift was installed, but could not be launched automatically.\r\n\r\n" + launchError.Message,
                            "VidoSwift Setup", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    }
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show("VidoSwift Setup could not continue.\r\n\r\n" + error.Message,
                "VidoSwift Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            try { if (Directory.Exists(temp)) Directory.Delete(temp, true); } catch { }
        }
    }

    private static void ExtractEmbeddedPayload(string self, string archivePath)
    {
        using (FileStream input = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (BinaryReader reader = new BinaryReader(input))
        {
            if (input.Length < 12) throw new InvalidDataException("The installer payload is missing.");
            input.Seek(-12, SeekOrigin.End);
            string marker = Encoding.ASCII.GetString(reader.ReadBytes(4));
            long archiveLength = reader.ReadInt64();
            long archiveStart = input.Length - 12 - archiveLength;
            if (marker != "VSWF" || archiveLength <= 0 || archiveStart <= 0)
                throw new InvalidDataException("The installer payload is damaged.");
            input.Position = archiveStart;
            using (FileStream output = new FileStream(archivePath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                byte[] buffer = new byte[1024 * 1024];
                long remaining = archiveLength;
                while (remaining > 0)
                {
                    int read = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                    if (read == 0) throw new EndOfStreamException("The installer payload is incomplete.");
                    output.Write(buffer, 0, read);
                    remaining -= read;
                }
            }
        }
    }

    private static void ExtractSafely(string archivePath, string destination)
    {
        string root = Path.GetFullPath(destination).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        using (ZipArchive archive = ZipFile.OpenRead(archivePath))
        {
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string target = Path.GetFullPath(Path.Combine(root, entry.FullName.Replace('/', Path.DirectorySeparatorChar)));
                if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("The setup archive contains an invalid path.");
                if (entry.FullName.EndsWith("/", StringComparison.Ordinal))
                {
                    Directory.CreateDirectory(target);
                    continue;
                }
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                using (Stream source = entry.Open())
                using (FileStream output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    source.CopyTo(output);
            }
        }
    }
}

internal sealed class SetupWizard : Form
{
    private static readonly Color BackgroundColor = Color.FromArgb(9, 9, 11);
    private static readonly Color SurfaceColor = Color.FromArgb(17, 17, 19);
    private static readonly Color InputColor = Color.FromArgb(20, 20, 24);
    private static readonly Color BorderColor = Color.FromArgb(41, 41, 46);
    private static readonly Color TextColor = Color.FromArgb(245, 245, 247);
    private static readonly Color MutedColor = Color.FromArgb(129, 129, 138);
    private static readonly Color AccentColor = Color.FromArgb(124, 92, 255);
    private static readonly Color ErrorColor = Color.FromArgb(255, 105, 115);

    private readonly string payloadRoot;
    private readonly string installScript;
    private readonly string applicationExe;
    private readonly string progressFile;
    private readonly Panel contentPanel;
    private readonly Panel actionPanel;
    private readonly Label stepName;
    private readonly Label stepCount;
    private readonly Panel[] stepSegments = new Panel[4];
    private readonly Timer progressTimer;
    private readonly StringBuilder installErrors = new StringBuilder();
    private readonly object installErrorLock = new object();
    private TextBox pathInput;
    private Label pathError;
    private Label progressStatus;
    private Label progressPercent;
    private Panel progressFill;
    private CheckBox launchCheck;
    private Process installProcess;
    private int currentStep;
    private bool allowClose;

    internal bool InstallSucceeded { get; private set; }
    internal bool UserCancelled { get; private set; }
    internal bool LaunchOnFinish { get; private set; }
    internal string InstallTarget { get; private set; }

    internal SetupWizard(string payloadRoot, string installScript, string applicationExe, string progressFile)
    {
        this.payloadRoot = payloadRoot;
        this.installScript = installScript;
        this.applicationExe = applicationExe;
        this.progressFile = progressFile;
        Text = "VidoSwift Setup";
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        ClientSize = new Size(560, 344);
        BackColor = BackgroundColor;
        ForeColor = TextColor;
        Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
        ShowInTaskbar = true;
        try { Icon = Icon.ExtractAssociatedIcon(applicationExe); } catch { }
        Shown += delegate { EnableDarkTitleBar(); };
        FormClosing += OnFormClosing;

        stepName = MakeLabel("WELCOME", 30, 17, 260, 18, 9F, AccentColor, true);
        stepCount = MakeLabel("STEP 1 OF 4", 430, 17, 100, 18, 8F, MutedColor, true);
        stepCount.TextAlign = ContentAlignment.TopRight;
        Controls.Add(stepName);
        Controls.Add(stepCount);

        for (int i = 0; i < stepSegments.Length; i++)
        {
            stepSegments[i] = new Panel { Left = 30 + i * 126, Top = 45, Width = 120, Height = 3, BackColor = BorderColor };
            Controls.Add(stepSegments[i]);
        }

        contentPanel = new Panel { Left = 30, Top = 70, Width = 500, Height = 204, BackColor = BackgroundColor };
        Controls.Add(contentPanel);
        actionPanel = new Panel { Left = 0, Top = 282, Width = 560, Height = 62, BackColor = SurfaceColor };
        var divider = new Panel { Left = 0, Top = 0, Width = 560, Height = 1, BackColor = BorderColor };
        actionPanel.Controls.Add(divider);
        Controls.Add(actionPanel);

        progressTimer = new Timer { Interval = 250 };
        progressTimer.Tick += OnProgressTick;
        RenderStep(0);
    }

    private void EnableDarkTitleBar()
    {
        try
        {
            int enabled = 1;
            DwmSetWindowAttribute(Handle, 20, ref enabled, sizeof(int));
            DwmSetWindowAttribute(Handle, 19, ref enabled, sizeof(int));
        }
        catch { }
    }

    private void OnFormClosing(object sender, FormClosingEventArgs e)
    {
        if (allowClose) return;
        if (currentStep == 2)
        {
            e.Cancel = true;
            return;
        }
        if (!InstallSucceeded)
        {
            DialogResult answer = MessageBox.Show("VidoSwift has not been installed yet. Exit setup?", "Exit VidoSwift Setup",
                MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (answer != DialogResult.Yes) { e.Cancel = true; return; }
            UserCancelled = true;
            allowClose = true;
        }
    }

    private void RenderStep(int step)
    {
        currentStep = step;
        AcceptButton = null;
        string[] names = { "WELCOME", "INSTALL LOCATION", "INSTALLING", "COMPLETE" };
        stepName.Text = names[step];
        stepCount.Text = "STEP " + (step + 1) + " OF 4";
        for (int i = 0; i < stepSegments.Length; i++)
            stepSegments[i].BackColor = i <= step ? AccentColor : BorderColor;
        contentPanel.Controls.Clear();
        actionPanel.Controls.Clear();
        actionPanel.Controls.Add(new Panel { Left = 0, Top = 0, Width = 560, Height = 1, BackColor = BorderColor });

        if (step == 0) RenderWelcome();
        else if (step == 1) RenderLocation();
        else if (step == 2) RenderInstalling();
        else RenderComplete();
    }

    private void RenderWelcome()
    {
        PictureBox mark = new PictureBox { Left = 15, Top = 53, Width = 58, Height = 58, SizeMode = PictureBoxSizeMode.Zoom, BackColor = SurfaceColor };
        try { mark.Image = Icon.ExtractAssociatedIcon(applicationExe).ToBitmap(); } catch { }
        contentPanel.Controls.Add(mark);
        contentPanel.Controls.Add(MakeLabel("Install VidoSwift", 95, 48, 380, 34, 21F, TextColor, true));
        contentPanel.Controls.Add(MakeLabel("A simple desktop app for downloading videos you have\r\npermission to save.", 96, 91, 380, 40, 10F, MutedColor, false));
        Label version = MakeLabel("●   Version 1.0.0", 96, 146, 180, 20, 9F, AccentColor, true);
        contentPanel.Controls.Add(version);
        AddAction("Cancel", false, ConfirmExit, 315);
        AddAction("Next", true, delegate { RenderStep(1); }, 425);
        AcceptButton = FindActionButton("Next");
    }

    private void RenderLocation()
    {
        contentPanel.Controls.Add(MakeLabel("Choose install folder", 0, 7, 480, 30, 18F, TextColor, true));
        contentPanel.Controls.Add(MakeLabel("The application and runtime files will stay together inside one VidoSwift folder.", 0, 44, 490, 35, 9F, MutedColor, false));
        contentPanel.Controls.Add(MakeLabel("INSTALL FOLDER", 0, 99, 220, 16, 8F, MutedColor, true));
        pathInput = new TextBox
        {
            Left = 0, Top = 119, Width = 365, Height = 32,
            Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "VidoSwift"),
            BackColor = InputColor, ForeColor = TextColor, BorderStyle = BorderStyle.FixedSingle,
            Font = new Font("Segoe UI", 9F, FontStyle.Regular),
            ShortcutsEnabled = true
        };
        pathInput.TextChanged += delegate { UpdatePathError(false); };
        contentPanel.Controls.Add(pathInput);
        Button browse = CreateButton("Browse…", false, delegate { BrowseForFolder(); });
        browse.SetBounds(377, 117, 105, 34);
        contentPanel.Controls.Add(browse);
        pathError = MakeLabel("Files will be installed together in this folder.", 0, 157, 490, 25, 8F, MutedColor, false);
        contentPanel.Controls.Add(pathError);
        AddAction("Cancel", false, ConfirmExit, 245);
        AddAction("Back", false, delegate { RenderStep(0); }, 355);
        AddAction("Install", true, BeginInstall, 455);
        AcceptButton = FindActionButton("Install");
    }

    private void BrowseForFolder()
    {
        string candidate;
        if (!TryNormalizeTarget(pathInput.Text, out candidate))
            candidate = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "VidoSwift");
        using (FolderBrowserDialog picker = new FolderBrowserDialog())
        {
            picker.Description = "Choose the VidoSwift install folder";
            picker.ShowNewFolderButton = true;
            picker.SelectedPath = Directory.Exists(candidate) ? candidate : Path.GetDirectoryName(candidate);
            if (picker.ShowDialog(this) == DialogResult.OK)
            {
                string selected;
                if (TryNormalizeTarget(picker.SelectedPath, out selected)) pathInput.Text = selected;
                UpdatePathError(false);
            }
        }
    }

    private bool TryNormalizeTarget(string value, out string target)
    {
        target = null;
        try
        {
            if (String.IsNullOrWhiteSpace(value)) return false;
            string path = Environment.ExpandEnvironmentVariables(value.Trim());
            if (!Path.IsPathRooted(path)) return false;
            path = Path.GetFullPath(path);
            string root = Path.GetPathRoot(path);
            if (!String.Equals(path, root, StringComparison.OrdinalIgnoreCase))
                path = path.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            target = String.Equals(Path.GetFileName(path), "VidoSwift", StringComparison.OrdinalIgnoreCase)
                ? path : Path.Combine(path, "VidoSwift");
            return true;
        }
        catch { return false; }
    }

    private void UpdatePathError(bool forceError)
    {
        if (pathError == null || pathInput == null) return;
        string target;
        bool valid = TryNormalizeTarget(pathInput.Text, out target);
        pathError.Text = valid ? "All VidoSwift files will be kept together in: " + target : "Enter a valid Windows folder path.";
        pathError.ForeColor = valid ? MutedColor : ErrorColor;
    }

    private void BeginInstall()
    {
        string target;
        if (!TryNormalizeTarget(pathInput.Text, out target)) { UpdatePathError(true); pathInput.Focus(); return; }
        InstallTarget = target;
        pathInput.Text = target;
        progressStatus = null;
        progressPercent = null;
        progressFill = null;
        RenderStep(2);
        ControlBox = false;
        try
        {
            if (File.Exists(progressFile)) File.Delete(progressFile);
            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell\\v1.0\\powershell.exe");
            if (!File.Exists(start.FileName)) start.FileName = "powershell.exe";
            start.Arguments = "-NoProfile -ExecutionPolicy Bypass -File " + Quote(installScript) +
                " -InstallPath " + Quote(InstallTarget) + " -ProgressFile " + Quote(progressFile) + " -Quiet";
            start.WorkingDirectory = payloadRoot;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            installProcess = new Process { StartInfo = start, EnableRaisingEvents = true };
            lock (installErrorLock) installErrors.Length = 0;
            installProcess.OutputDataReceived += delegate { };
            installProcess.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args)
            {
                if (String.IsNullOrWhiteSpace(args.Data)) return;
                lock (installErrorLock)
                {
                    if (installErrors.Length < 6000) installErrors.AppendLine(args.Data);
                }
            };
            installProcess.Start();
            installProcess.BeginOutputReadLine();
            installProcess.BeginErrorReadLine();
            progressTimer.Start();
        }
        catch (Exception error)
        {
            if (installProcess != null) { installProcess.Dispose(); installProcess = null; }
            ControlBox = true;
            MessageBox.Show("Setup could not start the installation.\r\n\r\n" + error.Message, "VidoSwift Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            RenderStep(1);
        }
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private void RenderInstalling()
    {
        contentPanel.Controls.Add(MakeLabel("Installing VidoSwift", 0, 45, 470, 32, 18F, TextColor, true));
        contentPanel.Controls.Add(MakeLabel("The application files are being placed together in the install folder.", 0, 82, 490, 30, 9F, MutedColor, false));
        Panel track = new Panel { Left = 0, Top = 132, Width = 490, Height = 7, BackColor = BorderColor };
        progressFill = new Panel { Left = 0, Top = 0, Width = 3, Height = 7, BackColor = AccentColor };
        track.Controls.Add(progressFill);
        contentPanel.Controls.Add(track);
        progressStatus = MakeLabel("Preparing installation…", 0, 151, 380, 22, 8F, MutedColor, false);
        progressPercent = MakeLabel("0%", 430, 151, 60, 22, 8F, MutedColor, false);
        progressPercent.TextAlign = ContentAlignment.TopRight;
        contentPanel.Controls.Add(progressStatus);
        contentPanel.Controls.Add(progressPercent);
    }

    private void OnProgressTick(object sender, EventArgs e)
    {
        try
        {
            if (File.Exists(progressFile))
            {
                string[] state = File.ReadAllText(progressFile).Split(new[] { '|' }, 2);
                if (state.Length == 2)
                {
                    int percent;
                    if (Int32.TryParse(state[0], out percent))
                    {
                        percent = Math.Max(0, Math.Min(100, percent));
                        if (progressFill != null) progressFill.Width = (int)(490 * percent / 100.0);
                        if (progressPercent != null) progressPercent.Text = percent + "%";
                    }
                    if (progressStatus != null) progressStatus.Text = state[1];
                }
            }
        }
        catch { }

        if (installProcess == null || !installProcess.HasExited) return;
        progressTimer.Stop();
        int code = installProcess.ExitCode;
        installProcess.WaitForExit();
        installProcess.Dispose();
        installProcess = null;
        ControlBox = true;
        if (code == 0)
        {
            InstallSucceeded = true;
            RenderStep(3);
        }
        else
        {
            string scriptDetail;
            lock (installErrorLock) scriptDetail = installErrors.ToString().Trim();
            if (scriptDetail.Length > 1800) scriptDetail = scriptDetail.Substring(scriptDetail.Length - 1800);
            string detail = "The installation did not finish. Check the selected folder and close any running VidoSwift app, then try again.";
            if (scriptDetail.Length > 0) detail += "\r\n\r\nDetails:\r\n" + scriptDetail;
            MessageBox.Show(detail, "VidoSwift Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            RenderStep(1);
        }
    }

    private void RenderComplete()
    {
        Label success = MakeLabel("✓", 5, 54, 54, 54, 29F, AccentColor, true);
        success.TextAlign = ContentAlignment.MiddleCenter;
        success.BackColor = SurfaceColor;
        contentPanel.Controls.Add(success);
        contentPanel.Controls.Add(MakeLabel("VidoSwift is ready", 78, 51, 400, 32, 19F, TextColor, true));
        contentPanel.Controls.Add(MakeLabel("Installation completed. One VidoSwift shortcut was added to your Desktop.\r\nDouble-click it to open the app.", 79, 91, 410, 43, 9F, MutedColor, false));
        launchCheck = new CheckBox { Left = 79, Top = 147, Width = 250, Height = 26, Text = "Launch VidoSwift", Checked = true, ForeColor = TextColor, BackColor = BackgroundColor, Font = new Font("Segoe UI", 9F, FontStyle.Regular), UseVisualStyleBackColor = false, FlatStyle = FlatStyle.Standard };
        contentPanel.Controls.Add(launchCheck);
        AddAction("Finish", true, delegate { LaunchOnFinish = launchCheck.Checked; allowClose = true; Close(); }, 435);
        AcceptButton = FindActionButton("Finish");
    }

    private void ConfirmExit()
    {
        DialogResult answer = MessageBox.Show("VidoSwift has not been installed yet. Exit setup?", "Exit VidoSwift Setup",
            MessageBoxButtons.YesNo, MessageBoxIcon.Question);
        if (answer == DialogResult.Yes)
        {
            UserCancelled = true;
            allowClose = true;
            Close();
        }
    }

    private void AddAction(string text, bool primary, Action action, int left)
    {
        Button button = CreateButton(text, primary, action);
        button.SetBounds(left, 14, 95, 34);
        actionPanel.Controls.Add(button);
    }

    private Button FindActionButton(string text)
    {
        foreach (Control control in actionPanel.Controls)
            if (control is Button && control.Text == text) return (Button)control;
        return null;
    }

    private Button CreateButton(string text, bool primary, Action action)
    {
        Button button = new Button
        {
            Text = text,
            BackColor = primary ? AccentColor : SurfaceColor,
            ForeColor = TextColor,
            FlatStyle = FlatStyle.Flat,
            Font = new Font("Segoe UI", 9F, FontStyle.Bold),
            Cursor = Cursors.Hand,
            UseVisualStyleBackColor = false,
            TabStop = true
        };
        button.FlatAppearance.BorderColor = primary ? AccentColor : BorderColor;
        button.FlatAppearance.MouseOverBackColor = primary ? Color.FromArgb(139, 116, 255) : Color.FromArgb(29, 29, 34);
        button.FlatAppearance.MouseDownBackColor = Color.FromArgb(96, 69, 220);
        button.Click += delegate { action(); };
        return button;
    }

    private Label MakeLabel(string text, int left, int top, int width, int height, float size, Color color, bool bold)
    {
        return new Label
        {
            Text = text,
            Left = left,
            Top = top,
            Width = width,
            Height = height,
            ForeColor = color,
            BackColor = BackgroundColor,
            Font = new Font("Segoe UI", size, bold ? FontStyle.Bold : FontStyle.Regular),
            AutoEllipsis = true,
            TextAlign = ContentAlignment.MiddleLeft
        };
    }

    [DllImport("dwmapi.dll", PreserveSig = true)]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int valueSize);
}
