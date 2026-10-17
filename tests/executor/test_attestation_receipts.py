"""Attested hashes in the verification receipt (unit, no database, tdverify faked).

TDX: the output hash comes from the proof whose SHA-512 the verified quote's
REPORTDATA binds, and it is compared with SHA-256 of the held output as on Nitro.
Both backends keep dataset_hash and context_hash, but only from an authentic attestation.
"""
import base64
import functools
import hashlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from workers.executor.models import ExecutionResult, JobStatus
from workers.executor.tdx_verifier import TdxVerificationResult, verify_tdx_attestation
from workers.executor.worker import AUTHENTICITY_CHECKS, ExecutorWorker, _attested_output_hash

JOB_ID = 'JOB-AR-1'
MRTD = 'ab' * 48
OUTPUT = 'count=7\nmean=4.2\n'
DATASET = b'id,age\n1,42\n'
CONTEXT_HASH = 'c0' * 32


def _sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def _tdx_attestation(output=OUTPUT, context_key='atl_context_hash', **proof_overrides):
    """An attestation as epsilon-enclave TdxAttestationService.create_execution_attestation builds it."""
    proof_data = {
        'job_id': 'session-1',
        'script_hash': _sha256(b'bundle'),
        'dataset_hash': _sha256(DATASET),
        'output_hash': _sha256(output),
        'timestamp': 1760000000,
        'nonce': '11' * 32,
        'atl_nonce': None,
        context_key: CONTEXT_HASH,
        'backend': 'tdx',
        **proof_overrides,
    }
    proof_canonical = json.dumps(proof_data, sort_keys=True, separators=(',', ':'))
    report_data = hashlib.sha512(proof_canonical.encode()).hexdigest()
    return {
        'attestation': {
            'backend': 'tdx', 'format': 'tdx_quote', 'attestation_document': base64.b64encode(b'quote').decode(),
            'attestation_document_length': 5, 'report_data': report_data,
        },
        'proof': {**proof_data, 'proof_canonical': proof_canonical},
    }


def _verdict(attestation, quote_verified=True, report_data=None):
    """What the tdverify helper reports for a quote."""
    return {
        'syntax_valid': True,
        'quote_verified': quote_verified,
        'report_data': report_data or attestation['attestation']['report_data'],
        'mrtd': MRTD,
        'rtmrs': ['01' * 48, '02' * 48, '03' * 48, '04' * 48],
    }


def _verify(attestation, verdict=None, **kwargs):
    with patch('workers.executor.tdx_verifier._run_tdverify', return_value=verdict or _verdict(attestation)):
        return verify_tdx_attestation(attestation, **{'expected_mrtd': MRTD, **kwargs})


def _make_worker(executor, storage_path):
    # Bypass __init__: it validates the environment and touches the database
    worker = ExecutorWorker.__new__(ExecutorWorker)
    worker._settings = MagicMock()
    worker._settings.storage.shared_storage_path = str(storage_path)
    worker._executor = executor
    worker._atl_client = None
    return worker


class TestTdxVerifier:
    def test_the_bound_proof_gives_the_attested_hashes(self):
        result = _verify(_tdx_attestation(), expected_output=OUTPUT)

        assert result.valid and result.output_verified and result.pcr_verified
        assert result.output_hash == _sha256(OUTPUT)
        assert result.dataset_hash == _sha256(DATASET)
        assert result.context_hash == CONTEXT_HASH
        assert result.error is None

    def test_a_context_hash_field_is_read_too(self):
        # The name the Nitro user_data uses, should the TDX proof adopt it
        result = _verify(_tdx_attestation(context_key='context_hash'), expected_output=OUTPUT)

        assert result.context_hash == CONTEXT_HASH

    def test_a_different_output_fails_the_output_check_and_keeps_the_attested_hash(self):
        result = _verify(_tdx_attestation(), expected_output='count=8\n')

        assert not result.output_verified
        assert not result.valid
        assert result.output_hash == _sha256(OUTPUT)
        # Like the worker's Nitro receipt: the error carries no hash
        assert result.error == 'Output hash mismatch'

    def test_without_an_expected_output_only_the_binding_is_checked(self):
        result = _verify(_tdx_attestation())

        assert result.valid and result.output_verified
        assert result.output_hash == _sha256(OUTPUT)

    def test_the_unsigned_proof_fields_are_not_used(self):
        attestation = _tdx_attestation()
        attestation['proof'].update(output_hash=_sha256('forged'), dataset_hash='0' * 64, atl_context_hash='1' * 64)

        result = _verify(attestation, expected_output='forged')

        assert result.output_hash == _sha256(OUTPUT)
        assert result.dataset_hash == _sha256(DATASET)
        assert result.context_hash == CONTEXT_HASH
        assert not result.output_verified

    @pytest.mark.parametrize('verdict_change', [
        {'report_data': '00' * 64},
        {'quote_verified': False},
    ], ids=['proof-not-bound', 'quote-not-verified'])
    def test_no_hash_is_attested_without_a_verified_binding(self, verdict_change):
        attestation = _tdx_attestation()

        result = _verify(attestation, {**_verdict(attestation), **verdict_change}, expected_output=OUTPUT)

        assert (result.output_hash, result.dataset_hash, result.context_hash) == (None, None, None)
        assert not result.output_verified
        assert not result.valid

    def test_a_run_without_a_dataset_has_no_dataset_hash(self):
        result = _verify(_tdx_attestation(dataset_hash=''), expected_output=OUTPUT)

        assert result.dataset_hash is None
        assert result.output_hash == _sha256(OUTPUT)


