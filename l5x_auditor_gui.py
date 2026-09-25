#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auditor L5X - motor de analisis de proyectos Studio 5000 exportados a L5X.

Uso:
    python3 l5x_audit.py archivo.L5X [archivo2.L5X ...]
    python3 l5x_audit.py archivo.L5X --csv reporte.csv

Busca patrones que normalmente indican logica puenteada, deshabilitada,
forzada o mal escrita. NO sustituye una revision manual: cada hallazgo
hay que confirmarlo en el contexto de la maquina.
"""

import re
import sys
import csv
import xml.etree.ElementTree as ET
from collections import defaultdict

MAX_EJEMPLOS = 6

SEVERITY_ORDER = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}


# ---------------------------------------------------------------- parser -----
def split_top(text, sep=","):
    """Separa por 'sep' respetando parentesis y corchetes."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def parse_series(text):
    """Convierte el texto de un rung en una lista.
    Instruccion -> ('inst', nombre, [args])
    Rama        -> ('branch', [serie, serie, ...])
    """
    items, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n;":
            i += 1
            continue
        if ch == "[":
            depth, j = 1, i + 1
            while j < n and depth:
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                j += 1
            inner = text[i + 1:j - 1]
            items.append(("branch", [parse_series(p) for p in split_top(inner)]))
            i = j
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", text[i:])
        if m:
            name = m.group(1)
            j = i + m.end()
            depth = 1
            while j < n and depth:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            args = [a.strip() for a in split_top(text[i + m.end():j - 1])]
            items.append(("inst", name, args))
            i = j
            continue
        i += 1
    return items


def walk(series):
    """Recorre recursivamente todas las instrucciones de una serie."""
    for it in series:
        if it[0] == "inst":
            yield it
        else:
            for sub in it[1]:
                yield from walk(sub)


def contacts_of(series, kind):
    """Contactos 'kind' (XIC/XIO) directos en este nivel de serie."""
    return [it[2][0] for it in series if it[0] == "inst" and it[1] == kind and it[2]]


# --------------------------------------------------------------- lectura -----
def load(path):
    """Devuelve (rungs, tag_values). rung = dict con ubicacion y texto."""
    tree = ET.parse(path)
    root = tree.getroot()
    rungs, values = [], {}

    def scan_tags(scope_el, scope_name):
        for tag in scope_el.iter("Tag"):
            name = tag.get("Name")
            for data in tag.findall("Data"):
                if data.get("Format") == "L5K" and data.text:
                    values[(scope_name, name)] = data.text.strip()

    ctrl = root.find(".//Controller")
    if ctrl is not None:
        tags_el = ctrl.find("Tags")
        if tags_el is not None:
            scan_tags(tags_el, "<controller>")

    for prog in root.iter("Program"):
        pname = prog.get("Name")
        tags_el = prog.find("Tags")
        if tags_el is not None:
            scan_tags(tags_el, pname)
        for rout in prog.iter("Routine"):
            rname = rout.get("Name")
            for rung in rout.iter("Rung"):
                t = rung.find("Text")
                if t is None or not t.text:
                    continue
                rungs.append({
                    "program": pname,
                    "routine": rname,
                    "number": rung.get("Number"),
                    "text": t.text.strip(),
                    "comment": (rung.findtext("Comment") or "").strip(),
                })
    return rungs, values


