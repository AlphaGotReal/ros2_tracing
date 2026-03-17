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

"""Entry point for ros2top — htop-like ROS 2 node monitor."""

import curses
import sys

import rclpy

from .monitor import Monitor
from .tui import init_colors, render


def main():
    """Initialize ROS 2, launch monitor, and run curses TUI."""
    try:
        rclpy.init()
    except Exception as e:
        print(f'Failed to init ROS 2: {e}')
        print('Is your ROS 2 workspace sourced?')
        sys.exit(1)

    mon = Monitor()
    mon.start()
    try:
        curses.wrapper(lambda stdscr: _run(stdscr, mon))
    except KeyboardInterrupt:
        pass
    finally:
        mon.shutdown()
        rclpy.shutdown()


def _run(stdscr, mon):
    init_colors()
    curses.curs_set(0)
    stdscr.timeout(1000)

    state = {
        'sort_key': 'cpu',
        'node_scroll': 0,
        'topic_scroll': 0,
        'focus': 'nodes',
        'peak_bw': 1024.0,
    }

    sort_keys = {
        ord('c'): 'cpu',
        ord('m'): 'mem',
        ord('n'): 'name',
        ord('p'): 'pid',
        ord('i'): 'io',
    }

    while True:
        snap = mon.snapshot()
        render(stdscr, snap, state)

        key = stdscr.getch()
        if key in (ord('q'), ord('Q')):
            break
        elif key == curses.KEY_RESIZE:
            stdscr.clear()
        elif key == curses.KEY_UP:
            _scroll(state, -1, snap)
        elif key == curses.KEY_DOWN:
            _scroll(state, 1, snap)
        elif key == ord('\t'):
            state['focus'] = (
                'topics' if state['focus'] == 'nodes'
                else 'nodes'
            )
        elif key in sort_keys:
            state['sort_key'] = sort_keys[key]


def _scroll(state, delta, snap):
    if state['focus'] == 'nodes':
        k = 'node_scroll'
        cap = max(0, len(snap.nodes) - 1)
    else:
        k = 'topic_scroll'
        cap = max(0, len(snap.topics) - 1)
    state[k] = max(0, min(state[k] + delta, cap))