class TestTdxOutputHashIsHeld:
    """process_job with a TDX attestation: the attested output hash is compared and held as on Nitro."""

    @pytest.fixture
    def repo(self):
        with patch('workers.executor.worker.job_repository') as mock:
            yield mock

    @pytest.fixture
    def run(self, tmp_path, repo):
        (tmp_path / 'repositories' / JOB_ID).mkdir(parents=True)

        def run(output, attestation, verdict=None, expected_mrtd=MRTD):
            executor = MagicMock(is_ready=True)
            executor.execute.return_value = ExecutionResult(
                job_id=JOB_ID, status=JobStatus.SUCCESS, execution_time=1.0, output=output, attestation=attestation,
            )
            worker = _make_worker(executor, tmp_path)
            verifier = functools.partial(verify_tdx_attestation, expected_mrtd=expected_mrtd)
            job = {'job_id': JOB_ID, 'workspace_id': 'ws-1', 'user_id': 'u-1', 'commit_sha': 'c', 'github_repo': 'r',
                   'github_branch': 'main', 'commit_message': 'm', 'commit_author': 'a', 'created_at': 'now'}
            with patch('workers.executor.tdx_verifier._run_tdverify', return_value=verdict or _verdict(attestation)), \
                    patch('workers.executor.worker.verify_tdx_attestation', side_effect=verifier):
                assert worker.process_job(job) is True
            return repo.complete_job_with_held_output.call_args.kwargs
        return run

    def test_a_matching_output_holds_its_attested_hash(self, run):
        held = run(OUTPUT, _tdx_attestation())

        receipt = json.loads(held['verification_receipt'])
        assert held['attested_output_hash'] == _sha256(OUTPUT)
        assert held['content'] == OUTPUT
        assert receipt['output_hash'] == _sha256(OUTPUT)
        assert receipt['checks']['output_verified'] is True
        assert (receipt['dataset_hash'], receipt['context_hash']) == (_sha256(DATASET), CONTEXT_HASH)

    def test_a_different_output_is_held_with_the_attested_hash_and_logged_as_a_mismatch(self, run, caplog):
        caplog.set_level(logging.WARNING)
        held = run('count=8\n', _tdx_attestation(output=OUTPUT))

        receipt = json.loads(held['verification_receipt'])
        assert held['attested_output_hash'] == _sha256(OUTPUT)
        assert held['content'] == 'count=8\n'
        assert receipt['checks']['output_verified'] is False
        assert receipt['error'] == 'Output hash mismatch'
        assert any(_sha256(OUTPUT) in r.getMessage() and _sha256('count=8\n') in r.getMessage()
                   for r in caplog.records)

    def test_an_unbound_proof_holds_no_attested_hash(self, run):
        attestation = _tdx_attestation()

        held = run(OUTPUT, attestation, verdict={**_verdict(attestation), 'report_data': '00' * 64})

        assert held['attested_output_hash'] is None

    def test_without_an_expected_mrtd_no_attested_hash_is_held(self, run):
        # pcr_verified stays False without EXPECTED_MRTD, so the attestation is not authentic for the gate
        held = run(OUTPUT, _tdx_attestation(), expected_mrtd=None)

        receipt = json.loads(held['verification_receipt'])
        assert receipt['output_hash'] == _sha256(OUTPUT)
        assert held['attested_output_hash'] is None
        assert (receipt['dataset_hash'], receipt['context_hash']) == (None, None)


