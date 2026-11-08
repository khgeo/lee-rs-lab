---
title: 'LEE RS Lab: a bilingual, login-free remote sensing teaching lab in QGIS'
tags:
  - remote sensing
  - QGIS
  - land cover classification
  - accuracy assessment
  - Khmer
  - education
  - Python
authors:
  - name: Sarath Yam
    orcid: 0000-0000-0000-0000   # TODO: replace with your ORCID iD
    affiliation: 1
affiliations:
  - name: Department of Geography, Royal University of Phnom Penh, Cambodia   # TODO: check the official department name
    index: 1
date: 9 November 2026
bibliography: paper.bib
---

# Summary

LEE RS Lab is an open-source plugin for QGIS [@qgis] that teaches the full remote sensing workflow, from
choosing a study area to a validated land cover map and a change analysis, in one guided panel. Students do not
need an account, an API key or a cloud project. They pick a province, district or commune from a list of
administrative boundaries, and the plugin downloads cloud-masked Sentinel-2 [@drusch2012], Landsat, Sentinel-1,
MODIS, DEM and land cover data from the Microsoft Planetary Computer [@planetarycomputer] and Earth Search. It
reads only the pixels inside the study area and computes every step on the student's own computer with NumPy
and GDAL. The panel follows the order of a typical course: (1) data, (2) enhancement (band combinations, spectral
indices, filters, PCA), (3) classification (K-means / ISODATA, a multi-season Sentinel-2 + Sentinel-1 feature
stack with a cluster labelling table, supervised classification with Maximum Likelihood, Minimum Distance, SAM,
Parallelepiped, Random Forest [@breiman2001], SVM and kNN through scikit-learn [@pedregosa2011]), accuracy
assessment with Cochran sample sizes [@cochran1977] and area-adjusted estimates with 95 % confidence intervals
[@olofsson2014], and (4) from–to change detection with a Sankey diagram. A fifth tab, LEE (*Local Earth Engine*),
runs Earth-Engine-style scripts (`lee.ImageCollection(...).filterDate(...).median()`) locally, with 32 ready
examples, so students can see the code behind the buttons and prepare for Google Earth Engine [@gorelick2017].

The whole interface switches between English and Khmer. Every section has a short built-in lesson. A
*Basic / Advanced* switch hides advanced tools from beginners, and a click-to-grow "magic wand" turns one click
into a training polygon of spectrally similar, connected pixels. A 15-lesson Khmer manual with annotated
screenshots, built on one real example (Kampong Tralach district, Kampong Chhnang province), is published as a
website and a PDF with the code (\autoref{fig:panel}).

![LEE RS Lab in QGIS (Khmer interface): a Random Forest land cover map of Kampong Tralach district from a
Landsat 9 composite, with the class legend and the area table of the supervised classification
section.\label{fig:panel}](figure1.jpg)

# Statement of need

Remote sensing courses in Cambodia, and in many other low- and middle-income countries, face three practical
barriers. First, most teaching material and software interfaces are in English, while many undergraduate students
learn best in their own language. Second, cloud platforms such as Google Earth Engine [@gorelick2017] and its
QGIS front ends [@wu2020] need a personal account and a cloud project for every student, which takes class
time to set up and maintain. Third, desktop tools are either
narrow (one step, such as classification or sampling) or broad and expert-oriented. The Semi-Automatic
Classification Plugin [@congedo2021] and the EnMAP-Box [@jakimow2023] are powerful, but a beginner must learn
many dialogs before reaching a first map. They also do not guide the student to statistically sound accuracy
and area estimates.

LEE RS Lab addresses these needs for an introductory or intermediate course (about 15 sessions). It is used
in one panel, in the order of the course, without registration, and in the students' language. It also teaches
good practice that is often skipped: it separates training accuracy from independent accuracy, reports user's
and producer's accuracy, and replaces pixel-counting areas with the stratified estimator of @olofsson2014. The
manual reports the real, imperfect results of the example (overall accuracy 48 %, kappa 0.22) and uses them to
discuss why a 100 % training accuracy, or a large apparent change between two dates, can be misleading.
Because the plugin needs only QGIS and free data, other teachers can adopt it by installing it from the
official QGIS plugin repository and following the manual. Translating `rs_lab/i18n/km.json` and the
manual into another language is enough to localise it.

# Features for teaching and learning

- **Guided workflow**: one dock panel with five tabs and collapsible sections. Each section has a 📖 lesson,
  and outputs of one step are pre-selected in the next.
- **Basic / Advanced**: Basic shows only the main steps and chooses defaults (for example Random Forest).
  Advanced shows every tool.
- **Training data**: hand-drawn polygons, the click-to-grow magic wand, or automatic points from ESA WorldCover
  [@zanaga2022].
- **Accuracy**: Cochran sample size with proportional or equal allocation, a point-labelling tool with class
  buttons and blind mode, a confusion matrix, kappa, and area-adjusted areas with confidence intervals.
- **Code bridge**: every dataset can be opened as a LEE script, and LEE results can be sent back to the other
  tabs.
- **Reports**: an A4 map layout and a PDF lab report of the student's results.

Automated tests (`tests/`, run on GitHub Actions) cover the numerical core. They include a check that the
area-adjusted estimator reproduces the worked example of @olofsson2014, as well as tests for the
clustering, the classifiers, the magic wand, and the completeness of the Khmer translation.

# Experience of use

<!-- TODO: replace with your real teaching experience. Suggested content:
     course name and level (e.g. GIS & Remote Sensing II, year 3, Department of Geography, RUPP),
     semester(s), number of students, which lessons were used, how long a first classification took,
     main difficulties students had, and what was changed in the plugin because of them
     (e.g. the Basic mode and the magic wand in version 2.4). If you have survey or test results,
     give the numbers here. -->

# Story of the project

<!-- TODO: check and adapt this paragraph; add the year the project started and what motivated it. -->
The plugin grew out of the author's remote sensing classes at the Royal University of Phnom Penh. The aim was
a tool that students could use in Khmer, on their own laptops, without creating cloud accounts. The exercises
were therefore built around free STAC catalogues and a small local engine, LEE, which keeps the Earth Engine
way of writing scripts but runs on the student's computer. Later versions added Khmer administrative
boundaries, a manual based on one real district, area-adjusted accuracy estimates, and a multi-season feature
stack for vegetation types. Version 2.4 added the Basic mode and the magic wand after feedback that the panel
was too complicated and that drawing training polygons was slow. The plugin and manual are developed openly at
<https://github.com/khgeo/lee-rs-lab>.

# Acknowledgements

<!-- TODO: thank the students / colleagues who tested the lessons, if any. -->
Parts of the code, tests and documentation were drafted with the help of an AI assistant (Claude,
Anthropic); the author reviewed, tested and is responsible for all content. Imagery is provided by the
Copernicus programme (ESA), USGS/NASA Landsat and the Microsoft Planetary Computer.

# References
