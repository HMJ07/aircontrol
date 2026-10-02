import ctypes
import os
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.OpenProcess.restype = wintypes.HANDLE
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _make_dpi_aware():
    """Sin esto Windows escala las coordenadas en pantallas con zoom y el cursor cae en otro sitio."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


_make_dpi_aware()


def _exe_of(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        return os.path.basename(buf.value) if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(handle)


def focus_app(exe_name):
    """Trae al primer plano la primera ventana visible con título de ese ejecutable. False si no hay ninguna."""
    found = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 and _exe_of(hwnd).lower() == exe_name.lower():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                                   # SW_RESTORE
    if not user32.SetForegroundWindow(hwnd):                         # Windows restringe quién puede robar el foco:
        fg = user32.GetForegroundWindow()                            # se engancha al hilo de la ventana actual
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        me = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(me, fg_thread, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(me, fg_thread, False)
    return user32.GetForegroundWindow() == hwnd


def screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def active_app():
    """Nombre del ejecutable de la ventana en primer plano (p. ej. 'chrome.exe')."""
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)
