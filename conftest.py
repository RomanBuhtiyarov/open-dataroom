import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Каталоги переводов компилируются до первого обращения к gettext.
import compile_messages  # noqa: E402

compile_messages.compile_all()
