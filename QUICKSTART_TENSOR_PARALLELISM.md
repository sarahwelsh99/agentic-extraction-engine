# Quick Start: Tensor Parallelism Setup

This is a fast reference for enabling tensor parallelism across your 4 NVIDIA L4 GPUs.

## In 3 Commands

```bash
# 1. Start vLLM with tensor parallelism (from agentic-extraction-engine repo)
./start_vllm.sh

# 2. In another terminal, verify all 4 GPUs are active
nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total \
            --format=csv,noheader,nounits | \
    awk -F',' '{printf "GPU %s: %3d%% util | %6.0f / %6.0f MB\n", $1, $2, $3, $4}'

# 3. Run the pipeline (automatically monitors GPUs)
python orchestrator.py --phase 1-2
```

## What It Does

`./start_vllm.sh` automatically:
- ✅ Detects 4 GPUs
- ✅ Enables `--tensor-parallel-size 4` (splits model across all 4 GPUs)
- ✅ Sets GPU memory utilization to 90% (conservative)
- ✅ Verifies server is responding
- ✅ Displays GPU activation status

## Verify It's Working

### After starting vLLM
Look for this in the output:
```
GPU Utilization Check:
  GPU 0: 45% used ✓ ACTIVE
  GPU 1: 42% used ✓ ACTIVE
  GPU 2: 44% used ✓ ACTIVE
  GPU 3: 41% used ✓ ACTIVE
```

All 4 should say `✓ ACTIVE`.

### During pipeline execution
```bash
python orchestrator.py --phase 1
# Shows GPU status every minute:
# ✓ All GPUs are active (tensor parallelism working)
# Avg Util: 47.9% | Total Memory: 49258 / 92136 MB
```

### Manual check
```bash
watch -n 1 nvidia-smi
```
All 4 GPUs should have:
- GPU-Util: 40-50% (similar across all 4)
- Memory: 50-60% (balanced)

## Expected Performance

| Metric | Value |
|--------|-------|
| Inference time per request | 2-4 seconds |
| Throughput (Phase 1-3) | 2-5 docs/sec |
| GPU utilization during inference | 40-50% each |
| Memory per GPU | ~12 GB / 23 GB |
| All GPUs active | ✓ Yes |

## If Something Goes Wrong

### Only 1 GPU is active
```bash
pkill -f vllm
./start_vllm.sh  # Restart
```

### CUDA out of memory
```bash
GPU_MEMORY_UTILIZATION=0.8 ./start_vllm.sh
```

### Imbalanced utilization (one GPU at 80%, others at 40%)
This is expected behavior initially. Give it 30 seconds to settle.
If it persists, see [GPU_SETUP.md](extraction/docs/GPU_SETUP.md#issue-imbalanced-gpu-utilization).

## Environment Variables

```bash
# Custom GPU utilization
GPU_MEMORY_UTILIZATION=0.9 ./start_vllm.sh

# Specific GPUs only (skip GPU 3)
CUDA_VISIBLE_DEVICES=0,1,2 ./start_vllm.sh

# Longer context length (more memory)
MAX_MODEL_LEN=65536 ./start_vllm.sh

# Lower context (lower memory, if needed)
MAX_MODEL_LEN=32768 ./start_vllm.sh
```

## Next Steps

1. Start vLLM: `./start_vllm.sh`
2. Verify GPUs in another terminal: `nvidia-smi`
3. Run pipeline: `python orchestrator.py --phase 1-2`
4. Check GPU summary at end of output

Full details: [GPU_SETUP.md](extraction/docs/GPU_SETUP.md)
