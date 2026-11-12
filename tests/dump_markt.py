"""TIJDELIJK (alleen op de werkbranch): rekent het bodemsignaal door op de echte historie en zet de uitkomst in het
logboek. Wordt verwijderd voordat dit naar main gaat."""
import json
import os
import sys
from datetime import date

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bodem as B  # noqa: E402
import tech as T  # noqa: E402

rows = {"ndx": T.yahoo_daily("^NDX")}
for key in ("vix", "v3m"):
    rows[key] = T.yahoo_daily(B.SRC[key][0], date(1990, 1, 1))
out = B.build(rows, [])
print("JSON:" + json.dumps(out, ensure_ascii=False, separators=(",", ":")))
