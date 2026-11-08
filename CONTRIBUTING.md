# Contributing to LEE RS Lab

Thank you for helping! LEE RS Lab is a teaching tool, so clear, simple and correct matters more than many features.

## Ask for help or report a problem

- **Questions and bugs**: open an issue at <https://github.com/khgeo/lee-rs-lab/issues>. Include your QGIS
  version, operating system, the LEE RS Lab version (*Plugins › Manage and Install Plugins*), what you did,
  and the message in the panel's status line or in *View › Panels › Log Messages › LEE RS Lab*. A screenshot helps.
- **Lessons and manual** (Khmer): open an issue with the lesson number and the sentence to change.
- Issues may be written in **English or Khmer**.

## Change the code

1. Fork the repository and create a branch.
2. The plugin is the folder `rs_lab/`. To try it, copy or link that folder into your QGIS profile's
   `python/plugins/` folder and restart QGIS (or use the *Plugin Reloader* plugin).
3. Every text shown in the interface goes through `tr("English text")`; add the Khmer translation to
   `rs_lab/i18n/km.json`. Agreed terms: band = ប៊ែន, reflectance = ការចាំងផ្លាត, plugin = កម្មវិធីជំនួយ (Plugin),
   remote sensing = ការយកព័ត៌មានពីចម្ងាយ.
4. Run the tests (they do not need QGIS; GDAL tests are skipped if GDAL is missing):

   ```bash
   python -m pip install numpy pytest   # GDAL: use your system / conda package
   python -m pytest -q tests
   ```

5. Avoid silent `try: … except: pass` (the QGIS plugin repository scan rejects it); log the error instead.
6. Open a pull request describing what changed and why. Raise the version in `rs_lab/metadata.txt` and add a
   changelog line only when preparing a release.

## Change the manual

The manual is Markdown in `docs/` (MkDocs Material). Preview with `pip install -r requirements.txt` and
`mkdocs serve`; the PDF is built with `python tools/build_pdf.py`.

## Licences

Code: GPL-2.0-or-later (`LICENSE`). Manual and figures: CC BY-SA 4.0. By contributing you agree that your
contribution is published under these licences.
