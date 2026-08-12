# GPU Setup & Tensor Parallelism Guide

This guide explains how to set up and verify tensor parallelism across all available GPUs.

## Hardware: Your Setup

You have **4 NVIDIA L4 GPUs** available:
- GPU 0: NVIDIA L4 (23 GB VRAM)
- GPU 1: NVIDIA L4 (23 GB VRAM)
- GPU 2: NVIDIA L4 (23 GB VRAM)
- GPU 3: NVIDIA L4 (23 GB VRAM)
- **Total**: 92 GB VRAM across 4 GPUs

## What is Tensor Parallelism?

**Tensor Parallelism** splits a neural network model's tensors across multiple GPUs:

- Without TP: One GPU holds the entire model (bottleneck)
- With TP-4: Model layers are split across 4 GPUs, all working in parallel

For your Qwen3-Coder-30B model (~60GB):
- **Without TP**: Would need 1 GPU with 60GB VRAM (not available)
- **With TP-4**: Each GPU holds ~15GB model + ~8GB activations ✓ Fits easily

### Benefits
✅ Model fits in memory
✅ All 4 GPUs work simultaneously
✅ 3-4x faster inference throughput
✅ Balanced load across GPUs

## Starting vLLM with Tensor Parallelism

### Option 1: Using the Provided Script (Recommended)

```bash
cd agentic-extraction-engine
chmod +x start_vllm.sh
./start_vllm.sh
```

This automatically:
1. Detects all 4 GPUs
2. Starts vLLM with `--tensor-parallel-size 4`
3. Enables 90% GPU memory utilization (conservative)
4. Verifies server is ready
5. Displays GPU activation status

### Option 2: Manual vLLM Startup

```bash
python -m vllm.entrypoints.openai.api_server \
    --model QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8 \
    --quantization AWQ \
    --tensor-parallel-size 4 \
    --gpu-memory-utilization 0.9 \
    --max-model-len 65536 \
    --host 0.0.0.0 \
    --port 8000
```

### Option 3: Custom GPU Selection

```bash
# Only use GPUs 0,1,2 (skip GPU 3)
CUDA_VISIBLE_DEVICES=0,1,2 ./start_vllm.sh

# Only use GPU 0 (single GPU, for testing)
CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
    --model QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8 \
    --quantization AWQ
```

## Verifying Tensor Parallelism is Working

### 1. Check vLLM Startup Log

The `start_vllm.sh` script prints GPU activation:

```
GPU Utilization Check:
  GPU 0: 45% used ✓ ACTIVE
  GPU 1: 42% used ✓ ACTIVE
  GPU 2: 44% used ✓ ACTIVE
  GPU 3: 41% used ✓ ACTIVE
```

If all show `✓ ACTIVE` with >10% memory, tensor parallelism is working.

### 2. Real-Time Monitoring

While pipeline is running:

```bash
watch -n 1 nvidia-smi
```

Look for:
- All 4 GPUs have GPU-Util > 0% (not just one)
- All 4 show vLLM process (python entries)
- Memory usage spread across all 4 GPUs

Example output:
```
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 535.136.01             Driver Version: 535.136.01               |
|-------------------------------+----------------------+----------------------+
| GPU  Name         Persistence-M| Bus-Id        Disp.A | Volatile GPU-Util  |
|===============================+======================+======================|
|   0  NVIDIA L4             On   | 00:1E.0       Off |                  45% |
|   1  NVIDIA L4             On   | 00:1F.0       Off |                  43% |
|   2  NVIDIA L4             On   | 00:20.0       Off |                  44% |
|   3  NVIDIA L4             On   | 00:21.0       Off |                  42% |
+-------------------------------+----------------------+----------------------+
```

All GPUs should have similar utilization (~40-50% during inference).

### 3. Check vLLM Process

```bash
ps aux | grep vllm | grep -v grep
```

Should show:
```
python -m vllm.entrypoints.openai.api_server --model ... --tensor-parallel-size 4 ...
```

The key part: `--tensor-parallel-size 4`

### 4. vLLM HTTP API Check

```bash
# List available models (should show tensor parallelism in config)
curl http://localhost:8000/v1/models

# Make a test inference
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }'
```

### 5. Pipeline Monitoring During Execution

The orchestrator automatically monitors GPU utilization:

```bash
python orchestrator.py --phase 1-2
```

Displays during execution:
```
========== GPU Status (2026-08-12T20:15:30) ==========
GPU 0: NVIDIA L4
  Utilization: 48.5% | Memory:   12456 /    23034 MB (54.0%) | Temp: 45.2°C | ✓ ACTIVE
GPU 1: NVIDIA L4
  Utilization: 47.2% | Memory:   11890 /    23034 MB (51.6%) | Temp: 44.8°C | ✓ ACTIVE
GPU 2: NVIDIA L4
  Utilization: 49.1% | Memory:   12678 /    23034 MB (55.0%) | Temp: 46.1°C | ✓ ACTIVE
GPU 3: NVIDIA L4
  Utilization: 46.8% | Memory:   12234 /    23034 MB (53.1%) | Temp: 45.5°C | ✓ ACTIVE
-----------------------------------------------------
Aggregate: 4/4 GPUs active | Avg Util: 47.9% | Total Memory: 49258 / 92136 MB
✓ All GPUs are active (tensor parallelism working)
```

