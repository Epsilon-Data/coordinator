"""
Tests for workers/ai_agent/tools/output_disclosure_tool.py
"""
import base64
import pytest
from workers.ai_agent.tools.output_disclosure_tool import OutputDisclosureTool


class TestOutputDisclosureTool:
    @pytest.fixture
    def checker(self):
        return OutputDisclosureTool()

    def test_clean_output(self, checker):
        """Normal analysis output passes all checks."""
        report = checker._run(
            stdout="Loaded 10 records\nAverage age: 45.2\n{'result': 'done'}",
            stderr="",
            output_files=[{
                "name": "output.csv",
                "extension": ".csv",
                "size": 100,
                "content": "metric,value\nheart_rate_avg,72.3\nage_avg,45.2\n",
            }],
        )
        assert report.safe is True
        assert len(report.findings) == 0

    def test_credential_in_stdout(self, checker):
        """API key in stdout is detected as critical."""
        report = checker._run(
            stdout="Config: OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
            stderr="",
            output_files=[],
        )
        assert report.safe is False
        assert any(f["category"] == "credential_leak" for f in report.findings)
        assert any(f["severity"] == "critical" for f in report.findings)

    def test_database_url_in_output(self, checker):
        """Database connection string in output is detected."""
        report = checker._run(
            stdout="DB: postgresql://user:pass@host:5432/db",
            stderr="",
            output_files=[],
        )
        assert report.safe is False
        assert any(f["category"] == "credential_leak" for f in report.findings)

    def test_base64_encoded_block(self, checker):
        """Large base64 block in output is flagged as exfiltration risk."""
        # Create a valid base64 block > 100 chars
        data = "This is sensitive patient data that should not leave the enclave" * 5
        encoded = base64.b64encode(data.encode()).decode()
        report = checker._run(
            stdout=f"Result: {encoded}",
            stderr="",
            output_files=[],
        )
        assert report.safe is False
        assert any(f["category"] == "encoded_data" for f in report.findings)

    def test_csv_with_id_column(self, checker):
        """CSV with identifier columns is flagged."""
        report = checker._run(
            stdout="",
            stderr="",
            output_files=[{
                "name": "results.csv",
                "extension": ".csv",
                "size": 200,
                "content": "patient_id,age,heart_rate\n123,45,72\n456,38,80\n",
            }],
        )
        assert report.safe is False
        assert any(f["category"] == "row_level_data" for f in report.findings)

    def test_csv_without_id_column(self, checker):
        """CSV without identifier columns passes."""
        report = checker._run(
            stdout="",
            stderr="",
            output_files=[{
                "name": "stats.csv",
                "extension": ".csv",
                "size": 100,
                "content": "metric,value\navg_age,45.2\navg_hr,72.1\n",
            }],
        )
        assert report.safe is True

    def test_system_info_in_stderr(self, checker):
        """System path in stderr is flagged."""
        report = checker._run(
            stdout="",
            stderr="File /home/appuser/script.py failed",
            output_files=[],
        )
        assert report.safe is False
        assert any(f["category"] == "system_info" for f in report.findings)

    def test_empty_output(self, checker):
        """Empty output passes."""
        report = checker._run(stdout="", stderr="", output_files=[])
        assert report.safe is True

    def test_normal_numeric_stdout(self, checker):
        """Numeric output (ages, counts) is normal and passes."""
        report = checker._run(
            stdout="402\n202\n482\n116\n900\n699\n322\n26\n96\n324\n",
            stderr="",
            output_files=[],
        )
        assert report.safe is True
