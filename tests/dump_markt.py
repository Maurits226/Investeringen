"""TIJDELIJK (alleen op de werkbranch): dagkoersen van de Nasdaq-100 en de angstindexen naar het logboek, ingepakt,
zodat het bodemsignaal op de echte historie doorgerekend kan worden. Wordt verwijderd voordat dit naar main gaat."""
import base64
import json
import os
import sys
import zlib

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import tech as T  # noqa: E402

out = {}
for sym in sys.argv[1:] or ["^NDX", "^VIX", "^VIX3M", "^VXN", "^VIX9D", "^VIX6M", "^SOX", "^GSPC"]:
    try:
        rows = T.yahoo_daily(sym)
        d0 = rows[0][0]
        out[sym] = {"d0": d0.isoformat(), "dd": [(d - d0).days for d, _ in rows], "c": [float(f"{c:.5g}") for _, c in rows]}
        print(f"{sym}: {len(rows)} dagen, {rows[0][0]} t/m {rows[-1][0]}, laatste {rows[-1][1]:.2f}")
    except Exception as e:  # noqa: BLE001
        print(f"{sym}: mislukt ({e})")
blob = base64.b64encode(zlib.compress(json.dumps(out, separators=(",", ":")).encode(), 9)).decode()
print(f"DUMP-BEGIN {len(blob)}")
for i in range(0, len(blob), 4000):
    print("DUMP:" + blob[i:i + 4000])
print("DUMP-END")
