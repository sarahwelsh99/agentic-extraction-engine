"""GPU monitoring and utilization tracking.

Monitors GPU utilization to ensure tensor parallelism is working correctly
and all GPUs are being utilized during pipeline execution.
"""
import logging
import subprocess
import json
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class GPUMonitor:
    """Monitor GPU utilization and tensor parallelism status."""

    def __init__(self):
        self.num_gpus = self._detect_gpus()
        self.stats_history: List[Dict] = []

    def _detect_gpus(self) -> int:
        """Detect number of available GPUs."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--list-gpus"],
                capture_output=True,
                text=True,
                timeout=5
            )
            num_gpus = len(result.stdout.strip().split('\n'))
            logger.info(f"Detected {num_gpus} GPUs")
            return num_gpus
        except Exception as e:
            logger.error(f"Failed to detect GPUs: {e}")
            return 0

    def get_gpu_stats(self) -> Dict[str, any]:
        """Get current GPU utilization stats.

        Returns:
            Dict with per-GPU and aggregate stats
        """
        try:
            # Query: index, name, temperature, memory.used, memory.total, utilization.gpu
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,temperature.gpu,memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits"
                ],
                capture_output=True,
                text=True,
                timeout=5
            )

            gpus = []
            total_memory_used = 0
            total_memory = 0
            total_gpu_util = 0
            active_gpus = 0

            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                parts = [p.strip() for p in line.split(',')]
                gpu_idx = int(parts[0])
                gpu_name = parts[1]
                temp = float(parts[2])
                mem_used = float(parts[3])
                mem_total = float(parts[4])
                gpu_util = float(parts[5])

                gpus.append({
                    "index": gpu_idx,
                    "name": gpu_name,
                    "temperature_c": temp,
                    "memory_used_mb": mem_used,
                    "memory_total_mb": mem_total,
                    "memory_percent": (mem_used / mem_total * 100) if mem_total > 0 else 0,
                    "gpu_utilization_percent": gpu_util,
                    "active": gpu_util > 10 or mem_used > mem_total * 0.1  # Active if >10% util or >10% mem
                })

                total_memory_used += mem_used
                total_memory += mem_total
                total_gpu_util += gpu_util
                if gpus[-1]["active"]:
                    active_gpus += 1

            stats = {
                "timestamp": datetime.now().isoformat(),
                "num_gpus_total": self.num_gpus,
                "num_gpus_active": active_gpus,
                "gpus": gpus,
                "aggregate": {
                    "total_memory_used_mb": total_memory_used,
                    "total_memory_mb": total_memory,
                    "total_memory_percent": (total_memory_used / total_memory * 100) if total_memory > 0 else 0,
                    "average_gpu_utilization_percent": total_gpu_util / max(self.num_gpus, 1),
                    "all_gpus_active": active_gpus == self.num_gpus
                }
            }

            self.stats_history.append(stats)
            return stats

        except Exception as e:
            logger.error(f"Failed to get GPU stats: {e}")
            return {
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
                "num_gpus_total": self.num_gpus
            }

    def print_status(self) -> None:
        """Print current GPU status in a human-readable format."""
        stats = self.get_gpu_stats()

        if "error" in stats:
            logger.warning(f"GPU stats unavailable: {stats['error']}")
            return

        print("\n" + "=" * 70)
        print(f"GPU Status ({stats['timestamp']})")
        print("=" * 70)

        for gpu in stats['gpus']:
            status = "✓ ACTIVE" if gpu['active'] else "⚠ IDLE"
            print(f"GPU {gpu['index']}: {gpu['name']}")
            print(f"  Utilization: {gpu['gpu_utilization_percent']:6.1f}% | "
                  f"Memory: {gpu['memory_used_mb']:8.0f} / {gpu['memory_total_mb']:8.0f} MB "
                  f"({gpu['memory_percent']:5.1f}%) | "
                  f"Temp: {gpu['temperature_c']:5.1f}°C | {status}")

        agg = stats['aggregate']
        print("-" * 70)
        print(f"Aggregate: {agg['num_gpus_active']}/{stats['num_gpus_total']} GPUs active | "
              f"Avg Util: {agg['average_gpu_utilization_percent']:6.1f}% | "
              f"Total Memory: {agg['total_memory_used_mb']:8.0f} / {agg['total_memory_mb']:8.0f} MB")

        if agg['all_gpus_active']:
            print("✓ All GPUs are active (tensor parallelism working)")
        else:
            print(f"⚠ Only {agg['num_gpus_active']} of {stats['num_gpus_total']} GPUs active")

        print("=" * 70 + "\n")

    def get_summary(self) -> Dict[str, any]:
        """Get summary statistics from history."""
        if not self.stats_history:
            return {"error": "No history collected"}

        # Filter out error entries
        valid_stats = [s for s in self.stats_history if "error" not in s]
        if not valid_stats:
            return {"error": "No valid statistics in history"}

        gpu_utils = [s['aggregate']['average_gpu_utilization_percent'] for s in valid_stats]
        all_active = [s['aggregate']['all_gpus_active'] for s in valid_stats]

        return {
            "samples": len(valid_stats),
            "average_gpu_utilization_percent": sum(gpu_utils) / len(gpu_utils) if gpu_utils else 0,
            "max_gpu_utilization_percent": max(gpu_utils) if gpu_utils else 0,
            "min_gpu_utilization_percent": min(gpu_utils) if gpu_utils else 0,
            "percent_time_all_gpus_active": (sum(all_active) / len(all_active) * 100) if all_active else 0,
        }


def create_monitor() -> GPUMonitor:
    """Create and initialize a GPU monitor."""
    monitor = GPUMonitor()
    if monitor.num_gpus == 0:
        logger.warning("No GPUs detected")
    return monitor
