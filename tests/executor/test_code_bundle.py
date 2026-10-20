"""The code bundle is deterministic: its SHA-256 (the attested script_hash) depends only on file paths and contents.

The known answer is shared with epsilon-attestation-verifier (`epsilon-verify code-digest`), which rebuilds the
same archive from a commit's build/ folder.
"""
import hashlib
import io
import os
import zipfile

import pytest

from workers.executor.services import ZipService

# build.yml, main.py and generated/data.csv below, plus excluded __pycache__/ and .DS_Store
KAT_SHA256 = 'b544578c808dfdf0f988fb168dad8e30a154fe6831bc337237b821bee3a36a03'


@pytest.fixture
def build_dir(tmp_path):
    (tmp_path / 'generated').mkdir()
    (tmp_path / '__pycache__').mkdir()
    (tmp_path / 'build.yml').write_bytes(b'analysis:\n  script_file: main.py\n')
    (tmp_path / 'main.py').write_bytes(b'print(1)\n')
    (tmp_path / 'generated' / 'data.csv').write_bytes(b'a,b\n1,2\n')
    (tmp_path / '__pycache__' / 'main.cpython-311.pyc').write_bytes(b'x')
    (tmp_path / '.DS_Store').write_bytes(b'x')
    return tmp_path


def _digest(path):
    return hashlib.sha256(ZipService().zip_directory(str(path)).data).hexdigest()


def test_known_answer(build_dir):
    assert _digest(build_dir) == KAT_SHA256


def test_modification_times_do_not_change_the_digest(build_dir):
    before = _digest(build_dir)
    os.utime(build_dir / 'main.py', (1, 1))
    os.utime(build_dir / 'generated' / 'data.csv', (2_000_000_000, 2_000_000_000))
    assert _digest(build_dir) == before


def test_content_changes_the_digest(build_dir):
    before = _digest(build_dir)
    (build_dir / 'main.py').write_bytes(b'print(2)\n')
    assert _digest(build_dir) != before


def test_entries_are_sorted_stored_and_extractable(build_dir):
    archive = zipfile.ZipFile(io.BytesIO(ZipService().zip_directory(str(build_dir)).data))
    assert archive.namelist() == ['build.yml', 'generated/data.csv', 'main.py']
    assert {i.compress_type for i in archive.infolist()} == {zipfile.ZIP_STORED}
    assert {i.date_time for i in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
    assert archive.read('generated/data.csv') == b'a,b\n1,2\n'