class TestReceiptInputHashes:
    @pytest.fixture
    def worker(self, tmp_path):
        return _make_worker(MagicMock(), tmp_path)

    @staticmethod
    def _nitro_result(user_data, **checks):
        values = dict(syntax_valid=True, certificate_chain_valid=True, aws_signature_valid=True, pcr_verified=True,
                      output_verified=True)
        values.update(checks)
        return SimpleNamespace(
            valid=all(values.values()), **values, pcr0='aa', pcr1='bb', pcr2='cc', output_hash=_sha256(OUTPUT),
            module_id='i-0abc', timestamp=None, error=None, timing={}, user_data=user_data,
        )

    def _nitro_receipt(self, worker, vr):
        attestation = {'attestation': {'attestation_document': 'ZG9j'}}
        with patch('workers.executor.worker.verify_attestation', return_value=vr):
            return json.loads(worker._verify_attestation(JOB_ID, attestation, MagicMock(), expected_output=OUTPUT))

    def test_nitro_receipt_keeps_the_attested_dataset_and_context_hash(self, worker):
        user_data = {'job_id': 'j', 'script_hash': 's' * 64, 'dataset_hash': _sha256(DATASET),
                     'output_hash': _sha256(OUTPUT), 'timestamp': 1, 'context_hash': CONTEXT_HASH, 'nonce': 'bm9uY2U='}

        receipt = self._nitro_receipt(worker, self._nitro_result(user_data))

        assert receipt['dataset_hash'] == _sha256(DATASET)
        assert receipt['context_hash'] == CONTEXT_HASH
        assert receipt['output_hash'] == _sha256(OUTPUT)

    @pytest.mark.parametrize('user_data', [
        {'dataset_hash': '', 'output_hash': 'x'},  # a run without a dataset
        {'output_hash': 'x'},  # no ATL context
        {'raw': 'bm90IGpzb24='},  # user_data that is not JSON
        None,
    ])
    def test_nitro_receipt_without_them(self, worker, user_data):
        receipt = self._nitro_receipt(worker, self._nitro_result(user_data))

        assert (receipt['dataset_hash'], receipt['context_hash']) == (None, None)

    @pytest.mark.parametrize('check', ['syntax_valid', 'certificate_chain_valid', 'aws_signature_valid', 'pcr_verified'])
    def test_nitro_receipt_drops_them_unless_the_attestation_is_authentic(self, worker, check):
        user_data = {'dataset_hash': _sha256(DATASET), 'context_hash': CONTEXT_HASH}

        receipt = self._nitro_receipt(worker, self._nitro_result(user_data, **{check: False}))

        assert (receipt['dataset_hash'], receipt['context_hash']) == (None, None)

    def test_nitro_receipt_keeps_them_on_an_output_mismatch(self, worker):
        # Authentic, but the output differs: the reviewer still sees the attested inputs
        user_data = {'dataset_hash': _sha256(DATASET), 'context_hash': CONTEXT_HASH}

        receipt = self._nitro_receipt(worker, self._nitro_result(user_data, output_verified=False))

        assert (receipt['dataset_hash'], receipt['context_hash']) == (_sha256(DATASET), CONTEXT_HASH)

    @pytest.mark.parametrize('check', AUTHENTICITY_CHECKS)
    def test_tdx_receipt_keeps_them_only_from_an_authentic_attestation(self, worker, check):
        attestation = {'attestation': {'backend': 'tdx', 'attestation_document': 'ZG9j'}}
        checks = dict.fromkeys(AUTHENTICITY_CHECKS, True)
        authentic = TdxVerificationResult(valid=True, output_verified=True, measurements={'mrtd': MRTD},
                                          output_hash=_sha256(OUTPUT), dataset_hash=_sha256(DATASET),
                                          context_hash=CONTEXT_HASH, **checks)
        not_authentic = TdxVerificationResult(**{**authentic.__dict__, check: False})

        with patch('workers.executor.worker.verify_tdx_attestation', side_effect=[authentic, not_authentic]):
            kept = json.loads(worker._verify_attestation(JOB_ID, attestation, MagicMock(), expected_output=OUTPUT))
            dropped = json.loads(worker._verify_attestation(JOB_ID, attestation, MagicMock(), expected_output=OUTPUT))

        assert (kept['dataset_hash'], kept['context_hash']) == (_sha256(DATASET), CONTEXT_HASH)
        assert (dropped['dataset_hash'], dropped['context_hash']) == (None, None)
        assert _attested_output_hash(json.dumps(kept)) == _sha256(OUTPUT)
        assert _attested_output_hash(json.dumps(dropped)) is None