## Troubleshooting

### Issue: Only 1 GPU is active

**Symptom**: Only GPU 0 shows utilization, others are idle

**Cause**: Tensor parallelism not enabled or vLLM started without `--tensor-parallel-size`

**Fix**:
```bash
# Kill the current vLLM process
pkill -f vllm

# Start with explicit tensor parallelism
./start_vllm.sh
```

### Issue: vLLM crashes with CUDA errors

**Symptom**: `CUDA out of memory` or `CUDA device not available`

**Cause**: GPU memory too low or tensor parallelism split doesn't fit

**Fix**:
1. Lower `--gpu-memory-utilization` (default 0.9):
   ```bash
   GPU_MEMORY_UTILIZATION=0.8 ./start_vllm.sh
   ```

2. Use smaller tensor parallelism (if not all GPUs needed):
   ```bash
   CUDA_VISIBLE_DEVICES=0,1,2 python -m vllm.entrypoints.openai.api_server \
       --model QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8 \
       --tensor-parallel-size 3
   ```

### Issue: Imbalanced GPU utilization

**Symptom**: GPU 0 at 80%, others at 40%

**Cause**: Model layer distribution uneven or one GPU is bottleneck

**Fix**:
1. Verify all GPUs have similar memory:
   ```bash
   nvidia-smi --query-gpu=index,memory.total --format=csv,noheader
   ```

2. Try with `--distributed-executor-backend ray`:
   ```bash
   ./start_vllm.sh  # Already uses ray by default if available
   ```

### Issue: "vLLM server failed to start"

**Symptom**: `vLLM server is not responding` after 60 seconds

**Cause**: Server crashed or hung during initialization

**Fix**:
1. Check logs:
   ```bash
   tail -50 logs/vllm_*.log
   ```

2. Reduce model size or increase timeout:
   ```bash
   MAX_MODEL_LEN=32768 ./start_vllm.sh
   ```

## Performance Expectations

With tensor parallelism across 4 L4 GPUs:

| Metric | Expected |
|--------|----------|
| Time per inference (128 tokens) | 2-4 seconds |
| Throughput (requests/sec) | 0.25-0.5 |
| GPU utilization (during inference) | 40-50% per GPU |
| Memory per GPU | 50-60% of 23 GB |
| Total model throughput | 2-5 docs/sec (Phases 1-3) |

Phases 1 & 3 (code generation, quality evaluation) are vLLM-heavy.
Phase 4 (deterministic execution) uses zero vLLM (CPU-only).

## Configuration Parameters

In `start_vllm.sh` or environment:

```bash
# GPU parallelism strategy
NUM_GPUS=4                          # Number of GPUs for tensor parallelism
TENSOR_PARALLEL_SIZE=4              # Same as NUM_GPUS (vLLM param)
PIPELINE_PARALLEL_SIZE=1            # Keep at 1 for single model

# Memory management
GPU_MEMORY_UTILIZATION=0.9          # Use 90% of GPU VRAM (conservative: 0.8)
MAX_MODEL_LEN=65536                 # Max context length (lower for more vram margin)
SWAP_SPACE=4                        # Swap space in GB (CPU fallback)

# Model configuration
DTYPE=auto                          # auto, float16, bfloat16
QUANTIZATION=AWQ                    # int4, int8, AWQ, etc.
SEED=0                              # For determinism

# API configuration
API_HOST=0.0.0.0                    # Accessible from any network interface
API_PORT=8000                       # Default OpenAI-compatible port
```

## Verifying GPU Utilization During Pipeline

The orchestrator includes built-in GPU monitoring:

```python
from gpu_monitor import create_monitor

monitor = create_monitor()
monitor.print_status()  # Display current GPU stats
summary = monitor.get_summary()  # Get historical averages
```

## Advanced: Manual GPU Monitoring Script

```bash
#!/bin/bash
# Monitor GPUs during pipeline execution
watch -n 1 'echo "=== GPU Status ===" && \
nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu \
           --format=csv,noheader,nounits | \
awk -F"," "{
    idx=\$1; util=\$3; mem_used=\$4; mem_total=\$5; temp=\$6;
    mem_pct=int((mem_used/mem_total)*100);
    printf \"GPU %s: %3d%% util | %5d/%5d MB (%3d%%) | %5.1fC\n\", idx, util, mem_used, mem_total, mem_pct, temp
}"'
```

## Next Steps

1. ✅ Start vLLM with tensor parallelism: `./start_vllm.sh`
2. ✅ Verify all 4 GPUs are active
3. ✅ Run pipeline: `python orchestrator.py --phase 1-2`
4. ✅ Monitor GPU utilization during execution
5. ✅ Check summary at end of pipeline

All 4 GPUs should remain active throughout Phases 1-3!
