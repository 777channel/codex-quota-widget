"""A fixed, transparent quota display that follows the Codex window."""

import atexit
import ctypes
from ctypes import wintypes
import json
import math
import msvcrt
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
import winreg
from alpha_text import AlphaText


DIRECTORY = Path(__file__).resolve().parent
STATE_FILE = DIRECTORY / "state.json"
WIDTH, HEIGHT = 214, 30
TRANSPARENT = "#010203"
UI_FONT = ("Segoe UI", -13)


def enable_dpi_awareness():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    if not user.SetProcessDpiAwarenessContext(-4):
        user.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        user.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
        if not user.SetThreadDpiAwarenessContext(-4):
            raise ctypes.WinError(ctypes.get_last_error())


def light_theme():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return True


def read_state():
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def countdown(reset_at, now=None):
    if not isinstance(reset_at, (int, float)) or not math.isfinite(reset_at):
        return "时间未知"
    seconds = max(0, math.ceil(reset_at - (time.time() if now is None else now)))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    clock = f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{days}天 {clock}" if days else clock


def remaining(window):
    if not isinstance(window, dict):
        return None
    used = window.get("usedPercent")
    if isinstance(used, bool) or not isinstance(used, (float, int)):
        return None
    if not math.isfinite(used):
        return None
    return max(0, min(100, 100 - used))


def find_codex():
    candidates = list((Path.home() / "AppData/Local/OpenAI/Codex/bin").glob("*/codex.exe"))
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    executable = shutil.which("codex")
    if not executable:
        # Fresh desktop installs may not yet have populated the CLI cache.
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
             "Get-AppxPackage -Name OpenAI.Codex | Select-Object -ExpandProperty InstallLocation"],
            capture_output=True, text=True, encoding="utf-8-sig", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for location in result.stdout.splitlines():
            candidate = Path(location.strip()) / "app/resources/codex.exe"
            if candidate.is_file():
                executable = str(candidate)
                break
    if not executable:
        raise RuntimeError("找不到本机 Codex 程序")
    return executable


class CodexWindow:
    """Read the host window bounds to position only our own overlay."""

    def __init__(self):
        self.user = ctypes.WinDLL("user32")
        self.kernel = ctypes.WinDLL("kernel32")
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.user.GetDpiForWindow.restype = wintypes.UINT
        self.user.GetDpiForSystem.restype = wintypes.UINT
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        self.user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self.user.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user.IsIconic.argtypes = [wintypes.HWND]
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback_type, wintypes.LPARAM]
        self.handle = None

    def bounds(self, handle):
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(handle, ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom

    def find(self):
        foreground = self.user.GetForegroundWindow()
        candidates = []

        def inspect(handle, unused):
            if not self.user.IsWindowVisible(handle) or self.user.IsIconic(handle):
                return True
            rect = self.bounds(handle)
            if rect is None or rect[2]-rect[0] < 500 or rect[3]-rect[1] < 300:
                return True
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            process = self.kernel.OpenProcess(0x1000, False, pid.value)
            if not process:
                return True
            try:
                path = ctypes.create_unicode_buffer(32768)
                length = wintypes.DWORD(len(path))
                found = self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length))
                if found and "openai.codex" in path.value.lower() and path.value.lower().endswith("chatgpt.exe"):
                    candidates.append((handle, rect))
            finally:
                self.kernel.CloseHandle(process)
            return True

        self.user.EnumWindows(self.callback_type(inspect), 0)
        if not candidates:
            self.handle = None
            return None, foreground
        current = next((item for item in candidates if item[0] == foreground), None)
        if current is None:
            current = next((item for item in candidates if item[0] == self.handle), None)
        if current is None:
            current = max(candidates, key=lambda item: (item[1][2]-item[1][0])*(item[1][3]-item[1][1]))
        self.handle = current[0]
        return current[1], foreground


