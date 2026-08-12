# Repository Structure

## Overview

```
agentic-extraction-engine/
├── docs/                          # Documentation
│   ├── README.md                  # Quick start & overview
│   ├── SCHEMA.md                  # PII schema reference (45 fields)
│   ├── CLAUDE.md                  # Operating notes & terminology
│   ├── GPU_SETUP.md               # GPU & tensor parallelism guide
│   └── QUICKSTART_TENSOR_PARALLELISM.md
│
├── extraction/                    # Pipeline modules
│   ├── core/                      # Shared infrastructure services
│   │   ├── config.py              # Configuration management
│   │   ├── bigquery_service.py    # BigQuery operations
│   │   ├── llm_service.py         # Local vLLM client
│   │   ├── gpu_monitor.py         # GPU utilization tracking
│   │   ├── workqueue.py           # SQLite work queue (from mosaic)
│   │   ├── status_ledger.py       # GCS status ledger
│   │   ├── output_store.py        # GCS output writer
│   │   └── throughput.py          # Metrics
│   │
│   ├── phase1/                    # Pattern Analysis & Code Generation
│   │   ├── __init__.py
│   │   ├── analyzer.py            # Analyze samples for patterns
│   │   └── code_generator.py      # Generate extraction code
│   │
│   ├── phase2/                    # Safety Validation & Testing
│   │   ├── __init__.py
│   │   ├── code_validator.py      # AST safety inspection
│   │   └── test_runner.py         # Test extraction code
│   │
│   ├── phase3/                    # Quality Feedback Loop (scaffolding)
│   │   └── __init__.py
│   │
│   ├── phase4/                    # Deterministic Execution (scaffolding)
│   │   └── __init__.py
│   │
│   ├── schemas/                   # Schema definitions
│   │   └── mosaic_pii_schema.json # Target schema (45 PII fields)
│   │
│   ├── tests/                     # Test suite
│   │   └── __init__.py
│   │
│   └── __init__.py                # Package init
│
├── scripts/                       # Operational scripts
│   └── start_vllm.sh              # Start vLLM with tensor parallelism
│
├── orchestrator.py                # Main entry point (all phases)
├── requirements.txt               # Python dependencies
├── source.env.example             # Configuration template
├── STRUCTURE.md                   # This file
└── .gitignore
```

## Key Directories

### `docs/`
All documentation files:
- **README.md** — Quick start, architecture overview
- **SCHEMA.md** — Complete PII schema reference
- **CLAUDE.md** — Operating notes, terminology, checklists
- **GPU_SETUP.md** — GPU configuration and troubleshooting
- **QUICKSTART_TENSOR_PARALLELISM.md** — Quick GPU reference

### `extraction/`
Core pipeline code organized by layers:

#### `extraction/core/`
**Shared infrastructure** used by all phases:
- **config.py** — Env-driven configuration for all phases
- **bigquery_service.py** — BigQuery operations (metadata, status)
- **llm_service.py** — Local vLLM client with TP support
- **gpu_monitor.py** — GPU utilization tracking
- **workqueue.py** — Local SQLite work queue (from mosaic)
- **status_ledger.py** — GCS status ledger (async writes)
- **output_store.py** — GCS output writer (NDJSON)
- **throughput.py** — Throughput metrics

#### `extraction/phase1/`
**Pattern Analysis & Code Generation** (LLM-driven):
- **analyzer.py** — Analyzes samples, identifies patterns
- **code_generator.py** — Generates Python extraction code

#### `extraction/phase2/`
**Safety Validation & Testing** (deterministic):
- **code_validator.py** — AST inspection for dangerous patterns
- **test_runner.py** — Test execution and schema validation

#### `extraction/phase3/` & `extraction/phase4/`
Scaffolding for future implementation:
- Quality feedback loop (Phase 3)
- Deterministic execution at scale (Phase 4)

#### `extraction/schemas/`
Schema definitions:
- **mosaic_pii_schema.json** — Target PII schema (45 fields)

