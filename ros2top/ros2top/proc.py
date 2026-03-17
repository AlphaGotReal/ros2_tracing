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

"""Read process and system metrics from /proc filesystem."""

import os

PAGE_SIZE = os.sysconf('SC_PAGE_SIZE')
CLK_TCK = os.sysconf('SC_CLK_TCK')
NUM_CPUS = os.sysconf('SC_NPROCESSORS_ONLN')


def meminfo():
    """Return (total_bytes, available_bytes)."""
    total = avail = 0
    with open('/proc/meminfo') as fh:
        for line in fh:
            if line.startswith('MemTotal:'):
                total = int(line.split()[1]) * 1024
            elif line.startswith('MemAvailable:'):
                avail = int(line.split()[1]) * 1024
                break
    return total, avail


def cpu_total():
    """Return (total_ticks, idle_ticks) from aggregate cpu line."""
    with open('/proc/stat') as fh:
        parts = fh.readline().split()
    vals = [int(v) for v in parts[1:]]
    total = sum(vals)
    idle = vals[3] + vals[4]  # idle + iowait
    return total, idle


def statm(pid):
    """Return (virt, res, shr) in bytes, or None on failure."""
    try:
        with open(f'/proc/{pid}/statm') as fh:
            p = fh.read().split()
        return (int(p[0]) * PAGE_SIZE,
                int(p[1]) * PAGE_SIZE,
                int(p[2]) * PAGE_SIZE)
    except (OSError, IndexError):
        return None


def stat(pid):
    """Return (utime, stime, threads, blkio_ticks), or None."""
    try:
        with open(f'/proc/{pid}/stat') as fh:
            raw = fh.read()
        # fields after "(comm) " — find last ')' to handle
        # comm fields that contain spaces or parentheses
        fields = raw[raw.rfind(')') + 2:].split()
        blkio = int(fields[39]) if len(fields) > 39 else 0
        return (int(fields[11]),   # utime
                int(fields[12]),   # stime
                int(fields[17]),   # num_threads
                blkio)
    except (OSError, IndexError, ValueError):
        return None


def cmdline(pid):
    """Return raw null-separated cmdline string."""
    try:
        with open(f'/proc/{pid}/cmdline') as fh:
            return fh.read()
    except OSError:
        return ''


def is_ros2(pid):
    """Check if PID is a ROS 2 process via cmdline or loaded libs."""
    cmd = cmdline(pid)
    if not cmd:
        return False
    if '--ros-args' in cmd:
        return True
    try:
        with open(f'/proc/{pid}/maps') as fh:
            for line in fh:
                if 'librcl.so' in line:
                    return True
    except OSError:
        pass
    return False


def all_pids():
    """Return list of all numeric PIDs from /proc."""
    return [int(e) for e in os.listdir('/proc')
            if e.isdigit()]
