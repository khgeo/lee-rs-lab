"""Translations, lessons, examples, metadata and small helpers that every release must keep consistent."""
import ast
import configparser
import json
import os
import re

import pytest

from conftest import ROOT
from rs_lab import catalog, lee_examples, lessons, sankey

PKG = os.path.join(ROOT, "rs_lab")


def tr_strings(path):
    """Every literal passed to tr("...") in a Python file (implicit concatenation resolved by ast)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr" and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            out.add(n.args[0].value)
    return out


@pytest.fixture(scope="module")
def km():
    return json.load(open(os.path.join(PKG, "i18n", "km.json"), encoding="utf-8"))


@pytest.mark.parametrize("module", ["panel.py", "plugin.py", "report.py", "aoi.py", "basemaps.py"])
def test_every_interface_string_has_khmer(module, km):
    missing = sorted(s for s in tr_strings(os.path.join(PKG, module)) if s not in km)
    assert not missing, "untranslated: %s" % missing[:10]


def test_khmer_placeholders_match(km):
    """'%d pixels' must stay '%d …' in Khmer, or the plugin crashes when formatting the message."""
    pat = re.compile(r"%[-+ #0]*\d*(?:\.\d+)?[sdfi%]")
    bad = [k for k, v in km.items() if sorted(pat.findall(k)) != sorted(pat.findall(v))]
    assert not bad, bad[:10]


def test_khmer_terms(km):
    """Agreed Khmer remote-sensing terms (band = ប៊ែន, reflectance = ការចាំងផ្លាត)."""
    text = " ".join(km.values())
    assert "ចាំផ្លាត" not in text.replace("ចាំងផ្លាត", "")
    assert "បាន់" not in text


def test_lessons_have_both_languages():
    for key, d in lessons.LESSONS.items():
        assert d.get("en") and d.get("km"), key
    assert lessons.lesson("no-such-lesson", "km") == ""


def test_lee_examples_compile():
    assert len(lee_examples.EXAMPLES) >= 30
    for item in lee_examples.EXAMPLES:
        title, code = item[0], item[1]
        compile(code, title, "exec")


def test_metadata_version_and_changelog():
    cp = configparser.ConfigParser()
    cp.read(os.path.join(PKG, "metadata.txt"), encoding="utf-8")
    g = cp["general"]
    v = g["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", v)
    assert g["changelog"].strip().startswith(v), "changelog must start with the current version"
    for k in ("name", "qgisMinimumVersion", "description", "about", "author", "email", "repository"):
        assert g.get(k), k


def test_help_pages_exist():
    for lang in ("en", "km"):
        assert os.path.getsize(os.path.join(PKG, "help", "index_%s.html" % lang)) > 1000


def test_band_role_from_description():
    assert catalog.role_from_desc(catalog.band_desc("B8", "nir", "842 nm")) == "nir"
    assert catalog.role_from_desc("SR_B5") == "nir"
    assert catalog.role_from_desc("") is None


def test_sankey_svg():
    m = [[100.0, 20.0], [5.0, 50.0]]
    svg = sankey.build_sankey_svg(m, [(1, "Forest", "#1a7f37"), (2, "Rice", "f2d64b")], 2020, 2026)
    assert svg.lstrip().startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "Forest" in svg and "2026" in svg