#### `extraction/tests/`
Test suite (organized to mirror phase structure)

### `scripts/`
Operational scripts:
- **start_vllm.sh** — Start vLLM with tensor parallelism (TP-4)

## Dependency Graph

```
orchestrator.py                  (main entry point)
├── extraction.core.config       (configuration)
├── extraction.core.gpu_monitor  (GPU monitoring)
│
├── Phase 1: Code Generation
│   ├── extraction.phase1.analyzer
│   │   ├── extraction.core.config
│   │   ├── extraction.core.llm_service
│   │   └── extraction.core.bigquery_service
│   └── extraction.phase1.code_generator
│       ├── extraction.core.config
│       └── extraction.core.llm_service
│
├── Phase 2: Validation & Testing
│   ├── extraction.phase2.code_validator
│   └── extraction.phase2.test_runner
│       └── extraction.core.config
│
├── Phase 3: Quality Loop (future)
│
└── Phase 4: Execution at Scale (future)
    ├── extraction.core.workqueue
    ├── extraction.core.status_ledger
    ├── extraction.core.output_store
    └── extraction.core.gpu_monitor
```

## Import Patterns

### Within `extraction/core/`
Files in `core/` use relative imports:
```python
from . import config           # Import from same core/ directory
from . import bigquery_service
```

### From phases to core
Phases use full module path:
```python
from extraction.core import config
from extraction.core.llm_service import get_llm_client
from extraction.core.bigquery_service import get_bigquery_client
```

### From orchestrator (repo root)
```python
from extraction.core import config
from extraction.core.gpu_monitor import create_monitor
```

## Generated Artifacts

Phase 1 generates extraction code, which goes to:
```
extraction/generated/
├── extractors_v1.0.py          # Generated code
├── extractors_v1.0.hash        # Checksum
└── analysis_v1.0.json          # Pattern analysis
```

## Running the Pipeline

```bash
# Start vLLM (in terminal 1)
./scripts/start_vllm.sh

# Run pipeline (in terminal 2)
python orchestrator.py --phase 1-2    # Run Phases 1-2
python orchestrator.py --phase 4      # Run Phase 4
```

## Configuration

Environment variables (see `source.env.example`):
```bash
export PROJECT_ID="your-gcp-project"
export GCS_OUTPUT_BUCKET="your-bucket"
export VLLM_API_BASE="http://localhost:8000"
# ... (25+ other options)
```

## Testing

Tests should mirror the phase structure:
```
extraction/tests/
├── test_phase1.py          # Test analyzer & code generator
├── test_phase2.py          # Test validator & test runner
├── fixtures/               # Test data
│   ├── sample_payloads.json
│   └── expected_outputs.json
```

## Future Additions

As the project grows:
```
├── extraction/utils/               # Shared utilities
│   ├── formatting.py               # Output formatting
│   └── validation.py               # Common validation
│
├── tools/                          # Maintenance scripts
│   ├── export_metrics.sh           # Export throughput metrics
│   ├── reconcile_status.sh         # Reconcile BQ status
│   └── cleanup.sh                  # Clean up artifacts
│
└── examples/                       # Example usage
    ├── custom_schema.json
    └── quick_start.py
```

## Migration from Old Structure

If you had code referencing old paths like:
- `from config import ...` → `from extraction.core import config`
- `from bigquery_service import ...` → `from extraction.core.bigquery_service import ...`
- `from llm_service import ...` → `from extraction.core.llm_service import ...`
- Etc.

All imports in the new structure follow the patterns above.

## Design Principles

1. **Separation of Concerns**: Infrastructure (core/) separate from phases
2. **Symmetry**: All phases (1-4) are organized identically
3. **Clarity**: Obvious where to find things (docs/, scripts/, core/, phases/)
4. **Scalability**: Easy to add new phases or infrastructure services
5. **Testability**: Tests mirror implementation structure
