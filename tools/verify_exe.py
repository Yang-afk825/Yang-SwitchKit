"""验证打包后的 exe 能否正常启动。

不截图（避免拍到桌面上的其他窗口），只检查：
  1. 进程是否存活到指定秒数（没崩）
  2. 是否出现标题含指定关键字的顶层窗口
  3. 窗口尺寸是否合理

两个必须注意的地方：
  · PyInstaller 的 --onefile 产物是「引导进程 + 真正的应用子进程」两层结构，
    只 terminate 引导进程会留下孤儿应用进程，窗口会一直挂在桌面上。
    所以关窗要用 taskkill /T 连子树一起杀。
  · Tk 是先建一个很小的窗口、随后才应用 geometry 和 minsize，
    所以不能一看到窗口就判尺寸，要等它长到合理大小。
"""
import ctypes
import ctypes.wintypes as wt
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]

EXE = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                   else ROOT / "dist" / "YangSwitchKit.exe")

#: 用标题里的稳定片段来认窗口；改产品名时这里要跟着改
FRAGMENT = "Yang-SwitchKit"

user32 = ctypes.windll.user32
user32.GetWindowTextLengthW.restype = ctypes.c_int
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def find_windows(fragment: str):
    """返回 [(标题, 宽, 高, 进程号)]。"""
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if fragment in buf.value:
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((buf.value, r.right - r.left, r.bottom - r.top, pid.value))
        return True

    EnumWindows(EnumWindowsProc(cb), 0)
    return found


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True, check=False)


def reasonable(rows) -> bool:
    return any(w > 400 and h > 300 for _, w, h, _ in rows)


# 先清掉上一次遗留的孤儿进程，否则会误判成「有一个小窗口」
stale = find_windows(FRAGMENT)
for title, w, h, pid in stale:
    print(f"清理遗留窗口：{title!r} 尺寸 {w}x{h} pid={pid}")
    kill_tree(pid)
if stale:
    time.sleep(1.0)

print("启动：", EXE)
proc = subprocess.Popen([str(EXE)])
start = time.time()
deadline = start + 30
hits = []

while time.time() < deadline:
    time.sleep(0.5)
    if proc.poll() is not None:
        print(f"进程在第 {time.time() - start:.1f} 秒退出，返回码 {proc.returncode}")
        sys.exit(1)
    found = find_windows(FRAGMENT)
    if found:
        hits = found
    if reasonable(hits):
        break

if hits:
    biggest = max(hits, key=lambda r: r[1] * r[2])
    print(f"窗口已出现：{biggest[0]!r}  尺寸 {biggest[1]}x{biggest[2]}  "
          f"耗时 {time.time() - start:.1f}s")
    ok = reasonable(hits)
    print("尺寸合理" if ok else "尺寸异常（等待 30 秒仍偏小）")
else:
    print("进程存活但未找到窗口")

# 连子进程一起收掉，别把应用进程留在桌面上
kill_tree(proc.pid)
try:
    proc.wait(timeout=8)
except subprocess.TimeoutExpired:
    proc.kill()
time.sleep(0.5)
left = find_windows(FRAGMENT)
print("残留窗口：", len(left))
if left:
    for title, w, h, pid in left:
        kill_tree(pid)
print("已关闭")
sys.exit(0 if hits and ok else 2)
