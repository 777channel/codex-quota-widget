using System;
using System.IO;
using System.IO.Compression;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class Launcher {
    static readonly string Root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CodexQuotaWidget");
    static readonly string Exe = Path.Combine(Root, "CodexQuota.exe");
    static readonly string Startup = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "Codex额度显示.lnk");
    static readonly string Menu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Codex额度显示");
    const string RegistryPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\CodexQuotaWidget";
    delegate bool EnumProc(IntPtr hwnd, IntPtr arg);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc callback, IntPtr arg);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);

    [STAThread] static int Main(string[] args) {
        bool silent = Array.IndexOf(args, "--silent") >= 0;
        try {
            if (args.Length > 0 && args[0] == "--cleanup") { Cleanup(int.Parse(args[1])); return 0; }
            if (args.Length > 0 && args[0] == "--uninstall") { Uninstall(silent); return 0; }
            using (Stream payload = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip")) {
                if (payload != null) {
                    bool created;
                    using (var mutex = new Mutex(true, @"Local\CodexQuotaWidgetSetup", out created)) {
                        if (!created) throw new IOException("另一个额度显示安装程序正在运行。");
                        Install(payload);
                    }
                    if (!silent) MessageBox.Show("安装完成，额度显示已启动。\n\n以后登录 Windows 后会在后台等待，打开 Codex 时自动显示。\n在额度文字上右键可调整位置，双击切换额度。\n\n可在 Windows 设置的已安装应用中卸载。", "Codex 额度显示", MessageBoxButtons.OK, MessageBoxIcon.Information);
                } else Start();
            }
            return 0;
        } catch (Exception error) {
            Directory.CreateDirectory(Root);
            File.WriteAllText(Path.Combine(Root, "setup-error.log"), error.ToString());
            if (!silent) MessageBox.Show(error.Message + "\n\n错误记录：" + Path.Combine(Root, "setup-error.log"), "Codex 额度显示", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    static void Stop() {
        string pidFile = Path.Combine(Root, "runtime.pid");
        int pid;
        if (!File.Exists(pidFile) || !int.TryParse(File.ReadAllText(pidFile), out pid)) return;
        Process process;
        try { process = Process.GetProcessById(pid); } catch (ArgumentException) { return; }
        using (process) {
            if (process.HasExited) return;
            string expected = Path.Combine(Root, @"runtime\pythonw.exe");
            if (!string.Equals(process.MainModule.FileName, expected, StringComparison.OrdinalIgnoreCase)) return;
            EnumWindows(delegate(IntPtr hwnd, IntPtr unused) {
                uint owner; GetWindowThreadProcessId(hwnd, out owner);
                if (owner == pid) PostMessage(hwnd, 0x10, IntPtr.Zero, IntPtr.Zero);
                return true;
            }, IntPtr.Zero);
            if (!process.WaitForExit(30000)) throw new IOException("额度显示尚未退出，请稍后重试。");
        }
    }

    static void Install(Stream payload) {
        Directory.CreateDirectory(Root);
        Stop();
        string prefix = Path.GetFullPath(Root) + Path.DirectorySeparatorChar;
        using (var archive = new ZipArchive(payload, ZipArchiveMode.Read)) {
            foreach (ZipArchiveEntry item in archive.Entries) {
                string target = Path.GetFullPath(Path.Combine(Root, item.FullName));
                if (!target.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) throw new IOException("安装包路径无效。");
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                using (Stream source = item.Open())
                using (Stream output = File.Create(target)) source.CopyTo(output);
            }
        }
        string state = Path.Combine(Root, "state.json");
        if (!File.Exists(state)) File.Copy(Path.Combine(Root, "defaults.json"), state);
        Shortcut(Startup, "--start");
        Directory.CreateDirectory(Menu);
        Shortcut(Path.Combine(Menu, "启动额度显示.lnk"), "--start");
        Shortcut(Path.Combine(Menu, "卸载额度显示.lnk"), "--uninstall");
        using (RegistryKey key = Registry.CurrentUser.CreateSubKey(RegistryPath)) {
            key.SetValue("DisplayName", "Codex 额度显示");
            key.SetValue("DisplayVersion", "1.0.0");
            key.SetValue("Publisher", "本地额度显示工具");
            key.SetValue("InstallLocation", Root);
            key.SetValue("DisplayIcon", Exe);
            key.SetValue("UninstallString", "\"" + Exe + "\" --uninstall");
            key.SetValue("NoModify", 1, RegistryValueKind.DWord);
            key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
        }
        Start();
        File.WriteAllText(Path.Combine(Root, "installed.txt"), "1.0.0\r\n" + DateTime.Now.ToString("s"));
    }

    static void Shortcut(string path, string args) {
        Type type = Type.GetTypeFromProgID("WScript.Shell");
        object shell = Activator.CreateInstance(type);
        object link = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
        try {
            Type lt = link.GetType();
            lt.InvokeMember("TargetPath", BindingFlags.SetProperty, null, link, new object[] { Exe });
            lt.InvokeMember("Arguments", BindingFlags.SetProperty, null, link, new object[] { args });
            lt.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, link, new object[] { Root });
            lt.InvokeMember("WindowStyle", BindingFlags.SetProperty, null, link, new object[] { 7 });
            lt.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
        } finally { Marshal.FinalReleaseComObject(link); Marshal.FinalReleaseComObject(shell); }
    }

    static void Start() {
        var info = new ProcessStartInfo(Path.Combine(Root, @"runtime\pythonw.exe"), "-I -B \"" + Path.Combine(Root, "entry.py") + "\"");
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.WindowStyle = ProcessWindowStyle.Hidden;
        info.WorkingDirectory = Root;
        using (Process child = Process.Start(info)) {
            if (child.WaitForExit(1500) && child.ExitCode != 0) throw new IOException("启动失败，请查看 runtime-error.log。");
        }
    }

    static void Uninstall(bool silent) {
        if (!silent && MessageBox.Show("卸载额度显示及其位置设置？", "Codex 额度显示", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
        Stop();
        if (File.Exists(Startup)) File.Delete(Startup);
        foreach (string name in new [] { "启动额度显示.lnk", "卸载额度显示.lnk" }) {
            string link = Path.Combine(Menu, name);
            if (File.Exists(link)) File.Delete(link);
        }
        if (Directory.Exists(Menu) && Directory.GetFileSystemEntries(Menu).Length == 0) Directory.Delete(Menu);
        Registry.CurrentUser.DeleteSubKeyTree(RegistryPath, false);
        string helper = Path.Combine(Path.GetTempPath(), "CodexQuotaCleanup-" + Guid.NewGuid().ToString("N") + ".exe");
        File.Copy(Exe, helper);
        Process.Start(new ProcessStartInfo(helper, "--cleanup " + Process.GetCurrentProcess().Id) { UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden });
    }

    static void Cleanup(int parent) {
        try { using (Process p = Process.GetProcessById(parent)) p.WaitForExit(30000); } catch (ArgumentException) { }
        string target = Path.GetFullPath(Root);
        string allowed = Path.GetFullPath(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CodexQuotaWidget"));
        if (!string.Equals(target, allowed, StringComparison.OrdinalIgnoreCase)) throw new IOException("卸载路径无效。");
        // Delete only this application's fixed installation directory, never traverse links.
        if (Directory.Exists(target)) DeleteTree(target);
    }

    static void DeleteTree(string path) {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) { Directory.Delete(path); return; }
        foreach (string file in Directory.GetFiles(path)) File.Delete(file);
        foreach (string dir in Directory.GetDirectories(path)) DeleteTree(dir);
        Directory.Delete(path);
    }
}
