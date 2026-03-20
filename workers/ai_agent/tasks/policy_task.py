from crewai import Task, Agent
from typing import List


def create_policy_task(agent: Agent, pii_fields: List[str]) -> Task:
    """Create policy collection task"""

    return Task(
        description=f"""
        Establish the security threat detection framework for reviewing researcher-submitted
        code that will execute inside a Trusted Execution Environment (AWS Nitro Enclave)
        with access to real patient datasets.

        CONTEXT: Researchers submit Python scripts to analyze healthcare data. The code runs
        inside an isolated enclave. Legitimate code WILL access patient data fields — that is
        expected. The threats we must detect are MALICIOUS behaviors.

        The following patient data fields exist in the datasets (for context, NOT for blocking):
        {', '.join(pii_fields)}

        Produce a detection framework covering these threat categories:

        TIER 1 — CRITICAL (immediate reject, any occurrence):
          - DATA EXFILTRATION: network calls (socket, urllib, requests, http.client,
            httpx, aiohttp), DNS resolution, any outbound connection attempt
          - CODE INJECTION: eval(), exec(), compile(), __import__(), os.system(),
            subprocess.*, os.popen(), commands module
          - REVERSE SHELLS: socket bind/connect patterns, pty.spawn, os.dup2
          - CREDENTIAL HARVESTING: reading env vars for secrets (OPENAI_API_KEY,
            DATABASE_URL, AWS_*), reading /proc, ~/.ssh, ~/.aws

        TIER 2 — HIGH (reject unless clearly justified):
          - FILESYSTEM ESCAPE: path traversal (../ sequences), symlink creation,
            accessing paths outside working directory, reading /etc/*, /proc/*
          - OBFUSCATED CODE: base64.b64decode() of strings that are then executed,
            hex-encoded payloads, rot13/xor encoding, marshaled/pickled code objects
          - DANGEROUS DESERIALIZATION: pickle.loads(), marshal.loads(), yaml.load()
            without SafeLoader, jsonpickle
          - DYNAMIC CODE LOADING: importlib with user-controlled strings, __import__
            with variables, pkgutil abuse

        TIER 3 — MEDIUM (flag for review):
          - SUSPICIOUS IMPORTS: ctypes, cffi, multiprocessing (for IPC abuse),
            threading (for race conditions), signal, resource
          - SUPPLY CHAIN RISK: requirements.txt with unusual packages, typosquatted
            package names, pinned to specific vulnerable versions
          - EXCESSIVE OUTPUT: writing suspiciously large files, binary content in
            text output files (possible steganographic exfiltration)
          - SYSTEM RECONNAISSANCE: os.uname(), platform.*, sys.version, directory
            listings, env var enumeration

        TIER 4 — LOW (informational, does not block):
          - Standard data analysis imports (pandas, numpy, scipy, sklearn, matplotlib)
          - File I/O within working directory (reading input CSV, writing output CSV)
          - Normal Python patterns (list comprehensions, generators, decorators)
          - Accessing patient data fields for legitimate analysis

        Output the complete threat framework as a structured summary for the Code Analyzer.
        """,
        agent=agent,
        expected_output="Tiered threat taxonomy with categories, detection patterns, and severity classifications for malicious code review"
    )
