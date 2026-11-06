"""Tiny i18n layer: English source strings + plain-JSON Khmer table.

tr("text") returns the Khmer string when the language is Khmer and a
translation exists, otherwise the English text.  Strings with
%-placeholders are translated as templates first and formatted after:
    tr("Downloaded %d tiles") % n
"""
import json
import os

from qgis.core import QgsSettings

KEY = "rs_lab/language"
_DIR = os.path.join(os.path.dirname(__file__), "i18n")
_table = {}
_lang = "en"


def _load():
    global _table
    _table = {}
    if _lang == "km":
        try:
            with open(os.path.join(_DIR, "km.json"), encoding="utf-8") as f:
                _table = json.load(f)
        except Exception:
            _table = {}


def init_language():
    global _lang
    s = QgsSettings()
    lang = s.value(KEY, "", type=str)
    if not lang:
        locale = s.value("locale/userLocale", "", type=str) or ""
        lang = "km" if locale.lower().startswith("km") else "en"
    _lang = lang if lang in ("en", "km") else "en"
    _load()
    return _lang


def set_language(lang):
    global _lang
    _lang = lang if lang in ("en", "km") else "en"
    QgsSettings().setValue(KEY, _lang)
    _load()


def language():
    return _lang


def tr(text):
    if _lang == "km":
        v = _table.get(text)
        if v:
            return v
    return text


def both(text):
    """English and Khmer together (used for layer names that must be clear to both)."""
    km = {}
    try:
        with open(os.path.join(_DIR, "km.json"), encoding="utf-8") as f:
            km = json.load(f)
    except Exception:
        pass
    return text, km.get(text, text)
