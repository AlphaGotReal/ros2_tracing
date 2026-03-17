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

"""ROS 2 node discovery, PID mapping, and topic bandwidth tracking."""

import importlib
import os
import time
from dataclasses import dataclass
from threading import Lock, Thread
from typing import List

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from rclpy.serialization import serialize_message

from . import proc

SELF_NODE = '_ros2top'

_BW_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    durability=DurabilityPolicy.VOLATILE,
)


@dataclass
class NodeInfo:
    """Per-process metrics for a ROS 2 node."""

    pid: int
    name: str
    virt: int = 0
    res: int = 0
    shr: int = 0
    threads: int = 0
    cpu_pct: float = 0.0
    io_pct: float = 0.0


@dataclass
class TopicInfo:
    """Bandwidth measurement for a single topic."""

    name: str
    type_name: str
    bw: float = 0.0  # bytes/sec


@dataclass
class Snapshot:
    """Point-in-time view of the entire ROS 2 stack."""

    nodes: List[NodeInfo]
    topics: List[TopicInfo]
    total_cpu: float
    mem_used: int
    mem_total: int
    total_bw: float
    node_count: int


def _import_msg(type_str):
    """Import message class from 'pkg/msg/Type' string."""
    parts = type_str.split('/')
    mod = importlib.import_module(f'{parts[0]}.{parts[1]}')
    return getattr(mod, parts[2])


def _extract_name(raw_cmd):
    """Extract a node label from raw null-separated cmdline."""
    parts = raw_cmd.split('\x00')
    for p in parts:
        if p.startswith('__node:='):
            return p.split('=', 1)[1]
    exe = parts[0] if parts else ''
    if 'python' in exe and len(parts) > 1:
        exe = parts[1]
    return os.path.basename(exe) or 'unknown'


class Monitor:
    """Collect ROS 2 node metrics and topic bandwidth."""

    def __init__(self):
        self._node = rclpy.create_node(SELF_NODE)
        self._exec = SingleThreadedExecutor()
        self._exec.add_node(self._node)
        self._thread = Thread(target=self._spin, daemon=True)

        self._pid_cache = {}   # pid -> is_ros2
        self._prev_stat = {}   # pid -> (utime, stime, blkio)
        self._prev_time = time.monotonic()
        self._prev_cpu = proc.cpu_total()

        self._subs = {}        # topic -> subscription
        self._bw = {}          # topic -> tracking dict
        self._bw_lock = Lock()

    def _spin(self):
        self._exec.spin()

    def start(self):
        """Start the background executor thread."""
        self._thread.start()

    def shutdown(self):
        """Stop executor and destroy the monitoring node."""
        self._exec.shutdown()
        self._node.destroy_node()

    def snapshot(self):
        """Collect and return a Snapshot of all metrics."""
        now = time.monotonic()
        dt = max(now - self._prev_time, 0.01)

        total_cpu = self._calc_cpu()

        mem_total, mem_avail = proc.meminfo()
        mem_used = mem_total - mem_avail

        nodes = self._collect_nodes(dt)

        self._refresh_topics()
        topics = self._calc_bw()
        total_bw = sum(t.bw for t in topics)

        graph = self._node.get_node_names_and_namespaces()
        n_nodes = sum(
            1 for n, _ in graph if n != SELF_NODE
        )

        self._prev_time = now

        return Snapshot(
            nodes=nodes,
            topics=topics,
            total_cpu=total_cpu,
            mem_used=mem_used,
            mem_total=mem_total,
            total_bw=total_bw,
            node_count=n_nodes,
        )

    def _calc_cpu(self):
        cur = proc.cpu_total()
        d_total = cur[0] - self._prev_cpu[0]
        d_idle = cur[1] - self._prev_cpu[1]
        self._prev_cpu = cur
        if d_total <= 0:
            return 0.0
        return (1.0 - d_idle / d_total) * 100.0

    def _collect_nodes(self, dt):
        """Scan /proc for ROS 2 processes and compute metrics."""
        my_pid = os.getpid()
        pids = proc.all_pids()
        alive = set(pids)

        ros2_pids = []
        for pid in pids:
            if pid == my_pid:
                continue
            if pid not in self._pid_cache:
                self._pid_cache[pid] = proc.is_ros2(pid)
            if self._pid_cache[pid]:
                ros2_pids.append(pid)

        # prune dead pids from cache
        for pid in list(self._pid_cache):
            if pid not in alive:
                del self._pid_cache[pid]
                self._prev_stat.pop(pid, None)

        # graph node names for label matching
        graph = self._node.get_node_names_and_namespaces()
        name_map = {}
        for name, ns in graph:
            if name == SELF_NODE:
                continue
            path = ns.rstrip('/') + '/' + name
            name_map[name] = path

        clk = proc.CLK_TCK
        nodes = []
        for pid in ros2_pids:
            sm = proc.statm(pid)
            st = proc.stat(pid)
            if sm is None or st is None:
                continue

            utime, stime, thr, blkio = st
            virt, res, shr = sm

            cpu_pct = io_pct = 0.0
            prev = self._prev_stat.get(pid)
            if prev:
                du = (utime + stime) - (prev[0] + prev[1])
                db = blkio - prev[2]
                wall = dt * clk
                if wall > 0:
                    cpu_pct = du / wall * 100.0
                    io_pct = db / wall * 100.0

            self._prev_stat[pid] = (utime, stime, blkio)

            label = _extract_name(proc.cmdline(pid))
            name = name_map.get(label, label)

            nodes.append(NodeInfo(
                pid=pid,
                name=name,
                virt=virt,
                res=res,
                shr=shr,
                threads=thr,
                cpu_pct=max(0.0, cpu_pct),
                io_pct=max(0.0, io_pct),
            ))

        return nodes

    def _refresh_topics(self):
        """Subscribe to new topics, remove stale ones."""
        current = {}
        for name, types in \
                self._node.get_topic_names_and_types():
            if types:
                current[name] = types[0]

        # remove stale subscriptions
        for topic in list(self._subs):
            if topic not in current:
                self._node.destroy_subscription(
                    self._subs.pop(topic))
                with self._bw_lock:
                    self._bw.pop(topic, None)

        # add new subscriptions
        for topic, type_str in current.items():
            if topic in self._subs:
                continue
            try:
                msg_cls = _import_msg(type_str)
            except (ModuleNotFoundError,
                    AttributeError, ImportError):
                continue

            with self._bw_lock:
                self._bw[topic] = {
                    'bytes': 0,
                    'prev_b': 0,
                    'prev_t': time.monotonic(),
                    'type': type_str,
                    'rate': 0.0,
                }

            def _make_cb(t):
                def cb(msg):
                    nb = len(serialize_message(msg))
                    with self._bw_lock:
                        if t in self._bw:
                            self._bw[t]['bytes'] += nb
                return cb

            sub = self._node.create_subscription(
                msg_cls, topic, _make_cb(topic), _BW_QOS,
            )
            self._subs[topic] = sub

    def _calc_bw(self):
        """Compute bandwidth for all tracked topics."""
        now = time.monotonic()
        result = []
        with self._bw_lock:
            for topic, d in self._bw.items():
                dt = now - d['prev_t']
                if dt > 0:
                    d['rate'] = \
                        (d['bytes'] - d['prev_b']) / dt
                d['prev_b'] = d['bytes']
                d['prev_t'] = now
                result.append(TopicInfo(
                    name=topic,
                    type_name=d['type'],
                    bw=max(0.0, d['rate']),
                ))
        return result
