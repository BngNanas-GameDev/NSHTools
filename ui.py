"""UI helpers — ANSI colors + box drawing untuk WMA & KMMA."""
import os
import sys
import ctypes


def enable_ansi():
    """Aktifkan ANSI escape codes di Windows 10+."""
    try:
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.wintypes.DWORD()
        k32.GetConsoleMode(h, ctypes.byref(mode))
        k32.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


enable_ansi()

# ── Colors ──────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
CYAN    = "\033[96m"
WHITE   = "\033[97m"
GRAY    = "\033[90m"
BG_BLUE = "\033[44m"
BG_GREEN = "\033[42m"
BG_RED  = "\033[41m"

# ── Box Drawing ─────────────────────────────────────────
def hline(w=60, ch="="):
    return ch * w

def box_top(w=60):
    return f"+{'='*(w-2)}+"

def box_mid(w=60):
    return f"+{'-'*(w-2)}+"

def box_bot(w=60):
    return f"+{'='*(w-2)}+"

def box_line(text, w=60, color=""):
    inner = w - 4
    t = text[:inner]
    pad = inner - len(_strip_ansi(t))
    line = f"| {t}{' '*pad} |"
    if color:
        return f"{color}|{RESET} {color}{t}{RESET}{' '*pad} {color}|{RESET}"
    return line

def box_line_2col(left, right, w=60, lcolor="", rcolor=""):
    inner = w - 4
    lp = len(_strip_ansi(left))
    rp = len(_strip_ansi(right))
    gap = inner - lp - rp - 2
    if gap < 0:
        gap = 0
    l = f"{lcolor}{left}{RESET}" if lcolor else left
    r = f"{rcolor}{right}{RESET}" if rcolor else right
    return f"| {l}{' '*gap}{r} |"

def box_center(text, w=60, color=""):
    inner = w - 4
    t = _strip_ansi(text)
    pad = (inner - len(t)) // 2
    pad2 = inner - len(t) - pad
    c = f"{color}{text}{RESET}" if color else text
    return f"|{' '*pad}{c}{' '*pad2}|"

def _strip_ansi(s):
    import re
    return re.sub(r'\033\[[0-9;]*m', '', s)


# ── Print Helpers ───────────────────────────────────────
def cls():
    os.system("cls")

def header(title, subtitle="", w=60):
    lines = []
    lines.append(box_top(w))
    lines.append(box_center(f"{BOLD}{WHITE}{title}{RESET}", w, BG_BLUE))
    if subtitle:
        lines.append(box_center(f"{GRAY}{subtitle}{RESET}", w))
    lines.append(box_mid(w))
    return "\n".join(lines)

def section(title, w=60):
    return f"  {CYAN}{BOLD}{title}{RESET}\n  {CYAN}{hline(w-4)}{RESET}"

def ok(text):
    return f"{GREEN}[OK]{RESET} {text}"

def err(text):
    return f"{RED}[!!]{RESET} {text}"

def warn(text):
    return f"{YELLOW}[!]{RESET} {text}"

def info(text):
    return f"{CYAN}[i]{RESET} {text}"

def dim(text):
    return f"{DIM}{text}{RESET}"

def bold(text):
    return f"{BOLD}{text}{RESET}"

def status_bar(total, pending, done, verified, w=60):
    parts = [
        f"{WHITE}Total{RESET}={total}",
        f"{YELLOW}Pending{RESET}={pending}",
        f"{GREEN}Selesai{RESET}={done}",
        f"{BLUE}Verified{RESET}={verified}",
    ]
    return f"  {'  '.join(parts)}"

def file_entry(idx, label, name, w=80):
    label_c = GREEN if label == "DEVELOP" else MAGENTA if label == "RICOH" else WHITE
    return f"  {DIM}{idx:>3}.{RESET} [{label_c}{label:7s}{RESET}] {name[:65]}"

def result_row(name, machine, operator, ok_bool, info_msg, w=80):
    mark = f"{GREEN}OK{RESET}" if ok_bool else f"{RED}!!{RESET}"
    n = name[:55]
    return f"  [{mark}] {n:55s} {machine:12s} {operator:10s}  {dim(info_msg)}"

def waiting(text="menunggu..."):
    return f"  {YELLOW}... {text}{RESET}"

def progress(current, total):
    return f"  {CYAN}[{current}/{total}]{RESET}"
