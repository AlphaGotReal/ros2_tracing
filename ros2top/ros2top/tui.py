# Copyright 2026 ros2_tracing contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Curses TUI renderer — htop-style layout for ROS 2 metrics."""

import curses
import socket

# color pair IDs
CP_LABEL = 1
CP_BAR = 2
CP_WARN = 3
CP_HEADER = 4
CP_SEP = 5
CP_KEY = 6
CP_NORM = 7
CP_HILITE = 8


def init_colors():
    """Initialize curses color pairs."""
    if not curses.has_colors():
        return
    curses.use_default_colors()
    curses.init_pair(CP_LABEL, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_BAR, curses.COLOR_GREEN, -1)
    curses.init_pair(CP_WARN, curses.COLOR_RED, -1)
    curses.init_pair(CP_HEADER, curses.COLOR_BLACK,
                     curses.COLOR_GREEN)
    curses.init_pair(CP_SEP, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_KEY, curses.COLOR_CYAN, -1)
    curses.init_pair(CP_NORM, curses.COLOR_WHITE, -1)
    curses.init_pair(CP_HILITE, curses.COLOR_YELLOW, -1)


# ── helpers ─────────────────────────────────────────────


def _put(win, y, x, text, attr=0):
    """Write text safely, clipping at terminal edge."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    text = text[:max(0, w - x)]
    if not text:
        return
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def _fmt_bytes(n):
    """Format byte count to human-readable string."""
    for unit in ('B', 'K', 'M', 'G', 'T'):
        if abs(n) < 1024:
            if unit == 'B':
                return f'{int(n)}{unit}'
            return f'{n:.1f}{unit}'
        n /= 1024.0
    return f'{n:.1f}P'


def _fmt_bw(bps):
    """Format bytes/sec to human-readable bandwidth."""
    for unit in ('B/s', 'KB/s', 'MB/s', 'GB/s'):
        if abs(bps) < 1024:
            return f'{bps:.1f} {unit}'
        bps /= 1024.0
    return f'{bps:.1f} TB/s'


# ── bar widget ──────────────────────────────────────────


def _draw_bar(win, y, x, label, pct, text, width):
    """Draw htop-style bar:  LABEL [|||||||||  TEXT]."""
    c_lbl = curses.color_pair(CP_LABEL) | curses.A_BOLD
    c_fill = curses.color_pair(CP_BAR) | curses.A_BOLD
    c_norm = curses.color_pair(CP_NORM)
    _, scr_w = win.getmaxyx()

    lbl = f' {label} '
    _put(win, y, x, lbl, c_lbl)
    bx = x + len(lbl)

    _put(win, y, bx, '[', c_norm)
    bx += 1

    inner = width - bx - 1
    if inner < 5:
        _put(win, y, bx, ']', c_norm)
        return

    filled = int(inner * max(0, min(pct, 100)) / 100.0)
    padded = text.rjust(inner)

    for i in range(inner):
        sx = bx + i
        if sx >= scr_w:
            break
        ch = padded[i]
        attr = c_fill if i < filled else c_norm
        try:
            win.addch(y, sx, ch, attr)
        except curses.error:
            pass

    _put(win, y, bx + inner, ']', c_norm)


# ── separator ───────────────────────────────────────────


def _sep(win, y, w, title=None):
    """Draw a horizontal separator line."""
    attr = curses.color_pair(CP_SEP)
    if title:
        prefix = f' \u2500\u2500 {title} '
        line = prefix + '\u2500' * max(0, w - len(prefix))
    else:
        line = '\u2500' * w
    _put(win, y, 0, line[:w], attr)


# ── header ──────────────────────────────────────────────


def _draw_header(win, snap, state, w):
    """Draw CPU / MEM / NET bars + node count. Returns next y."""
    c_lbl = curses.color_pair(CP_LABEL) | curses.A_BOLD
    c_bold = curses.color_pair(CP_NORM) | curses.A_BOLD

    hostname = socket.gethostname()
    _put(win, 0, 0,
         f' ROS 2 Top \u2500 {hostname}', c_lbl)

    info = f'Nodes: {snap.node_count}'
    _put(win, 0, w - len(info) - 1, info, c_bold)

    bar_w = max(w // 2, 40)

    cpu_txt = f'{snap.total_cpu:.1f}%'
    _draw_bar(win, 1, 0, 'CPU', snap.total_cpu,
              cpu_txt, bar_w)

    mem_pct = 0.0
    if snap.mem_total > 0:
        mem_pct = snap.mem_used / snap.mem_total * 100.0
    mem_txt = (f'{_fmt_bytes(snap.mem_used)} / '
               f'{_fmt_bytes(snap.mem_total)}')
    _draw_bar(win, 2, 0, 'MEM', mem_pct, mem_txt, bar_w)

    # NET bar auto-scaled to observed peak
    peak = state.get('peak_bw', 1024.0)
    peak = max(peak, snap.total_bw, 1024.0)
    state['peak_bw'] = peak
    bw_pct = snap.total_bw / peak * 100.0
    bw_txt = _fmt_bw(snap.total_bw)
    _draw_bar(win, 3, 0, 'NET', bw_pct, bw_txt, bar_w)

    # ROS 2 stack totals on right side of header
    ros_mem = sum(n.res for n in snap.nodes)
    ros_cpu = sum(n.cpu_pct for n in snap.nodes)
    _put(win, 1, bar_w + 2,
         f'ROS CPU: {ros_cpu:5.1f}%', c_bold)
    _put(win, 2, bar_w + 2,
         f'ROS MEM: {_fmt_bytes(ros_mem):>8}', c_bold)
    _put(win, 3, bar_w + 2,
         f'ROS NET: {_fmt_bw(snap.total_bw):>12}', c_bold)

    return 4


# ── node table ──────────────────────────────────────────


SORT_MAP = {
    'cpu': lambda n: -n.cpu_pct,
    'mem': lambda n: -n.res,
    'name': lambda n: n.name.lower(),
    'pid': lambda n: n.pid,
    'io': lambda n: -n.io_pct,
}


def _draw_nodes(win, snap, state, y, rows, w):
    """Draw the per-node metrics table. Returns next y."""
    c_hdr = curses.color_pair(CP_HEADER) | curses.A_BOLD
    c_norm = curses.color_pair(CP_NORM)
    c_hi = curses.color_pair(CP_HILITE)
    c_warn = curses.color_pair(CP_WARN) | curses.A_BOLD

    hdr = (f'{"PID":>7}  {"NODE":<24} '
           f'{"VIRT":>8} {"RES":>8} {"SHR":>8} '
           f'{"THR":>4} {"CPU%":>6} {"IO%":>6}')
    _put(win, y, 0, hdr.ljust(w)[:w], c_hdr)
    y += 1

    key = state.get('sort_key', 'cpu')
    fn = SORT_MAP.get(key, SORT_MAP['cpu'])
    data = sorted(snap.nodes, key=fn)

    off = state.get('node_scroll', 0)
    vis = data[off:off + rows]

    for i, n in enumerate(vis):
        nm = n.name[:24]
        x = 0

        _put(win, y + i, x, f'{n.pid:>7}', c_norm)
        x += 9
        _put(win, y + i, x, f'{nm:<24}', c_norm)
        x += 25
        _put(win, y + i, x,
             f'{_fmt_bytes(n.virt):>8}', c_norm)
        x += 9
        _put(win, y + i, x,
             f'{_fmt_bytes(n.res):>8}', c_hi)
        x += 9
        _put(win, y + i, x,
             f'{_fmt_bytes(n.shr):>8}', c_norm)
        x += 9
        _put(win, y + i, x, f'{n.threads:>4}', c_norm)
        x += 5

        cpu_a = c_warn if n.cpu_pct > 50 else c_norm
        _put(win, y + i, x,
             f'{n.cpu_pct:>5.1f}%', cpu_a)
        x += 7

        io_a = c_warn if n.io_pct > 20 else c_norm
        _put(win, y + i, x,
             f'{n.io_pct:>5.1f}%', io_a)

    return y + rows


# ── topic table ─────────────────────────────────────────


def _draw_topics(win, snap, state, y, rows, w):
    """Draw the per-topic bandwidth table. Returns next y."""
    c_hdr = curses.color_pair(CP_HEADER) | curses.A_BOLD
    c_norm = curses.color_pair(CP_NORM)
    c_hi = curses.color_pair(CP_HILITE)

    hdr = (f'  {"TOPIC":<34} {"TYPE":<26} '
           f'{"BANDWIDTH":>12}')
    _put(win, y, 0, hdr.ljust(w)[:w], c_hdr)
    y += 1

    data = sorted(snap.topics, key=lambda t: -t.bw)
    off = state.get('topic_scroll', 0)
    vis = data[off:off + rows]

    for i, t in enumerate(vis):
        tn = t.name[:34]
        # condense 'pkg/msg/Type' -> 'pkg/Type'
        tp = t.type_name.split('/')
        short = (f'{tp[0]}/{tp[-1]}'
                 if len(tp) >= 3 else t.type_name)
        short = short[:26]
        bw = _fmt_bw(t.bw)

        _put(win, y + i, 2, f'{tn:<34}', c_norm)
        _put(win, y + i, 37, f'{short:<26}', c_norm)
        _put(win, y + i, 64, f'{bw:>12}', c_hi)

    return y + rows


# ── footer ──────────────────────────────────────────────


_HINTS = [
    ('q', 'Quit'),
    ('\u2191\u2193', 'Scroll'),
    ('Tab', 'Focus'),
    ('c', 'CPU'),
    ('m', 'MEM'),
    ('n', 'Name'),
    ('p', 'PID'),
    ('i', 'IO'),
]


def _draw_footer(win, y, w):
    """Draw key binding hints at the bottom."""
    c_key = curses.color_pair(CP_KEY) | curses.A_BOLD
    c_norm = curses.color_pair(CP_NORM)
    x = 1
    for key, desc in _HINTS:
        _put(win, y, x, key, c_key)
        x += len(key)
        _put(win, y, x, f':{desc}  ', c_norm)
        x += len(desc) + 3
        if x >= w - 10:
            break


# ── main render ─────────────────────────────────────────


def render(win, snap, state):
    """Full-screen render of the TUI."""
    win.erase()
    h, w = win.getmaxyx()

    if h < 12 or w < 60:
        _put(win, 0, 0, 'Terminal too small (min 60x12)')
        win.refresh()
        return

    y = _draw_header(win, snap, state, w)
    _sep(win, y, w)
    y += 1

    # split remaining space: 60% nodes, 40% topics
    remaining = h - y - 3
    node_rows = max(2, int(remaining * 0.6))
    topic_rows = max(2, remaining - node_rows)

    y = _draw_nodes(win, snap, state, y, node_rows, w)

    _sep(win, y, w, title='Topics')
    y += 1

    _draw_topics(win, snap, state, y, topic_rows, w)

    _draw_footer(win, h - 1, w)
    win.refresh()
