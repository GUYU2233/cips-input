// cips-input installer bootstrapper.
//
// Built by deploy/bundle/build_installer.ps1 with the .NET Framework compiler
// that ships with Windows (csc.exe), so the project needs no extra toolchain.
//
// Responsibilities:
//   1. carry the payload (official PIME installer + cips-input module +
//      prebuilt rime-frost + install.ps1) as an embedded zip resource,
//   2. request elevation through the manifest (app.manifest),
//   3. in GUI mode, let the user opt in to installing the Wanxiang language
//      model (wanxiang-lts-zh-hans.gram, downloaded or picked locally),
//   4. extract the payload to a temporary folder and run install.ps1 with
//      Windows PowerShell 5.1, streaming its output into the progress window,
//   5. clean up the temporary folder and report the exit code.
//
// /batch runs without UI and without waiting for a click (used by CI tests).
// Every other switch is forwarded to install.ps1, e.g. /batch -DryRun or
// /batch -InstallGram.

using System;
using System.Collections.Generic;
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
                        MessageBox.Show("cips-input 正在安装中，请等待当前安装完成。", "cips-input",
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

        private static bool SplitArgs(string[] args, out string[] forwarded)
        {
            bool batch = false;
            List<string> rest = new List<string>();
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
        private const string GramFileName = "wanxiang-lts-zh-hans.gram";

        private readonly string[] _baseArgs;
        private readonly string _logPath;
        private readonly CheckBox _gramCheckBox;
        private readonly Button _browseGramButton;
        private readonly Label _gramHint;
        private readonly Label _status;
        private readonly ProgressBar _progress;
        private readonly TextBox _output;
        private readonly Button _installButton;
        private readonly Button _closeButton;

        private string _localGramPath = "";
        private int _exitCode = 1;
        private bool _running = false;
        private bool _finished = false;

        public InstallerForm(string[] psArgs, string logPath)
        {
            _baseArgs = psArgs ?? new string[0];
            _logPath = logPath;

            Text = "cips-input 安装程序";
            ClientSize = new Size(640, 505);
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
            description.Text = "内置白霜拼音（Rime Frost）与剪贴板模拟按键注入（含 PIME 运行时）。需要管理员权限。";
            description.AutoSize = true;
            description.Location = new Point(18, 42);
            Controls.Add(description);

            GroupBox optionsBox = new GroupBox();
            optionsBox.Text = "可选组件";
            optionsBox.Location = new Point(18, 68);
            optionsBox.Size = new Size(604, 78);
            Controls.Add(optionsBox);

            _gramCheckBox = new CheckBox();
            _gramCheckBox.Text = "附带安装万象语言模型 (wanxiang-lts-zh-hans.gram，约 373 MB)";
            _gramCheckBox.AutoSize = true;
            _gramCheckBox.Location = new Point(12, 22);
            _gramCheckBox.CheckedChanged += delegate { UpdateGramHint(); };
            optionsBox.Controls.Add(_gramCheckBox);

            _browseGramButton = new Button();
            _browseGramButton.Text = "选择本地模型...";
            _browseGramButton.Size = new Size(116, 24);
            _browseGramButton.Location = new Point(476, 18);
            _browseGramButton.Click += OnBrowseGram;
            optionsBox.Controls.Add(_browseGramButton);

            _gramHint = new Label();
            _gramHint.AutoSize = false;
            _gramHint.AutoEllipsis = true;
            _gramHint.ForeColor = Color.DimGray;
            _gramHint.Location = new Point(30, 48);
            _gramHint.Size = new Size(560, 20);
            optionsBox.Controls.Add(_gramHint);

            DetectInitialGramOptions();

            _status = new Label();
            _status.Text = "请确认安装选项后点击“开始安装”。";
            _status.AutoSize = false;
            _status.AutoEllipsis = true;
            _status.Location = new Point(18, 156);
            _status.Size = new Size(604, 20);
            Controls.Add(_status);

            _progress = new ProgressBar();
            _progress.Style = ProgressBarStyle.Blocks;
            _progress.Location = new Point(18, 180);
            _progress.Size = new Size(604, 16);
            Controls.Add(_progress);

            _output = new TextBox();
            _output.Multiline = true;
            _output.ReadOnly = true;
            _output.ScrollBars = ScrollBars.Vertical;
            _output.WordWrap = false;
            _output.Font = new Font("Consolas", 8.5F);
            _output.BackColor = Color.FromArgb(250, 250, 250);
            _output.Location = new Point(18, 206);
            _output.Size = new Size(604, 244);
            Controls.Add(_output);

            _installButton = new Button();
            _installButton.Text = "开始安装";
            _installButton.Size = new Size(104, 30);
            _installButton.Location = new Point(410, 462);
            _installButton.Click += OnStartInstall;
            Controls.Add(_installButton);

            _closeButton = new Button();
            _closeButton.Text = "取消";
            _closeButton.Size = new Size(96, 30);
            _closeButton.Location = new Point(526, 462);
            _closeButton.Click += delegate { Close(); };
            Controls.Add(_closeButton);

            AcceptButton = _installButton;
            FormClosing += OnFormClosing;
        }

        public int ExitCode
        {
            get { return _exitCode; }
        }

        private void DetectInitialGramOptions()
        {
            for (int i = 0; i < _baseArgs.Length; i++)
            {
                if (string.Equals(_baseArgs[i], "-InstallGram", StringComparison.OrdinalIgnoreCase))
                {
                    _gramCheckBox.Checked = true;
                }
                else if (string.Equals(_baseArgs[i], "-GramPath", StringComparison.OrdinalIgnoreCase) && i + 1 < _baseArgs.Length)
                {
                    _localGramPath = _baseArgs[i + 1];
                    _gramCheckBox.Checked = true;
                }
            }

            if (string.IsNullOrEmpty(_localGramPath))
            {
                try
                {
                    string exeDir = Path.GetDirectoryName(Application.ExecutablePath);
                    if (!string.IsNullOrEmpty(exeDir))
                    {
                        string sibling = Path.Combine(exeDir, GramFileName);
                        if (File.Exists(sibling))
                        {
                            _localGramPath = sibling;
                            _gramCheckBox.Checked = true;
                        }
                    }
                }
                catch
                {
                    // Ignore path inspection failures.
                }
            }
            UpdateGramHint();
        }

        private void UpdateGramHint()
        {
            if (!_gramCheckBox.Checked)
            {
                _gramHint.Text = "未勾选：仅安装白霜拼音词库（离线可用，也可稍后随时手动或重跑安装器补充）。";
            }
            else if (!string.IsNullOrEmpty(_localGramPath))
            {
                _gramHint.Text = "将使用本地模型文件：" + _localGramPath;
            }
            else
            {
                _gramHint.Text = "将从 GitHub Release 下载 wanxiang-lts-zh-hans.gram (~373 MB) 并启用整句模型。";
            }
        }

        private void OnBrowseGram(object sender, EventArgs e)
        {
            using (OpenFileDialog dialog = new OpenFileDialog())
            {
                dialog.Title = "选择万象语言模型文件 (" + GramFileName + ")";
                dialog.Filter = "Rime 语言模型 (*.gram)|*.gram|所有文件 (*.*)|*.*";
                dialog.FileName = GramFileName;
                if (dialog.ShowDialog(this) == DialogResult.OK)
                {
                    _localGramPath = dialog.FileName;
                    _gramCheckBox.Checked = true;
                    UpdateGramHint();
                }
            }
        }

        private void OnStartInstall(object sender, EventArgs e)
        {
            if (_running || _finished) { return; }

            List<string> finalArgs = new List<string>();
            bool hasInstallGram = false;
            bool hasGramPath = false;
            foreach (string arg in _baseArgs)
            {
                if (string.Equals(arg, "-InstallGram", StringComparison.OrdinalIgnoreCase))
                {
                    if (!_gramCheckBox.Checked) { continue; }
                    hasInstallGram = true;
                }
                finalArgs.Add(arg);
            }
            for (int i = 0; i < finalArgs.Count; i++)
            {
                if (string.Equals(finalArgs[i], "-GramPath", StringComparison.OrdinalIgnoreCase))
                {
                    hasGramPath = true;
                    break;
                }
            }

            if (_gramCheckBox.Checked)
            {
                if (!hasInstallGram) { finalArgs.Add("-InstallGram"); }
                if (!hasGramPath && !string.IsNullOrEmpty(_localGramPath))
                {
                    finalArgs.Add("-GramPath");
                    finalArgs.Add(_localGramPath);
                }
            }

            _running = true;
            _installButton.Enabled = false;
            _gramCheckBox.Enabled = false;
            _browseGramButton.Enabled = false;
            _closeButton.Enabled = false;
            _status.Text = "正在安装...";
            _progress.Style = ProgressBarStyle.Marquee;
            _progress.MarqueeAnimationSpeed = 30;

            string[] argsCopy = finalArgs.ToArray();
            Thread worker = new Thread(delegate() { Worker(argsCopy); });
            worker.IsBackground = true;
            worker.Start();
        }

        private void Worker(string[] args)
        {
            int code = Runner.Run(args, _logPath, OnLine);
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
            _finished = true;
            _progress.Style = ProgressBarStyle.Blocks;
            _progress.MarqueeAnimationSpeed = 0;
            _closeButton.Text = "关闭";
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
            else if (!_finished)
            {
                _exitCode = 1;
            }
        }
    }
}
