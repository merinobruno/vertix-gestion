"""Export the exact generated DOCX files with a private local Word instance."""

import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path


LOCK = threading.Lock()
POWERSHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
SCRIPT = Path(__file__).with_name("export-pdf.ps1")
ERROR = "Word no pudo exportar el PDF. Podés descargar el archivo Word o volver a intentar."


class ExportError(Exception):
    pass


def close_timed_out_word(directory):
    identity = directory/"word-instance.txt"
    if not identity.is_file():
        return
    match = re.fullmatch(r"(\d+) (\d+)", identity.read_text(encoding="utf-8"))
    if not match:
        return
    # Kill only the exact instance created by this worker; a recycled PID or a
    # different program fails both identity checks.
    pid, created = match.groups()
    command = (f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
               f"if ($p -and $p.ProcessName -eq 'WINWORD' -and $p.StartTime.ToFileTimeUtc() -eq {created}) "
               "{ $p.Kill(); [void]$p.WaitForExit(4000) }")
    subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", command],
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=12,
                   creationflags=subprocess.CREATE_NO_WINDOW, check=False)


def pdf_batch(documents):
    if os.name != "nt" or not POWERSHELL.is_file():
        raise ExportError(ERROR)
    with LOCK, tempfile.TemporaryDirectory(prefix="panel-word-") as temporary:
        directory = Path(temporary)
        for name, data in documents.items():
            (directory / (name + ".docx")).write_bytes(data)
        try:
            result = subprocess.run(
                [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(SCRIPT), "-Directory", str(directory)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=60 + 10*len(documents),
                creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        except subprocess.TimeoutExpired:
            close_timed_out_word(directory)
            raise ExportError(ERROR) from None
        if result.returncode:
            close_timed_out_word(directory)
            raise ExportError(ERROR)
        pdfs = {name: (directory/(name + ".pdf")).read_bytes() for name in documents}
        if any(not data.startswith(b"%PDF-") for data in pdfs.values()):
            raise ExportError(ERROR)
        return pdfs