# --------------------------------------------------------------- chequeos ----
def check_rung(r, findings):
    text = r["text"]
    series = parse_series(text)
    loc = (r["program"], r["routine"], r["number"])

    def add(sev, code, msg):
        findings.append({"sev": sev, "code": code, "prog": loc[0],
                         "rout": loc[1], "rung": loc[2], "msg": msg,
                         "text": text[:300]})

    # 1. Rama siempre verdadera: XIC(t) y XIO(t) en ramas hermanas.
    def scan_branches(s):
        for it in s:
            if it[0] == "branch":
                xic, xio = set(), set()
                for leg in it[1]:
                    if len(leg) == 1 and leg[0][0] == "inst":
                        nm, args = leg[0][1], leg[0][2]
                        if nm == "XIC" and args:
                            xic.add(args[0])
                        if nm == "XIO" and args:
                            xio.add(args[0])
                for t in xic & xio:
                    add("ALTA", "RAMA-SIEMPRE-VERDADERA",
                        f"XIC y XIO de '{t}' en ramas paralelas: el interlock queda anulado")
                for leg in it[1]:
                    scan_branches(leg)

    scan_branches(series)

    # 2. Serie siempre falsa: XIC(t) y XIO(t) en el mismo camino serie.
    def scan_series(s):
        xic, xio = set(contacts_of(s, "XIC")), set(contacts_of(s, "XIO"))
        for t in xic & xio:
            add("ALTA", "SERIE-SIEMPRE-FALSA",
                f"XIC y XIO de '{t}' en serie: el renglon nunca puede conducir")
        for it in s:
            if it[0] == "branch":
                for leg in it[1]:
                    scan_series(leg)

    scan_series(series)

    # 3. AFI: logica deshabilitada a proposito.
    if re.search(r"\bAFI\s*\(\s*\)", text):
        add("MEDIA", "AFI", "Contiene AFI(): hay logica deshabilitada en el renglon")

    # 4. Valores fijos escritos a resultados de inspeccion / medicion.
    #    Se ignoran .ID, .PRE, limites y la rama de dry cycle (ahi es normal).
    dry = "DryCycle" in text
    for it in walk(series):
        if it[1] in ("MOV", "FLL") and len(it[2]) >= 2:
            src, dst = it[2][0], it[2][1]
            if not re.fullmatch(r"-?\d+(\.\d+)?", src) or src == "0":
                continue
            if re.search(r"\.(ID|PRE|ACC|Len|LEN)\b|\.Lim\.|Check\[", dst):
                continue
            if re.search(r"(Status_Res|\.Res\[|Results\[|Inspect|Measure)", dst, re.I):
                add("BAJA" if dry else "ALTA", "VALOR-FIJO",
                    f"Escribe la constante {src} en '{dst}'" +
                    (" (rama de dry cycle)" if dry else ": posible resultado forzado"))

    # 5. Temporizadores con preset 0.
    for it in walk(series):
        if it[1] in ("TON", "TOF", "RTO") and it[2]:
            base = it[2][0]
            if re.search(r"MOV\(0,\s*" + re.escape(base) + r"\.PRE", text):
                add("MEDIA", "TIMER-PRE-0", f"Preset de '{base}' se carga con 0")

    # 6. Bits de debug que condicionan logica de produccion.
    for it in walk(series):
        if it[1] in ("XIC", "XIO") and it[2]:
            tag = it[2][0]
            if re.match(r"g?[Dd]ebug", tag) or "_Debug" in tag or "Bypass" in tag:
                findings.append({"sev": "BAJA", "code": "DEBUG-BIT",
                                 "prog": loc[0], "rout": loc[1], "rung": loc[2],
                                 "msg": f"Condicionado por '{tag}'",
                                 "text": text[:300], "tag": tag})

    # 7. Salto incondicional / JMP-LBL.
    for it in walk(series):
        if it[1] == "JMP":
            add("BAJA", "JMP", "Usa JMP: revisar que no salte logica de seguridad")

    return series


