"""Beveiligingsbeleid (CSP) van elke pagina: het script in de pagina mag alleen draaien als zijn vingerafdruk (sha256) in
het beleid staat. Code die via een lek in de pagina belandt (XSS) heeft die vingerafdruk niet en draait dus niet; zo kan
niemand de token uit je browser stelen, ook niet als er ooit een ontsnappingsfout in een pagina sluipt.

Na elke wijziging aan een <script> in een .html-bestand de vingerafdrukken bijwerken:
    python3 tests/csp.py --fix
tests/test_static.py controleert dat ze kloppen (anders blijft de pagina in de browser leeg)."""
import base64
import glob
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = re.compile(r'(<meta http-equiv="Content-Security-Policy" content=")([^"]*)(">)')
INLINE = re.compile(r"<script>([\s\S]*?)</script>")
SCRIPT_SRC = re.compile(r"script-src [^;]*")
# Inline handlers (onclick="...") en javascript:-links draaien onder dit beleid niet: gebruik addEventListener
HANDLER = re.compile(r"""\son(?:click|dblclick|load|error|change|input|submit|focus|blur|toggle|key[a-z]+|mouse[a-z]+|touch[a-z]+"""
                     r"""|pointer[a-z]+)\s*=\s*["'`$]|javascript:""", re.I)


def fingerprint(script):
    return "sha256-" + base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()


def wanted(html):
    """De regel script-src die bij deze pagina hoort."""
    return " ".join(["script-src 'self'"] + [f"'{fingerprint(s)}'" for s in INLINE.findall(html)])


def problems(name, html):
    out = []
    m = META.search(html.split("</head>")[0])
    if not m:
        return [f"{name}: geen Content-Security-Policy in de kop"]
    cur = SCRIPT_SRC.search(m.group(2))
    if not cur or cur.group(0) != wanted(html):
        out.append(f"{name}: vingerafdruk van het script klopt niet met het beveiligingsbeleid: draai python3 tests/csp.py --fix")
    if "'unsafe-inline'" in (cur.group(0) if cur else "") or "'unsafe-eval'" in m.group(2):
        out.append(f"{name}: script-src staat ingevoegde code toe ('unsafe-inline' of 'unsafe-eval')")
    for h in sorted(set(x.strip() for x in HANDLER.findall(html))):
        out.append(f"{name}: inline handler of javascript:-link ({h}…) draait niet onder het beleid: gebruik addEventListener")
    return out


def fix(html):
    m = META.search(html)
    if not m:
        return html
    policy = SCRIPT_SRC.sub(wanted(html), m.group(2)) if SCRIPT_SRC.search(m.group(2)) else m.group(2) + "; " + wanted(html)
    return html[:m.start()] + m.group(1) + policy + m.group(3) + html[m.end():]


def pages():
    return sorted(glob.glob(os.path.join(ROOT, "*.html")))


if __name__ == "__main__":
    bad = 0
    for f in pages():
        html = open(f, encoding="utf-8").read()
        if "--fix" in sys.argv:
            new = fix(html)
            if new != html:
                open(f, "w", encoding="utf-8").write(new)
                print(f"  {os.path.basename(f)}: vingerafdruk bijgewerkt")
            html = new
        for p in problems(os.path.basename(f), html):
            bad += 1
            print("  FOUT", p)
    print(f"CSP: {bad} probleem/problemen" if bad else "CSP: alle pagina's in orde")
    sys.exit(1 if bad else 0)
