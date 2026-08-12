#!/bin/bash
"""Start vLLM with tensor parallelism across all available GPUs.

This script:
1. Detects available GPUs
2. Starts vLLM with tensor parallelism enabled
3. Verifies all GPUs are active
4. Logs throughput metrics

Usage:
    ./start_vllm.sh                    # Auto-detect all GPUs
    CUDA_VISIBLE_DEVICES=0,1,2,3 ./start_vllm.sh  # Specific GPUs
"""

set -e

# GPU Configuration
NUM_GPUS=${NUM_GPUS:-$(nvidia-smi --list-gpus | wc -l)}
MODEL=${MODEL:-"QuantTrio/Qwen3-Coder-30B-A3B-Instruct-GPTQ-Int8"}
QUANTIZATION=${QUANTIZATION:-"AWQ"}
DTYPE=${DTYPE:-"auto"}
GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-0.9}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-65536}
SWAP_SPACE=${SWAP_SPACE:-4}
ENFORCE_EAGER=${ENFORCE_EAGER:-false}
SEED=${SEED:-0}

# API Configuration
API_HOST=${API_HOST:-"0.0.0.0"}
API_PORT=${API_PORT:-8000}

# Logging
LOG_DIR=${LOG_DIR:-"./logs"}
LOG_FILE="$LOG_DIR/vllm_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

echo "Starting vLLM with Tensor Parallelism"
echo "======================================"
echo "GPUs available: $NUM_GPUS"
echo "Model: $MODEL"
echo "Tensor parallelism size: $NUM_GPUS"
echo "GPU memory utilization: $GPU_MEMORY_UTILIZATION"
echo "Max model length: $MAX_MODEL_LEN"
echo "API endpoint: http://$API_HOST:$API_PORT"
echo "Log file: $LOG_FILE"
echo ""

# Verify GPU count
if [ "$NUM_GPUS" -lt 1 ]; then
    echo "ERROR: No GPUs detected"
    exit 1
fi

# Start vLLM with tensor parallelism
python -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --quantization "$QUANTIZATION" \
    --dtype "$DTYPE" \
    --tensor-parallel-size "$NUM_GPUS" \
    --pipeline-parallel-size 1 \
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
    --max-model-len "$MAX_MODEL_LEN" \
    --swap-space "$SWAP_SPACE" \
    --enforce-eager "$ENFORCE_EAGER" \
    --seed "$SEED" \
    --host "$API_HOST" \
    --port "$API_PORT" \
    --disable-log-requests \
    --log-level INFO \
    2>&1 | tee "$LOG_FILE" &

VLLM_PID=$!
echo "vLLM PID: $VLLM_PID"

# Wait for server to be ready
echo "Waiting for vLLM server to be ready..."
for i in {1..60}; do
    if curl -s "http://localhost:$API_PORT/v1/models" > /dev/null 2>&1; then
        echo "✓ vLLM server is ready"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "✗ vLLM server failed to start after 60 seconds"
        kill $VLLM_PID 2>/dev/null || true
        exit 1
    fi
    echo "  Waiting... ($i/60)"
    sleep 1
done

# Verify all GPUs are active
echo ""
echo "GPU Utilization Check:"
sleep 2
nvidia-smi --query-gpu=index,name,memory.used,memory.total \
    --format=csv,noheader | \
    awk -F',' '{
        idx=$1; name=$2; used=$3; total=$4;
        gsub(/ MiB/, "", used); gsub(/ MiB/, "", total);
        util=int((used/total)*100);
        status=(util > 10) ? "✓ ACTIVE" : "⚠ IDLE";
        printf "  GPU %s: %3d%% used %s\n", idx, util, status
    }'

# Save PID for monitoring/cleanup
echo "$VLLM_PID" > "$LOG_DIR/vllm.pid"

echo ""
echo "vLLM is running with tensor parallelism across $NUM_GPUS GPUs"
echo "API ready at: http://$API_HOST:$API_PORT/v1"
echo ""
echo "To stop vLLM:"
echo "  kill $VLLM_PID"
echo "  OR: pkill -f 'vllm.entrypoints.openai'"
echo ""
echo "To monitor GPU usage:"
echo "  watch -n 1 nvidia-smi"
echo ""

wait $VLLM_PID