def check_project(path):
    rungs, values = load(path)
    findings = []
    ons_bits = defaultdict(list)     # (programa, bit) -> ubicaciones
    ote_tags = defaultdict(list)     # (programa, tag) -> ubicaciones
    otl_tags, otu_tags = defaultdict(list), defaultdict(list)

    for r in rungs:
        series = check_rung(r, findings)
        loc = f"{r['program']}/{r['routine']} rung {r['number']}"
        for it in walk(series):
            if it[1] in ("ONS", "OSR", "OSF") and it[2]:
                ons_bits[(r["program"], it[2][0])].append(loc)
            elif it[1] == "OTE" and it[2]:
                ote_tags[(r["program"], it[2][0])].append(loc)
            elif it[1] == "OTL" and it[2]:
                otl_tags[(r["program"], it[2][0])].append(loc)
            elif it[1] == "OTU" and it[2]:
                otu_tags[(r["program"], it[2][0])].append(loc)

    # 8. Bit de one-shot reutilizado en el mismo programa.
    for (prog, bit), locs in ons_bits.items():
        if len(locs) > 1:
            findings.append({"sev": "ALTA", "code": "ONS-DUPLICADO", "prog": prog,
                             "rout": "-", "rung": "-",
                             "msg": f"Bit de one shot '{bit}' usado {len(locs)} veces: " +
                                    ", ".join(locs[:4]),
                             "text": ""})

    # 9. Misma bobina OTE en varios renglones (la ultima gana).
    for (prog, tag), locs in ote_tags.items():
        if len(locs) > 1:
            findings.append({"sev": "MEDIA", "code": "OTE-DUPLICADA", "prog": prog,
                             "rout": "-", "rung": "-",
                             "msg": f"OTE de '{tag}' en {len(locs)} renglones: " +
                                    ", ".join(locs[:4]),
                             "text": ""})

    # 10. OTL sin OTU.
    for (prog, tag), locs in otl_tags.items():
        if (prog, tag) not in otu_tags:
            findings.append({"sev": "MEDIA", "code": "OTL-SIN-OTU", "prog": prog,
                             "rout": "-", "rung": "-",
                             "msg": f"'{tag}' se enclava con OTL y no se ve OTU en el mismo programa",
                             "text": ""})

    return findings, values, len(rungs)



# ------------------------------------------------------------------- GUI -----
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

APP_TITLE = "Auditor de proyectos L5X"


def resolver_valor_debug(values, tag):
    m = re.match(r"([A-Za-z0-9_]+)\.BIT\[(\d+)\]\.(\d+)$", tag)
    if m:
        base, idx, bit = m.group(1), int(m.group(2)), int(m.group(3))
    else:
        m2 = re.match(r"([A-Za-z0-9_]+)\.(\d+)$", tag)
        if not m2:
            return None
        base, idx, bit = m2.group(1), None, int(m2.group(2))
    for (_scope, name), raw in values.items():
        if name != base:
            continue
        nums = re.findall(r"-?\d+", raw)
        if idx is not None and idx < len(nums):
            word = int(nums[idx])
        elif nums:
            word = int(nums[0])
        else:
            return None
        if word < 0:
            word += 1 << 32
        return (word >> bit) & 1
    return None


def filas_csv(path, findings, values):
    """Convierte los hallazgos en filas listas para el CSV."""
    filas = []
    debug = defaultdict(list)
    for f in findings:
        if f["code"] == "DEBUG-BIT":
            debug[f["tag"]].append(f"{f['prog']}/{f['rout']}")
            continue
        filas.append([os.path.basename(path), f["sev"], f["code"], f["prog"],
                      f["rout"], f["rung"], f["msg"], f.get("text", "")[:250]])
    filas.sort(key=lambda r: (SEVERITY_ORDER.get(r[1], 9), r[2], r[3], r[4]))
    for tag, locs in sorted(debug.items(), key=lambda x: -len(x[1])):
        val = resolver_valor_debug(values, tag)
        filas.append([os.path.basename(path), "INFO", "DEBUG-BIT", tag,
                      f"valor={'?' if val is None else val}", str(len(locs)),
                      "Bit de debug o bypass que condiciona logica",
                      "; ".join(sorted(set(locs))[:8])])
    return filas


