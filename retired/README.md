# Retired components

Not part of the pipeline. Kept because each was working and tested when it was
removed, and the reasons for removal were scope decisions rather than defects.

| Component | Was | Removed because |
|---|---|---|
| `judge_extraction` | Tool 5, LLM verdict with write/reprocess/abandon routing | Superseded by `evaluate_extraction`, which answers one binary question instead |
| `map_to_schema` | Tool 6, mapped extracted columns onto the mosaic schema | The pipeline now loads a document's own columns to a per-guid table, so there is no shared schema to map onto |
| `column_labeler` | Model-backed column classifier used by `map_to_schema` | Same reason. Correctly labelled `Mail ID`, `Cell`, `Zip`, `SIN`, `Nombre`, `Courriel`, and returned nothing for `Application ID`, `Audit Date`, `Ticket #` |
| `write_to_gcs` | Wrote NDJSON to GCS with Hive partitioning | Output goes to BigQuery instead |
