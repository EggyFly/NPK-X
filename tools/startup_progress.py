"""Startup progress for the NPK browser.

多行帧依赖光标相对移动, 在部分终端下会错位; 因此这里只保留单行进度,
固定用 \r + 清行原地重绘, 并且整行宽度不超过终端列数, 避免自动折行.
"""

import shutil
import sys
import threading
import time

STEPS = (
    "profile",
    "pathdict",
    "catalog",
    "xn index",
    "artifacts",
)
BAR_WIDTH = 22
_active = None
_lock = threading.Lock()


def _supports_ansi() -> bool:
    return sys.stdout.isatty() and not sys.stdout.closed


def _bar(fraction: float) -> str:
    filled = max(0, min(BAR_WIDTH, int(round(fraction * BAR_WIDTH))))
    return "[" + "#" * filled + "." * (BAR_WIDTH - filled) + "]"


def _step_index(step: str) -> int:
    return STEPS.index(step) if step in STEPS else len(STEPS)


def _fraction(step: str, ratio: float = 0.0) -> float:
    index = _step_index(step)
    return min(1.0, (index + max(0.0, min(1.0, ratio))) / len(STEPS))


def _width() -> int:
    return shutil.get_terminal_size((100, 20)).columns


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _render() -> str:
    step = _active["step"]
    ratio = _active["ratio"]
    if _active["sub"]:
        sub_ratio, sub_label = _active["sub"]
        sub_text = f" >> {sub_label} {int(sub_ratio * 100):3d}%"
    else:
        sub_text = ""
    overall = _fraction(step, ratio)
    return (f"{_bar(ratio)} {int(ratio * 100):3d}% | {_active['label']}{sub_text}"
            f" | overall {int(overall * 100):3d}%")


def _repaint() -> None:
    width = _width()
    text = _render()
    # 新帧更短时旧行会残留, 垫空格; 帧本身永不超终端列数, 防止自动折行.
    if _active["width"] and len(text) < _active["width"]:
        text = text + " " * (_active["width"] - len(text))
    if len(text) >= width:
        text = text[: width - 1]
    _active["width"] = len(text)
    _write("\r\033[2K" + text)


def begin(step: str, detail: str = "") -> None:
    global _active
    with _lock:
        if not _supports_ansi():
            print(f"[{step}] {detail}" if detail else f"[{step}]", flush=True)
            return
        if _active:
            return
        _active = {"step": step, "ratio": 0.0, "label": f"{step} | {detail}".strip(), "sub": None, "width": 0}
        _repaint()


def update(step: str, done: int, total: int, detail: str = "") -> None:
    if not _supports_ansi():
        return
    with _lock:
        if not _active or _active["step"] != step or not total:
            return
        _active["ratio"] = min(1.0, done / total)
        _active["label"] = f"{step} {done}/{total} | {detail}" if detail else f"{step} {done}/{total}"
        _repaint()


def sub(step: str, done: int, total: int, detail: str = "") -> None:
    """当前步骤的内层进度, 例如逐 npk 的条目进度."""
    if not _supports_ansi():
        return
    with _lock:
        if not _active or _active["step"] != step or not total:
            return
        if detail:
            _active["sub"] = (min(1.0, done / total), f"{step} {done}/{total} | {detail}")
        else:
            _active["sub"] = None
        _repaint()


def finish(step: str, detail: str = "") -> None:
    global _active
    with _lock:
        if not _supports_ansi():
            print(f"[{step}] done {detail}" if detail else f"[{step}] done", flush=True)
            return
        if not _active or _active["step"] != step:
            return
        _write("\r\033[2K" + f"{_bar(1.0)} 100% | {step} done | {detail}"[: _width() - 1] + "\n")
        _active = None


def note(text: str) -> None:
    if not _supports_ansi():
        print(text, flush=True)
        return
    with _lock:
        _write("\r\033[2K" + text[: _width() - 1] + "\n")
        if _active:
            _active["width"] = 0
            _repaint()


class ticker:
    """每秒重绘进度行, 避免慢步骤看起来像卡住."""

    def __init__(self, step: str, detail: str = ""):
        self.step = step
        self.detail = detail
        self.started = time.time()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.wait(1.0):
            with _lock:
                if not _active or _active["step"] != self.step:
                    return
                if not self.detail:
                    return
                elapsed = int(time.time() - self.started)
                previous = _active["label"]
                _active["label"] = f"{self.step} | {self.detail} {elapsed}s"
                _repaint()
                _active["label"] = previous

    def __enter__(self):
        begin(self.step, self.detail)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
        if _supports_ansi():
            with _lock:
                if _active and _active["step"] == self.step:
                    _active["label"] = f"{self.step} | {self.detail} {int(time.time() - self.started)}s"
                    _repaint()