class AuditorApp:
    def __init__(self, root):
        self.root = root
        self.archivos = []
        root.title(APP_TITLE)
        root.geometry("900x600")

        top = tk.Frame(root, pady=8, padx=8)
        top.pack(fill="x")
        tk.Button(top, text="Seleccionar archivos L5X...", width=26,
                  command=self.elegir).pack(side="left")
        self.lbl = tk.Label(top, text="Ningun archivo seleccionado", anchor="w")
        self.lbl.pack(side="left", padx=10)

        opts = tk.Frame(root, padx=8)
        opts.pack(fill="x")
        self.solo_altas = tk.BooleanVar(value=False)
        tk.Checkbutton(opts, text="Solo severidad ALTA en el CSV",
                       variable=self.solo_altas).pack(side="left")
        self.btn = tk.Button(opts, text="Revisar y generar CSV", width=24,
                             state="disabled", command=self.lanzar)
        self.btn.pack(side="right", pady=6)

        self.barra = ttk.Progressbar(root, mode="indeterminate")
        self.barra.pack(fill="x", padx=8)

        self.txt = scrolledtext.ScrolledText(root, font=("Consolas", 9))
        self.txt.pack(fill="both", expand=True, padx=8, pady=8)
        self.log("Selecciona uno o varios archivos .L5X exportados de Studio 5000.\n"
                 "Para exportar: File > Save As > Logix Designer XML File (*.L5X)\n")

    def log(self, msg):
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.root.update_idletasks()

    def elegir(self):
        rutas = filedialog.askopenfilenames(
            title="Selecciona archivos L5X",
            filetypes=[("Archivos L5X", "*.L5X *.l5x"), ("Todos", "*.*")])
        if not rutas:
            return
        self.archivos = list(rutas)
        self.lbl.config(text=f"{len(self.archivos)} archivo(s) seleccionado(s)")
        self.btn.config(state="normal")
        for r in self.archivos:
            self.log(f"Seleccionado: {os.path.basename(r)}")

    def lanzar(self):
        destino = filedialog.asksaveasfilename(
            title="Guardar reporte como", defaultextension=".csv",
            initialfile="reporte_l5x.csv",
            filetypes=[("CSV", "*.csv")])
        if not destino:
            return
        self.btn.config(state="disabled")
        self.barra.start(12)
        threading.Thread(target=self.trabajar, args=(destino,), daemon=True).start()

    def trabajar(self, destino):
        todas, resumen = [], []
        try:
            for ruta in self.archivos:
                self.log(f"\nRevisando {os.path.basename(ruta)} ...")
                try:
                    findings, values, nrungs = check_project(ruta)
                except Exception as exc:
                    self.log(f"  ERROR: {exc}")
                    continue
                filas = filas_csv(ruta, findings, values)
                if self.solo_altas.get():
                    filas = [f for f in filas if f[1] in ("ALTA", "INFO")]
                todas.extend(filas)
                conteo = defaultdict(int)
                for f in findings:
                    if f["code"] != "DEBUG-BIT":
                        conteo[f["code"]] += 1
                self.log(f"  {nrungs} renglones revisados")
                for code, c in sorted(conteo.items(), key=lambda x: -x[1]):
                    self.log(f"    {c:5d}  {code}")
                altas = sum(1 for f in findings if f.get("sev") == "ALTA")
                resumen.append((os.path.basename(ruta), nrungs, altas))

            with open(destino, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(["archivo", "severidad", "codigo", "programa",
                            "rutina", "rung", "detalle", "texto_del_renglon"])
                w.writerows(todas)

            self.log(f"\nCSV generado: {destino}")
            self.log("\nRecuerda: cada hallazgo hay que confirmarlo en contexto. "
                     "AFI y OTL sin OTU suelen ser normales.")
            resumen_txt = "\n".join(
                f"{n}: {r} renglones, {a} hallazgos de severidad alta"
                for n, r, a in resumen)
            messagebox.showinfo(APP_TITLE, f"Listo.\n\n{resumen_txt}\n\n{destino}")
        finally:
            self.barra.stop()
            self.btn.config(state="normal")


if __name__ == "__main__":
    root = tk.Tk()
    AuditorApp(root)
    root.mainloop()
