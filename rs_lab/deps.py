"""Optional Python packages (scikit-learn for Random Forest / SVM / kNN) installed with pip into a
private folder inside the QGIS profile (no admin rights needed, survives plugin updates). The folder
is appended to the END of sys.path so QGIS's own packages always win. No account or login is used
anywhere in RS Lab."""
import importlib
import os
import platform
import shutil
import subprocess  # nosec B404 - only to run pip with a fixed argument list (no shell)
import sys

from qgis.core import QgsApplication, QgsSettings
import logging

LOG = logging.getLogger(__name__)

LIB_DIR = os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "rs_lab_libs")
OLD_LIB_DIR = os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "rs_teaching_lab_libs")


def add_lib_path():
    for d in (LIB_DIR, OLD_LIB_DIR):
        if os.path.isdir(d) and d not in sys.path:
            sys.path.append(d)


def migrate_settings():
    """Copy settings saved under the old plugin name (rs_teaching_lab/…) once."""
    s = QgsSettings()
    for k in ("language", "out_dir"):
        old, new = "rs_teaching_lab/" + k, "rs_lab/" + k
        if s.contains(old) and not s.contains(new):
            s.setValue(new, s.value(old))


add_lib_path()


def _candidates():
    exe = sys.executable or ""
    d = os.path.dirname(exe)
    v = "%d.%d" % sys.version_info[:2]
    c = [exe, getattr(sys, "_base_executable", "")]
    if platform.system() == "Windows":
        for root in (sys.exec_prefix, sys.base_exec_prefix, d):
            c += [os.path.join(root, "python.exe"), os.path.join(root, "python3.exe")]
    else:
        for root in (sys.exec_prefix, sys.base_exec_prefix, os.path.join(d, ".."), d):
            for name in ("python" + v, "python3", "python"):
                c += [os.path.join(root, "bin", name), os.path.join(root, name)]
        if platform.system() == "Darwin":     # QGIS.app layouts (official, conda-based, older)
            app = exe.split(".app/")[0] + ".app" if ".app/" in exe else "/Applications/QGIS.app"
            for sub in ("Contents/MacOS/bin", "Contents/MacOS", "Contents/Frameworks/bin",
                        "Contents/Frameworks/Python.framework/Versions/%s/bin" % v,
                        "Contents/Frameworks/Python.framework/Versions/Current/bin"):
                for name in ("python" + v, "python3"):
                    c.append(os.path.join(app, sub, name))
        c += [shutil.which("python" + v) or ""]
    seen, out = set(), []
    for p in c:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            rp = os.path.realpath(p)
            if rp not in seen and "qgis" not in os.path.basename(rp).lower():
                seen.add(rp)
                out.append(p)
    return out


def _same_version(exe):
    """True if `exe` is a working Python with the SAME major.minor as QGIS (checked in isolated mode,
    so a mismatched interpreter cannot pick up QGIS's standard library and crash)."""
    try:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        p = subprocess.run([exe, "-I", "-c", "import sys;print('%d.%d'%sys.version_info[:2])"],  # noqa: S603  # nosec B603
                           capture_output=True, text=True, timeout=30, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode == 0 and p.stdout.strip() == "%d.%d" % sys.version_info[:2]
    except Exception:  # noqa: BLE001
        return False


_EXE = []


def python_exe():
    """Interpreter that matches this QGIS's Python, or None (then pip runs inside QGIS)."""
    if not _EXE:
        _EXE.append(next((c for c in _candidates() if _same_version(c)), None))
    return _EXE[0]


def has_module(name):
    try:
        importlib.invalidate_caches()
        importlib.import_module(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def pip_args(packages, upgrade=False):
    a = ["install", "--disable-pip-version-check", "--no-warn-script-location", "--target", LIB_DIR]
    if upgrade:
        a.append("--upgrade")
    return a + list(packages)


def pip_command(packages, upgrade=False):
    exe = python_exe()
    return [exe or "(QGIS Python, in-process)", "-m", "pip"] + pip_args(packages, upgrade)


def env_for_child():
    """Only site-packages folders are passed on — never the standard library (that crashed 2.0.x on macOS)."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP")}
    paths = [p for p in sys.path if p and os.path.isdir(p) and "site-packages" in p]
    paths.append(LIB_DIR)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _pip_in_process(args):
    """Fallback when no matching interpreter exists: run pip inside QGIS's own Python."""
    import contextlib
    import io
    buf = io.StringIO()
    try:
        from pip._internal.cli.main import main as pip_main
    except Exception as e:  # noqa: BLE001
        return 1, "pip is not available in this QGIS Python (%s).\nOpen a terminal and run:\n  %s -m pip install " \
                  "--target \"%s\" scikit-learn" % (e, sys.executable, LIB_DIR)
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = pip_main(args)
        except SystemExit as e:
            code = e.code or 0
        except Exception as e:  # noqa: BLE001
            buf.write("\n%s" % e)
            code = 1
    return int(code or 0), buf.getvalue()


def run_pip(packages, upgrade=False, timeout=900):
    """Install packages into LIB_DIR. Used only from background tasks. Returns (code, output)."""
    os.makedirs(LIB_DIR, exist_ok=True)
    args = pip_args(packages, upgrade)
    exe = python_exe()
    if exe is None:
        return _pip_in_process(args)
    p = subprocess.run([exe, "-m", "pip"] + args, capture_output=True, text=True, env=env_for_child(),  # noqa: S603  # nosec B603
                       timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    if p.returncode != 0 and ("No module named pip" in out or "Fatal Python error" in out):
        code, out2 = _pip_in_process(args)
        return code, out + "\n--- retried inside QGIS ---\n" + out2
    return p.returncode, out


def run_blocking(cmd, timeout=900):
    """Used only from background tasks."""
    p = subprocess.run(cmd, capture_output=True, text=True, env=env_for_child(), timeout=timeout,  # noqa: S603  # nosec B603
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")


def guard_multiprocessing():
    """Make multiprocessing start a real Python (same version) instead of the QGIS app, if any library
    still spawns processes. Safe to call many times; does nothing on Windows-style frozen apps."""
    import multiprocessing
    exe = sys.executable or ""
    if os.path.basename(exe).lower().startswith("python"):
        return
    py = python_exe()
    if py:
        try:
            multiprocessing.set_executable(py)
        except Exception:  # noqa: BLE001
            LOG.debug("ignored error", exc_info=True)
