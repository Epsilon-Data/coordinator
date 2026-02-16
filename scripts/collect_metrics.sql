-- ============================================================================
-- PhD Evaluation Metrics Collection Queries
-- Run against the coordinator PostgreSQL database after instrumented jobs complete
-- ============================================================================

-- 1. Per-job timing breakdown (run after 3-5 instrumented jobs)
SELECT
    job_id,
    created_at,
    completed_at,
    EXTRACT(EPOCH FROM (completed_at - created_at))::numeric(10,2) AS end_to_end_seconds,

    -- Attestation document size
    LENGTH(attestation) AS attestation_json_bytes,

    -- Verification receipt size
    LENGTH(verification_receipt) AS receipt_json_bytes,

    -- Coordinator-side step timing (from execution_metrics)
    execution_metrics::jsonb->>'build_validation_ms' AS build_validation_ms,
    execution_metrics::jsonb->>'get_public_key_ms' AS get_public_key_ms,
    execution_metrics::jsonb->>'fetch_csv_ms' AS data_ingestion_ms,
    execution_metrics::jsonb->>'zip_and_encrypt_ms' AS zip_and_encrypt_ms,
    execution_metrics::jsonb->>'enclave_round_trip_ms' AS enclave_round_trip_ms,
    execution_metrics::jsonb->>'total_ms' AS executor_total_ms,

    -- Enclave-internal timing (nested in execution_metrics)
    execution_metrics::jsonb->'enclave_internal'->>'decrypt_zip_ms' AS enclave_decrypt_zip_ms,
    execution_metrics::jsonb->'enclave_internal'->>'decrypt_csv_ms' AS enclave_decrypt_csv_ms,
    execution_metrics::jsonb->'enclave_internal'->>'script_execution_ms' AS enclave_script_exec_ms,
    execution_metrics::jsonb->'enclave_internal'->>'attestation_generation_ms' AS attestation_gen_ms,

    -- Server-side verification timing (from verification_receipt)
    verification_receipt::jsonb->'timing'->>'parse_ms' AS verify_parse_ms,
    verification_receipt::jsonb->'timing'->>'cert_chain_ms' AS verify_cert_chain_ms,
    verification_receipt::jsonb->'timing'->>'signature_ms' AS verify_signature_ms,
    verification_receipt::jsonb->'timing'->>'pcr_match_ms' AS verify_pcr_match_ms,
    verification_receipt::jsonb->'timing'->>'output_hash_ms' AS verify_output_hash_ms,
    verification_receipt::jsonb->'timing'->>'total_ms' AS verify_total_ms

FROM job_requests
WHERE status = 'success'
  AND execution_metrics IS NOT NULL
ORDER BY created_at DESC
LIMIT 10;


-- 2. Aggregated averages for paper table (N jobs)
SELECT
    COUNT(*) AS n_jobs,

    -- End-to-end
    ROUND(AVG(EXTRACT(EPOCH FROM (completed_at - created_at)))::numeric, 2) AS avg_end_to_end_s,

    -- Attestation doc size
    ROUND(AVG(LENGTH(attestation))::numeric, 0) AS avg_attestation_bytes,

    -- Verification receipt size
    ROUND(AVG(LENGTH(verification_receipt))::numeric, 0) AS avg_receipt_bytes,

    -- Coordinator steps
    ROUND(AVG((execution_metrics::jsonb->>'build_validation_ms')::numeric), 1) AS avg_build_validation_ms,
    ROUND(AVG((execution_metrics::jsonb->>'get_public_key_ms')::numeric), 1) AS avg_get_pubkey_ms,
    ROUND(AVG((execution_metrics::jsonb->>'fetch_csv_ms')::numeric), 1) AS avg_data_ingestion_ms,
    ROUND(AVG((execution_metrics::jsonb->>'zip_and_encrypt_ms')::numeric), 1) AS avg_zip_encrypt_ms,
    ROUND(AVG((execution_metrics::jsonb->>'enclave_round_trip_ms')::numeric), 1) AS avg_enclave_round_trip_ms,

    -- Enclave internal
    ROUND(AVG((execution_metrics::jsonb->'enclave_internal'->>'decrypt_zip_ms')::numeric), 1) AS avg_decrypt_zip_ms,
    ROUND(AVG((execution_metrics::jsonb->'enclave_internal'->>'decrypt_csv_ms')::numeric), 1) AS avg_decrypt_csv_ms,
    ROUND(AVG((execution_metrics::jsonb->'enclave_internal'->>'script_execution_ms')::numeric), 1) AS avg_script_exec_ms,
    ROUND(AVG((execution_metrics::jsonb->'enclave_internal'->>'attestation_generation_ms')::numeric), 1) AS avg_attestation_gen_ms,

    -- Server-side verification
    ROUND(AVG((verification_receipt::jsonb->'timing'->>'total_ms')::numeric), 1) AS avg_server_verify_ms

FROM job_requests
WHERE status = 'success'
  AND execution_metrics IS NOT NULL;


-- 3. Attestation document raw size (base64 doc inside JSON)
SELECT
    job_id,
    LENGTH(attestation::jsonb->'attestation'->>'attestation_document') AS attestation_b64_chars,
    ROUND(LENGTH(attestation::jsonb->'attestation'->>'attestation_document') * 0.75) AS attestation_raw_bytes_approx
FROM job_requests
WHERE status = 'success'
  AND attestation IS NOT NULL
ORDER BY created_at DESC
LIMIT 5;


-- 4. EC2 boot time (Lambda start_instances → executor worker ready)
SELECT
    id,
    boot_requested_at,
    boot_ready_at,
    boot_duration_ms,
    ROUND(boot_duration_ms / 1000.0, 1) AS boot_duration_s
FROM boot_events
WHERE boot_ready_at IS NOT NULL
ORDER BY id DESC
LIMIT 10;

-- Average boot time
SELECT
    COUNT(*) AS n_boots,
    ROUND(AVG(boot_duration_ms)::numeric, 0) AS avg_boot_ms,
    ROUND(AVG(boot_duration_ms / 1000.0)::numeric, 1) AS avg_boot_s,
    ROUND(MIN(boot_duration_ms / 1000.0)::numeric, 1) AS min_boot_s,
    ROUND(MAX(boot_duration_ms / 1000.0)::numeric, 1) AS max_boot_s
FROM boot_events
WHERE boot_ready_at IS NOT NULL;


-- 5. DB migration commands (run once before deploying instrumented code)
-- ALTER TABLE job_requests ADD COLUMN IF NOT EXISTS execution_metrics TEXT;
-- ALTER TABLE job_requests ADD COLUMN IF NOT EXISTS verification_receipt TEXT;
-- boot_events table is auto-created by the middleware Lambda
