"""Pruebas del motor de análisis (no requieren Tk)."""

import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import l5x_auditor_gui as m  # noqa: E402


def rung(text, comment=""):
    return {"program": "P", "routine": "R", "number": "0", "text": text, "comment": comment}


def codes_for(text, comment=""):
    findings = []
    m.check_rung(rung(text, comment), findings)
    return [f["code"] for f in findings]


class ParserTests(unittest.TestCase):
    def test_parse_series_branches(self):
        s = m.parse_series("XIC(A)[XIC(B) ,XIO(C) ]OTE(D);")
        self.assertEqual(s[0], ("inst", "XIC", ["A"]))
        self.assertEqual(s[1][0], "branch")
        self.assertEqual(len(s[1][1]), 2)
        self.assertEqual([i[1] for i in m.walk(s)], ["XIC", "XIC", "XIO", "OTE"])

    def test_nested_args(self):
        s = m.parse_series("CPT(Dest,(A+B)*C[2])MOV(Arr[Idx[1]],Out);")
        self.assertEqual(s[0][2], ["Dest", "(A+B)*C[2]"])
        self.assertEqual(s[1][2], ["Arr[Idx[1]]", "Out"])


class RungRuleTests(unittest.TestCase):
    def test_branch_always_true(self):
        self.assertIn("RAMA-SIEMPRE-VERDADERA", codes_for("[XIC(A) ,XIO(A) ]OTE(B);"))

    def test_series_always_false(self):
        self.assertIn("SERIE-SIEMPRE-FALSA", codes_for("XIC(A)XIO(A)OTE(B);"))

    def test_clean_rung(self):
        self.assertEqual(codes_for("XIC(Start)[XIC(Motor) ,XIC(Run) ]XIO(Stop)OTE(Motor);"), [])

    def test_afi(self):
        self.assertIn("AFI", codes_for("AFI()OTE(B);"))

    def test_fixed_value_and_dry_cycle(self):
        f = []
        m.check_rung(rung("XIC(A)MOV(1,Cam.Status_Res);"), f)
        self.assertEqual((f[0]["code"], f[0]["sev"]), ("VALOR-FIJO", "ALTA"))
        f = []
        m.check_rung(rung("XIC(DryCycle)MOV(1,Cam.Results[0]);"), f)
        self.assertEqual(f[0]["sev"], "BAJA")
        self.assertEqual(codes_for("MOV(5,Cam.Results[0].ID);"), [])
        self.assertEqual(codes_for("MOV(0,Cam.Status_Res);"), [])

    def test_timer_pre_zero(self):
        self.assertIn("TIMER-PRE-0", codes_for("MOV(0,T1.PRE)TON(T1,?,?);"))

    def test_input_write(self):
        self.assertIn("ESCRITURA-ENTRADA", codes_for("XIC(A)OTE(Local:1:I.Data.0);"))
        self.assertNotIn("ESCRITURA-ENTRADA", codes_for("XIC(Local:1:I.Data.0)OTE(Local:2:O.Data.0);"))

    def test_suspicious_comment(self):
        self.assertIn("COMENTARIO-SOSPECHOSO", codes_for("XIC(A)OTE(B);", "Puente temporal"))
        self.assertNotIn("COMENTARIO-SOSPECHOSO", codes_for("XIC(A)OTE(B);", "Arranque de bomba"))


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = m.write_demo(cls.tmp)
        cls.res = m.audit_file(cls.path)

    def test_demo_triggers_every_rule(self):
        found = {f["code"] for f in self.res.findings}
        self.assertEqual(found, set(m.RULES))

    def test_project_metadata(self):
        info = self.res.project.info
        self.assertEqual(info["ProcessorType"], "1756-L83E")
        self.assertEqual(self.res.nrungs, 16)
        self.assertEqual(self.res.nst, 3)
        self.assertEqual(set(self.res.project.programs), {"Estacion10", "Pruebas_Viejas"})

    def test_debug_values_resolved(self):
        vals = {f["tag"]: f["value"] for f in self.res.findings if f["code"] == "DEBUG-BIT"}
        self.assertEqual(vals["gDebug.2"], 1)        # 5 = 0b101 -> bit 2 = 1
        self.assertEqual(vals["Bypass_Puerta"], 1)

    def test_disabled_rules_are_filtered(self):
        res = m.audit_file(self.path, enabled=set(m.RULES) - {"AFI", "JMP"})
        found = {f["code"] for f in res.findings}
        self.assertNotIn("AFI", found)
        self.assertNotIn("JMP", found)

    def test_sorted_and_unique(self):
        order = [m.SEVERITY_ORDER[f["sev"]] for f in self.res.findings]
        self.assertEqual(order, sorted(order))
        ids = [f["id"] for f in self.res.findings]
        self.assertEqual(len(ids), len(set(ids)))

    def test_backwards_compatible_api(self):
        findings, values, n = m.check_project(self.path)
        self.assertEqual(n, 16)
        self.assertTrue(findings)
        rungs, values2 = m.load(self.path)
        self.assertEqual(len(rungs), 16)
        self.assertEqual(values, values2)

    def test_rejects_non_l5x(self):
        bad = os.path.join(self.tmp, "bad.L5X")
        with open(bad, "w") as fh:
            fh.write("<foo/>")
        with self.assertRaises(ValueError):
            m.load_project(bad)

    def test_exports(self):
        results, findings = [self.res], self.res.findings
        for kind in ("csv", "html", "json"):
            dest = os.path.join(self.tmp, "r." + kind)
            m.EXPORTERS[kind](dest, results, findings)
            self.assertGreater(os.path.getsize(dest), 200)
        with open(os.path.join(self.tmp, "r.csv"), encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0], m.CSV_HEADER)
        self.assertEqual(len(rows) - 1, len(findings))
        with open(os.path.join(self.tmp, "r.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["hallazgos"]), len(findings))
        if m.xlsx_available():
            dest = os.path.join(self.tmp, "r.xlsx")
            m.export_xlsx(dest, results, findings)
            self.assertGreater(os.path.getsize(dest), 1000)

    def test_cli(self):
        out = os.path.join(self.tmp, "cli.csv")
        self.assertEqual(m.cli([self.path, "--csv", out, "--min-sev", "ALTA"]), 0)
        with open(out, encoding="utf-8-sig") as fh:
            sevs = {r[1] for r in list(csv.reader(fh))[1:]}
        self.assertEqual(sevs, {"ALTA"})
        self.assertEqual(m.cli([self.path, "--fail-on", "ALTA"]), 2)


class RiskTests(unittest.TestCase):
    def test_grades(self):
        self.assertEqual(m.risk_grade(m.risk_index({"ALTA": 0}, 1000)), "A")
        self.assertEqual(m.risk_grade(m.risk_index({"ALTA": 50}, 100)), "E")


if __name__ == "__main__":
    unittest.main()
