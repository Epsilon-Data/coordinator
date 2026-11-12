"""Marking a worker process not dumpable (unit; the real prctl call is not made)."""
from unittest.mock import MagicMock, patch

from shared.process_hardening import PR_GET_DUMPABLE, PR_SET_DUMPABLE, deny_same_user_inspection


def _libc(set_result=0, dumpable_after=0):
    libc = MagicMock()
    libc.prctl.side_effect = lambda option, *args: set_result if option == PR_SET_DUMPABLE else dumpable_after
    return libc


class TestDenySameUserInspection:
    def test_sets_the_process_not_dumpable_and_confirms_it(self):
        libc = _libc()
        with patch('shared.process_hardening.sys.platform', 'linux'), \
                patch('shared.process_hardening.ctypes.CDLL', return_value=libc):
            assert deny_same_user_inspection() is True

        assert [c.args[:2] for c in libc.prctl.call_args_list] == [(PR_SET_DUMPABLE, 0), (PR_GET_DUMPABLE, 0)]

    def test_reports_failure_when_the_call_fails_or_does_not_take_effect(self):
        for libc in (_libc(set_result=-1), _libc(dumpable_after=1)):
            with patch('shared.process_hardening.sys.platform', 'linux'), \
                    patch('shared.process_hardening.ctypes.CDLL', return_value=libc):
                assert deny_same_user_inspection() is False

    def test_reports_failure_when_libc_cannot_be_loaded(self):
        with patch('shared.process_hardening.sys.platform', 'linux'), \
                patch('shared.process_hardening.ctypes.CDLL', side_effect=OSError('no libc')):
            assert deny_same_user_inspection() is False

    def test_does_nothing_off_linux(self):
        with patch('shared.process_hardening.sys.platform', 'darwin'), \
                patch('shared.process_hardening.ctypes.CDLL') as cdll:
            assert deny_same_user_inspection() is False
        cdll.assert_not_called()
