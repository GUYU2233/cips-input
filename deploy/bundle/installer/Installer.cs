// cips-input installer bootstrapper.
//
// Built by deploy/bundle/build_installer.ps1 with the .NET Framework compiler
// that ships with Windows (csc.exe), so the project needs no extra toolchain.
//
// Responsibilities:
//   1. carry the whole payload (official PIME installer + cips-input module +
//      install.ps1) as an embedded zip resource,
//   2. request elevation through the manifest (app.manifest),
//   3. extract the payload to a temporary folder,
//   4. run install.ps1 with Windows PowerShell 5.1, streaming its output into
//      a small progress window,
//   5. clean up the temporary folder and report the exit code.
//
// /batch runs without UI and without waiting for a click (used by CI tests).
// Every other switch is forwarded to install.ps1, e.g. /batch -DryRun.

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace CipsInputInstaller
{
    internal static class Program
    {
        private const string ResourceName = "cips.payload.zip";
        private const string MutexName = "Global\\cips-input-installer";

        [STAThread]
        private static int Main(string[] args)
        {
            string[] forwarded;
            bool batch = SplitArgs(args, out forwarded);

            bool createdNew;
            using (Mutex mutex = new Mutex(true, MutexName, out createdNew))
            {
                if (!createdNew)
                {
                    if (!batch)
                    {
                        MessageBox.Show("cips-input is already being installed.", "cips-input",
                            MessageBoxButtons.OK, MessageBoxIcon.Information);
                    }
                    return 2;
                }

                string logPath = Path.Combine(Path.GetTempPath(), "cips-input-install.log");
                if (batch)
                {
                    return Runner.Run(forwarded, logPath, Console.WriteLine);
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                using (InstallerForm form = new InstallerForm(forwarded, logPath))
                {
                    Application.Run(form);
                    return form.ExitCode;
                }
            }
        }

        // Returns true for /batch; everything else is forwarded to install.ps1.
        private static bool SplitArgs(string[] args, out string[] forwarded)
        {
            bool batch = false;
            System.Collections.Generic.List<string> rest = new System.Collections.Generic.List<string>();
            if (args != null)
            {
                foreach (string arg in args)
                {
                    if (string.Equals(arg, "/batch", StringComparison.OrdinalIgnoreCase))
                    {
                        batch = true;
                    }
                    else
                    {
                        rest.Add(arg);
                    }
                }
            }
            forwarded = rest.ToArray();
            return batch;
        }
    }

    internal static class Runner
    {
        private const string ResourceName = "cips.payload.zip";

        public static int Run(string[] psArgs, string logPath, Action<string> onLine)
        {
            string tempDir = Path.Combine(Path.GetTempPath(), "cips-input-setup-" + Guid.NewGuid().ToString("N"));
            try
            {
                Directory.CreateDirectory(tempDir);
                string payloadDir = Path.Combine(tempDir, "payload");
                ExtractPayload(payloadDir);

                string script = Path.Combine(payloadDir, "install.ps1");
                if (!File.Exists(script))
                {
                    Emit(onLine, "[FAIL] payload is missing install.ps1");
                    return 1;
                }

                string powershell = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.System),
                    @"WindowsPowerShell\v1.0\powershell.exe");
                if (!File.Exists(powershell))
                {
                    Emit(onLine, "[FAIL] Windows PowerShell 5.1 was not found.");
                    return 1;
                }

                StringBuilder arguments = new StringBuilder();
                arguments.Append("-NoProfile -ExecutionPolicy Bypass -NoLogo -File \"");
                arguments.Append(script);
                arguments.Append("\" -NoPrompt");
                foreach (string arg in psArgs)
                {
                    arguments.Append(' ');
                    arguments.Append(Quote(arg));
                }

                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = powershell;
                info.Arguments = arguments.ToString();
                info.WorkingDirectory = payloadDir;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                info.StandardOutputEncoding = Encoding.UTF8;
                info.StandardErrorEncoding = Encoding.UTF8;

                Emit(onLine, "[INFO] payload extracted to " + payloadDir);
                using (Process process = new Process())
                {
                    process.StartInfo = info;
                    process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
                    {
                        if (e.Data != null) { Emit(onLine, e.Data); }
                    };
                    process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                    {
                        if (e.Data != null) { Emit(onLine, e.Data); }
                    };
                    process.Start();
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    process.WaitForExit();
                    Emit(onLine, "[INFO] installer exit code: " + process.ExitCode);
                    return process.ExitCode;
                }
            }
            catch (Exception ex)
            {
                Emit(onLine, "[FAIL] " + ex.Message);
                return 1;
            }
            finally
            {
                TryDeleteDirectory(tempDir);
            }
        }

        private static void Emit(Action<string> onLine, string line)
        {
            try
            {
                if (onLine != null) { onLine(line); }
            }
            catch
            {
                // A broken output sink must not abort the installation.
            }
        }

        private static string Quote(string value)
        {
            if (value == null) { return "\"\""; }
            if (value.IndexOf(' ') < 0 && value.IndexOf('"') < 0) { return value; }
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static void ExtractPayload(string payloadDir)
        {
            Assembly assembly = Assembly.GetExecutingAssembly();
            using (Stream resource = assembly.GetManifestResourceStream(ResourceName))
            {
                if (resource == null)
                {
                    throw new InvalidOperationException("Embedded payload is missing (resource " + ResourceName + ").");
                }
                Directory.CreateDirectory(payloadDir);
                string zipPath = Path.Combine(Path.GetDirectoryName(payloadDir), "payload.zip");
                using (FileStream file = File.Create(zipPath))
                {
                    resource.CopyTo(file);
                }
                ZipFile.ExtractToDirectory(zipPath, payloadDir);
                try { File.Delete(zipPath); } catch { }
            }
        }

        private static void TryDeleteDirectory(string path)
        {
            for (int attempt = 0; attempt < 10; attempt++)
            {
                try
                {
                    if (Directory.Exists(path)) { Directory.Delete(path, true); }
                    return;
                }
                catch
                {
                    Thread.Sleep(200);
                }
            }
        }
    }

    internal sealed class InstallerForm : Form
    {
        private readonly string[] _psArgs;
        private readonly string _logPath;
        private readonly Label _status;
        private readonly ProgressBar _progress;
        private readonly TextBox _output;
        private readonly Button _closeButton;
        private int _exitCode = 1;
        private bool _running = true;

        public InstallerForm(string[] psArgs, string logPath)
        {
            _psArgs = psArgs;
            _logPath = logPath;

            Text = "cips-input 安装程序";
            ClientSize = new Size(620, 430);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            Label title = new Label();
            title.Text = "cips-input 安装程序";
            title.Font = new Font(Font.FontFamily, 12F, FontStyle.Bold);
            title.AutoSize = true;
            title.Location = new Point(16, 14);
            Controls.Add(title);

            Label description = new Label();
            description.Text = "将安装 cips-input 输入法（内置 PIME 运行时）。安装需要管理员权限。";
            description.AutoSize = true;
            description.Location = new Point(18, 44);
            Controls.Add(description);

            _status = new Label();
            _status.Text = "正在准备...";
            _status.AutoSize = false;
            _status.AutoEllipsis = true;
            _status.Location = new Point(18, 74);
            _status.Size = new Size(584, 20);
            Controls.Add(_status);

            _progress = new ProgressBar();
            _progress.Style = ProgressBarStyle.Marquee;
            _progress.MarqueeAnimationSpeed = 30;
            _progress.Location = new Point(18, 98);
            _progress.Size = new Size(584, 16);
            Controls.Add(_progress);

            _output = new TextBox();
            _output.Multiline = true;
            _output.ReadOnly = true;
            _output.ScrollBars = ScrollBars.Vertical;
            _output.WordWrap = false;
            _output.Font = new Font("Consolas", 8.5F);
            _output.BackColor = Color.FromArgb(250, 250, 250);
            _output.Location = new Point(18, 124);
            _output.Size = new Size(584, 250);
            Controls.Add(_output);

            _closeButton = new Button();
            _closeButton.Text = "关闭";
            _closeButton.Enabled = false;
            _closeButton.Size = new Size(96, 30);
            _closeButton.Location = new Point(506, 386);
            _closeButton.Click += delegate { Close(); };
            Controls.Add(_closeButton);

            FormClosing += OnFormClosing;
        }

        public int ExitCode
        {
            get { return _exitCode; }
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            Thread worker = new Thread(Worker);
            worker.IsBackground = true;
            worker.Start();
        }

        private void Worker()
        {
            int code = Runner.Run(_psArgs, _logPath, OnLine);
            try
            {
                BeginInvoke(new Action<int>(Finish), code);
            }
            catch
            {
                // The window may already be disposed.
            }
        }

        private void OnLine(string line)
        {
            try
            {
                if (IsDisposed) { return; }
                BeginInvoke(new Action<string>(AppendLine), line);
            }
            catch
            {
                // Ignore UI marshalling failures during shutdown.
            }
        }

        private void AppendLine(string line)
        {
            if (IsDisposed) { return; }
            _output.AppendText(line + Environment.NewLine);
            if (line.StartsWith("[STEP]"))
            {
                _status.Text = line.Substring(6).Trim();
            }
        }

        private void Finish(int code)
        {
            _exitCode = code;
            _running = false;
            _progress.Style = ProgressBarStyle.Blocks;
            _progress.MarqueeAnimationSpeed = 0;
            _closeButton.Enabled = true;
            if (code == 0)
            {
                _status.Text = "安装完成。";
                _status.ForeColor = Color.ForestGreen;
            }
            else
            {
                _status.Text = "安装失败，退出码 " + code + "。详见日志：" + _logPath;
                _status.ForeColor = Color.Firebrick;
            }
        }

        private void OnFormClosing(object sender, FormClosingEventArgs e)
        {
            if (_running)
            {
                e.Cancel = true;
                MessageBox.Show("安装正在进行，请等待它结束。", "cips-input",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        }
    }
}