class CodexClient:
    def __init__(self):
        self.process = None
        self.messages = queue.Queue()
        self.sequence = 0

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()

    @staticmethod
    def read_messages(process, messages):
        try:
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
        except (OSError, ValueError):
            pass
        finally:
            messages.put({"disconnected": True})

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.sequence += 1
        request = {"id": self.sequence, "method": method}
        if params is not None:
            request["params"] = params
        self.send(request)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if message.get("disconnected"):
                raise RuntimeError("Codex 连接已断开")
            if message.get("id") == self.sequence:
                if "error" in message:
                    raise RuntimeError(message["error"].get("message", "额度读取失败"))
                return message["result"]
        raise RuntimeError("额度读取超时")

    def read_limits(self):
        if self.process is None or self.process.poll() is not None:
            self.close()
            self.messages = queue.Queue()
            self.process = subprocess.Popen(
                [find_codex(), "app-server"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
            )
            threading.Thread(
                target=self.read_messages, args=(self.process, self.messages), daemon=True,
            ).start()
            self.request("initialize", {"clientInfo": {
                "name": "codex_quota_widget", "title": "Codex 额度显示", "version": "1.0.0",
            }})
            self.send({"method": "initialized"})
        result = self.request("account/rateLimits/read")
        limits = result.get("rateLimitsByLimitId") or {}
        bucket = limits.get("codex")
        if bucket is None:
            legacy = result.get("rateLimits")
            if isinstance(legacy, dict) and legacy.get("limitId") in (None, "codex"):
                bucket = legacy
        if not isinstance(bucket, dict):
            raise RuntimeError("当前账户暂无 Codex 额度数据")
        return {"primary": bucket.get("primary"), "secondary": bucket.get("secondary")}


class QuotaWidget:
    def __init__(self, root):
        self.root = root
        self.state = read_state()
        self.bucket = self.state.get("limits", {})
        self.mode = self.state.get("mode", "secondary")
        if self.mode not in ("primary", "secondary"):
            self.mode = "secondary"
        self.error = ""
        self.client = CodexClient()
        self.results = queue.Queue()
        self.refresh_event = threading.Event()
        self.stop_event = threading.Event()
        self.host_available = threading.Event()
        self.drag_offset = (0, 0)
        self.dragging = False
        self.locked = bool(self.state.get("locked", True))
        self.host = CodexWindow()
        self.dpi = self.host.user.GetDpiForSystem()
        self.scale = self.dpi / 96
        self.width = round(WIDTH * self.scale)
        self.height = round(HEIGHT * self.scale)
        self.ui_font = (UI_FONT[0], -round(abs(UI_FONT[1]) * self.scale))
        self.host_bounds = None
        self.next_host_check = 0
        self.next_theme_check = 0
        self.text_color = "#282828"
        self.position_background = "#f3f3f3"
        self.timer_text = "读取额度中…"
        self.percent_text = "—"
        self.stale = True
        self.tooltip = None
        self.tooltip_job = None
        self.closed = False
        root.title("Codex 额度显示")
        root.withdraw()
        root.overrideredirect(True)
        root.configure(bg=TRANSPARENT)
        root.attributes("-topmost", True)
        root.resizable(False, False)
        x = round(self.state.get("x", root.winfo_screenwidth() / self.scale - WIDTH - 36) * self.scale)
        y = round(self.state.get("y", root.winfo_screenheight() / self.scale - HEIGHT - 100) * self.scale)
        x = min(max(0, x), max(0, root.winfo_screenwidth() - self.width))
        y = min(max(0, y), max(0, root.winfo_screenheight() - self.height))
        root.geometry(f"{self.width}x{self.height}+{x}+{y}")
        self.canvas = tk.Canvas(root, width=self.width, height=self.height, bg=TRANSPARENT,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.end_drag)
        self.canvas.bind("<Double-Button-1>", lambda event: self.toggle_mode())
        self.canvas.bind("<Button-3>", self.show_menu)
        self.canvas.bind("<Enter>", self.schedule_tooltip)
        self.canvas.bind("<Leave>", lambda event: self.hide_tooltip())
        self.menu = tk.Menu(root, tearoff=False)
        self.apply_dpi(self.dpi)
        root.bind("<Escape>", lambda event: self.close())
        root.bind("<F5>", lambda event: self.refresh_event.set())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.update_idletasks()
        self.overlay_handle = self.host.user.GetAncestor(root.winfo_id(), 2)
        style = self.host.user.GetWindowLongPtrW(self.overlay_handle, -20)
        # Keep the display out of the taskbar and avoid taking focus from typing.
        self.host.user.SetWindowLongPtrW(self.overlay_handle, -20, style | 0x08000000 | 0x80 | 0x80000)
        self.renderer = AlphaText(self.overlay_handle)
        self.update_position_style()
        atexit.register(self.client.close)
        self.worker_thread = threading.Thread(target=self.worker, daemon=True)
        self.worker_thread.start()
        self.tick()

    def apply_dpi(self, dpi):
        self.hide_tooltip()
        self.dpi = dpi
        self.scale = dpi / 96
        self.width = round(WIDTH * self.scale)
        self.height = round(HEIGHT * self.scale)
        self.ui_font = (UI_FONT[0], -round(abs(UI_FONT[1]) * self.scale))
        self.root.geometry(f"{self.width}x{self.height}")
        self.canvas.configure(width=self.width, height=self.height)
        self.menu.configure(font=self.ui_font)

    def worker(self):
        while not self.stop_event.is_set():
            self.refresh_event.clear()
            if not self.host_available.is_set():
                self.client.close()
                self.refresh_event.wait(5)
                continue
            try:
                self.results.put((self.client.read_limits(), time.time(), ""))
            except Exception as error:
                self.results.put((None, None, str(error)))
                self.client.close()
            self.refresh_event.wait(60)
        self.client.close()

    def save(self):
        if self.closed:
            return
        self.state.update(mode=self.mode, locked=self.locked,
                          x=round(self.root.winfo_x() / self.scale),
                          y=round(self.root.winfo_y() / self.scale))
        temporary = STATE_FILE.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(STATE_FILE)
        except OSError:
            self.error = "显示位置未能保存"

    def tick(self):
        if self.closed:
            return
        while not self.results.empty():
            limits, fetched_at, self.error = self.results.get_nowait()
            if limits is not None:
                self.bucket = limits
                self.state.update(limits=limits, fetched_at=fetched_at)
                self.save()
        window = self.bucket.get(self.mode)
        value = remaining(window)
        reset = window.get("resetsAt") if isinstance(window, dict) else None
        expired = isinstance(reset, (int, float)) and reset <= time.time()
        self.stale = bool(self.error) or time.time() - self.state.get("fetched_at", 0) > 125 or expired
        self.timer_text = countdown(reset) if value is not None else "读取额度中…"
        self.percent_text = "等待刷新" if expired else (f"{value:.0f}%" if value is not None else "—")
        if time.monotonic() >= self.next_theme_check:
            light = light_theme()
            self.text_color = "#282828" if light else "#ededed"
            self.position_background = "#f3f3f3" if light else "#343434"
            self.next_theme_check = time.monotonic() + 5
        if time.monotonic() >= self.next_host_check:
            self.follow_host()
            self.next_host_check = time.monotonic() + 0.5
        self.draw()
        self.root.after(250, self.tick)

    def draw(self):
        self.renderer.render(self.timer_text, self.percent_text, self.dpi, self.text_color,
                             unlocked=not self.locked, background=self.position_background,
                             stale=self.stale)

    def follow_host(self):
        rect, foreground = self.host.find()
        self.host_bounds = rect
        available = rect is not None
        if available:
            dpi = self.host.user.GetDpiForWindow(self.host.handle)
            if dpi and dpi != self.dpi:
                self.apply_dpi(dpi)
        if available != self.host_available.is_set():
            if available:
                self.host_available.set()
            else:
                self.host_available.clear()
            self.refresh_event.set()
        own_window = self.root.winfo_id()
        own_pid = wintypes.DWORD()
        self.host.user.GetWindowThreadProcessId(foreground, ctypes.byref(own_pid))
        our_pid = wintypes.DWORD()
        self.host.user.GetWindowThreadProcessId(own_window, ctypes.byref(our_pid))
        if rect is None or (foreground != self.host.handle and own_pid.value != our_pid.value):
            self.hide_tooltip()
            self.root.withdraw()
            return
        if self.dragging:
            return
        left, top, right, bottom = rect
        offset_x = round(self.state.get("offset_right", 460) * self.scale)
        offset_y = round(self.state.get("offset_bottom", 92) * self.scale)
        margin = round(12 * self.scale)
        x = max(left+margin, min(right-self.width-margin, right-offset_x))
        y = max(top+round(40*self.scale), min(bottom-self.height-margin, bottom-offset_y))
        if self.root.state() == "withdrawn":
            self.root.deiconify()
        self.host.user.SetWindowPos(self.overlay_handle, -1, x, y, self.width, self.height, 0x10)

    def start_drag(self, event):
        self.hide_tooltip()
        if self.locked:
            return
        self.dragging = True
        self.drag_offset = (event.x, event.y)

    def end_drag(self, event):
        if not self.dragging:
            return
        self.dragging = False
        if self.host_bounds is not None:
            self.state["offset_right"] = (self.host_bounds[2] - self.root.winfo_x()) / self.scale
            self.state["offset_bottom"] = (self.host_bounds[3] - self.root.winfo_y()) / self.scale
        self.save()

    def drag(self, event):
        if self.locked or not self.dragging:
            return
        x = event.x_root - self.drag_offset[0]
        y = event.y_root - self.drag_offset[1]
        if self.host_bounds is not None:
            left, top, right, bottom = self.host_bounds
            margin = round(12 * self.scale)
            x = min(max(left+margin, x), right-self.width-margin)
            y = min(max(top+round(40*self.scale), y), bottom-self.height-margin)
        self.host.user.SetWindowPos(self.overlay_handle, -1, x, y, self.width, self.height, 0x10)

    def update_position_style(self):
        self.canvas.configure(cursor="" if self.locked else "fleur")
        self.draw()

    def toggle_lock(self):
        self.hide_tooltip()
        self.locked = not self.locked
        self.update_position_style()
        self.save()

    def toggle_mode(self):
        self.mode = "primary" if self.mode == "secondary" else "secondary"
        self.hide_tooltip()
        self.save()

    def show_menu(self, event):
        self.hide_tooltip()
        menu = self.menu
        menu.delete(0, "end")
        menu.add_command(label="调整位置" if self.locked else "固定当前位置", command=self.toggle_lock)
        menu.add_separator()
        menu.add_command(label="切换到" + (" 5 小时额度" if self.mode == "secondary" else "周额度"), command=self.toggle_mode)
        menu.add_command(label="立即刷新", command=self.refresh_event.set)
        menu.add_separator()
        updated = self.state.get("fetched_at")
        label = "上次刷新 " + time.strftime("%H:%M:%S", time.localtime(updated)) if updated else "尚未读取到额度"
        menu.add_command(label=label, state="disabled")
        if self.error:
            menu.add_command(label=self.error[:70], state="disabled")
        menu.add_separator()
        menu.add_command(label="退出", command=self.close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def schedule_tooltip(self, event):
        self.hide_tooltip()
        if not self.dragging:
            self.tooltip_job = self.root.after(650, self.show_tooltip)

    def show_tooltip(self):
        self.tooltip_job = None
        lines = []
        for key, label in (("secondary", "周额度"), ("primary", "5 小时额度")):
            window = self.bucket.get(key)
            value = remaining(window)
            if value is not None:
                lines.append(f"{label}剩余 {value:.0f}%　{countdown(window.get('resetsAt'))}后重置")
        lines.append("已固定 · 右键可调整位置" if self.locked else "拖到模型选择旁 · 右键固定当前位置")
        lines.append("双击切换周额度 / 5 小时额度")
        if self.error:
            lines.append("刷新失败，当前为上次读取值")
        self.tooltip = tk.Toplevel(self.root)
        self.tooltip.overrideredirect(True)
        self.tooltip.attributes("-topmost", True)
        tk.Label(self.tooltip, text="\n".join(lines), bg="#282828", fg="#eeeeee",
                 font=self.ui_font, justify="left",
                 padx=round(12*self.scale), pady=round(9*self.scale)).pack()
        self.tooltip.update_idletasks()
        x = min(self.root.winfo_x(), self.root.winfo_screenwidth()-self.tooltip.winfo_width()-8)
        y = max(0, self.root.winfo_y()-self.tooltip.winfo_height()-round(8*self.scale))
        self.tooltip.geometry(f"+{max(0, x)}+{y}")

    def hide_tooltip(self):
        if self.tooltip_job is not None:
            self.root.after_cancel(self.tooltip_job)
            self.tooltip_job = None
        if self.tooltip is not None:
            self.tooltip.destroy()
            self.tooltip = None

    def close(self):
        if self.closed:
            return
        self.save()
        self.closed = True
        self.hide_tooltip()
        self.stop_event.set()
        self.refresh_event.set()
        self.renderer.close()
        self.root.destroy()


def main():
    lock = (DIRECTORY / "widget.lock").open("a+b")
    lock.seek(0)
    if not lock.read(1):
        lock.write(b"1")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return
    enable_dpi_awareness()
    root = tk.Tk()
    widget = QuotaWidget(root)
    try:
        root.mainloop()
    finally:
        widget.stop_event.set()
        widget.refresh_event.set()
        widget.worker_thread.join(timeout=23)
        widget.client.close()
        lock.close()


if __name__ == "__main__":
    main()
