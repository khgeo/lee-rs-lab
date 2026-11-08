"""Short bilingual lessons shown in each tab (HTML)."""

LESSONS = {
    "setup": {
        "en": """<b>Where the data come from</b><br>
RS Lab needs <b>no account and no login</b>. Satellite images are read from free, open STAC catalogues of
<i>Cloud-Optimised GeoTIFFs</i>: Microsoft Planetary Computer and Earth Search (Amazon Web Services).
A COG lets QGIS read only the pixels inside your AOI, so you never download a whole 100 km scene.
Composites, indices and classifications are computed on <b>your own computer</b>.
Use <i>Test connections</i> to check that every source can be reached from your network.""",
        "km": """<b>ប្រភពទិន្នន័យ</b><br>
RS Lab <b>មិនត្រូវការគណនី ឬការចូលប្រើ</b>ទេ។ រូបភាពផ្កាយរណបត្រូវបានអានពីកាតាឡុក STAC បើកចំហឥតគិតថ្លៃនៃ
<i>Cloud-Optimised GeoTIFF</i>៖ Microsoft Planetary Computer និង Earth Search (Amazon Web Services)។
COG អនុញ្ញាតឲ្យ QGIS អានតែភីកសែលក្នុងតំបន់សិក្សា ដូច្នេះអ្នកមិនចាំបាច់ទាញយករូបភាពទាំងមូល ១០០ គីឡូម៉ែត្រទេ។
ការផ្សំរូបភាព សន្ទស្សន៍ និងចំណាត់ថ្នាក់ត្រូវបានគណនានៅលើ<b>កុំព្យូទ័ររបស់អ្នកផ្ទាល់</b>។
ចុច <i>សាកល្បងការតភ្ជាប់</i> ដើម្បីពិនិត្យថាប្រភពនីមួយៗអាចភ្ជាប់បានពីបណ្ដាញរបស់អ្នក។""",
    },
    "download": {
        "en": """<b>Satellite images and their levels</b><br>
<b>Level-1 / TOA</b> = reflectance measured at the top of the atmosphere (includes haze).
<b>Level-2 / SR</b> = surface reflectance after atmospheric correction — best for analysis.<br>
<b>Raw DN</b> are the integers stored by the provider (Sentinel-2: reflectance × 10000;
Landsat C2 L2: reflectance = DN × 0.0000275 − 0.2). <b>Scaled</b> converts them to 0–1 reflectance.<br>
A <b>composite</b> combines many dates pixel by pixel; the median removes most clouds and shadows.
Spatial resolution: Sentinel-2 10–60 m, Landsat 30 m (15 m pan), Sentinel-1 10 m.""",
        "km": """<b>រូបភាពផ្កាយរណប និងកម្រិតដំណើរការ</b><br>
<b>Level-1 / TOA</b> = ការចាំងផ្លាតវាស់នៅកំពូលបរិយាកាស (មានផ្សែងអ័ព្ទ)។
<b>Level-2 / SR</b> = ការចាំងផ្លាតលើផ្ទៃដី ក្រោយកែតម្រូវបរិយាកាស — ល្អបំផុតសម្រាប់វិភាគ។<br>
<b>Raw DN</b> គឺជាចំនួនគត់ដែលអ្នកផ្ដល់ទិន្នន័យរក្សាទុក (Sentinel-2៖ ការចាំងផ្លាត × 10000;
Landsat C2 L2៖ ការចាំងផ្លាត = DN × 0.0000275 − 0.2)។ <b>Scaled</b> បំប្លែងទៅជាតម្លៃ 0–1។<br>
<b>Composite</b> ផ្សំរូបភាពច្រើនកាលបរិច្ឆេទ ភីកសែលម្ដងមួយៗ; តម្លៃមធ្យម (median) លុបពពក និងស្រមោលភាគច្រើន។
ភាពច្បាស់លំហ៖ Sentinel-2 10–60 ម៉ែត្រ, Landsat 30 ម៉ែត្រ (pan 15 ម៉ែត្រ), Sentinel-1 10 ម៉ែត្រ។""",
    },
    "composite": {
        "en": """<b>Band composites and contrast enhancement</b><br>
A screen shows three colours (Red, Green, Blue). We can put <i>any</i> three bands into them.
True colour = Red, Green, Blue bands. False colour (NIR, Red, Green) shows healthy vegetation in red
because leaves reflect near-infrared strongly.<br>
Raw values occupy a narrow range, so images look dark. <b>Contrast stretching</b> maps that range to
0–255: <i>min–max</i>, <i>percent clip</i> (ignore the 2% extremes), <i>standard deviation</i>, and
<i>histogram equalization</i> (spreads values so each grey level has equal frequency).
Use the <b>spectral profile</b> tool to click pixels and compare their spectral signatures.""",
        "km": """<b>ការផ្សំប៊ែន និងការពង្រឹងកម្រិតពន្លឺ</b><br>
អេក្រង់បង្ហាញពណ៌បី (ក្រហម បៃតង ខៀវ)។ យើងអាចដាក់ប៊ែន <i>ណាមួយ</i> ចំនួនបីចូល។
ពណ៌ពិត = ប៊ែនក្រហម បៃតង ខៀវ។ ពណ៌ក្លែង (NIR, ក្រហម, បៃតង) បង្ហាញរុក្ខជាតិល្អជាពណ៌ក្រហម
ព្រោះស្លឹកចាំងផ្លាតអ៊ីនហ្វ្រារ៉េដជិតខ្លាំង។<br>
តម្លៃដើមស្ថិតក្នុងចន្លោះតូច ធ្វើឲ្យរូបភាពងងឹត។ <b>ការលាតកម្រិតពន្លឺ</b> ផ្ទេរចន្លោះនោះទៅ 0–255៖
<i>min–max</i>, <i>percent clip</i> (មិនគិតតម្លៃជ្រុល 2%), <i>គម្លាតស្តង់ដារ</i>, និង
<i>ការធ្វើឲ្យស្មើអ៊ីស្តូក្រាម</i> (ចែកចាយតម្លៃឲ្យកម្រិតពណ៌ប្រផេះនីមួយៗមានប្រេកង់ស្មើគ្នា)។
ប្រើឧបករណ៍ <b>ខ្សែកោងវិសាលគម</b> ដើម្បីចុចលើភីកសែល ហើយប្រៀបធៀបហត្ថលេខាវិសាលគម។""",
    },
    "indices": {
        "en": """<b>Spectral indices</b><br>
An index combines bands to highlight one feature and reduce illumination effects.
NDVI = (NIR − Red)/(NIR + Red): water &lt; 0, bare soil ≈ 0.1–0.2, dense vegetation &gt; 0.6.
MNDWI uses Green and SWIR to map water; NDBI highlights built-up areas; NBR shows burnt areas.
<b>Otsu</b> chooses a threshold automatically from the histogram (e.g. water / non-water).
Indices must be computed on <i>reflectance</i>; on raw DN the ratio is the same for SR bands but
not for indices with constants (EVI, SAVI) — use scaled reflectance for those.""",
        "km": """<b>សន្ទស្សន៍វិសាលគម</b><br>
សន្ទស្សន៍ផ្សំប៊ែនដើម្បីបញ្ជាក់លក្ខណៈមួយ និងកាត់បន្ថយឥទ្ធិពលពន្លឺ។
NDVI = (NIR − ក្រហម)/(NIR + ក្រហម)៖ ទឹក &lt; 0, ដីទទេ ≈ 0.1–0.2, រុក្ខជាតិក្រាស់ &gt; 0.6។
MNDWI ប្រើបៃតង និង SWIR ដើម្បីគូសផែនទីទឹក; NDBI បញ្ជាក់តំបន់សំណង់; NBR បង្ហាញតំបន់ឆេះ។
<b>Otsu</b> ជ្រើសតម្លៃកំណត់ដោយស្វ័យប្រវត្តិពីអ៊ីស្តូក្រាម (ឧ. ទឹក / មិនមែនទឹក)។
សន្ទស្សន៍ត្រូវគណនាលើ <i>ការចាំងផ្លាត</i>; សន្ទស្សន៍ដែលមានថេរ (EVI, SAVI) ត្រូវប្រើតម្លៃ scaled។""",
    },
    "filters": {
        "en": """<b>Spatial filters and PCA</b><br>
A filter moves a small window (kernel) over the image. <i>Low-pass</i> (mean, Gaussian, median)
smooths noise; <i>high-pass</i> sharpens; <i>edge</i> filters (Sobel, Laplacian) find boundaries such as
roads, canals and field edges. Median keeps edges better than mean.<br>
<b>Principal Component Analysis</b> rotates correlated bands into uncorrelated components. PC1 holds
most of the variance (overall brightness); later PCs hold contrasts and noise.""",
        "km": """<b>តម្រងលំហ និង PCA</b><br>
តម្រងរំកិលបង្អួចតូច (kernel) លើរូបភាព។ <i>Low-pass</i> (mean, Gaussian, median) ធ្វើឲ្យរលោង
កាត់បន្ថយសំឡេងរំខាន; <i>High-pass</i> ធ្វើឲ្យច្បាស់; តម្រង<i>គែម</i> (Sobel, Laplacian) រកព្រំដែនដូចជា
ផ្លូវ ប្រឡាយ និងគែមស្រែ។ Median រក្សាគែមបានល្អជាង mean។<br>
<b>ការវិភាគសមាសធាតុចម្បង (PCA)</b> បង្វិលប៊ែនដែលទាក់ទងគ្នា ទៅជាសមាសធាតុមិនទាក់ទងគ្នា។ PC1 មានបម្រែបម្រួល
ច្រើនបំផុត (ពន្លឺរួម); PC ក្រោយៗមានភាពផ្ទុយគ្នា និងសំឡេងរំខាន។""",
    },
    "unsupervised": {
        "en": """<b>Unsupervised classification</b><br>
The computer groups pixels with similar spectra into <i>clusters</i> without any training data.
<b>K-means</b>: choose k, assign each pixel to the nearest cluster centre, move centres, repeat.
<b>ISODATA</b> also splits wide clusters, merges close ones and removes tiny ones.<br>
Afterwards the analyst must <b>label</b> each cluster (e.g. cluster 3 = water) using the image and
local knowledge. Ask for more clusters than classes, then merge them by giving the same name.""",
        "km": """<b>ចំណាត់ថ្នាក់ដោយមិនមានការណែនាំ</b><br>
កុំព្យូទ័របែងចែកភីកសែលដែលមានវិសាលគមស្រដៀងគ្នាទៅជា <i>ក្រុម</i> ដោយគ្មានទិន្នន័យហ្វឹកហាត់។
<b>K-means</b>៖ ជ្រើស k, ដាក់ភីកសែលនីមួយៗទៅក្រុមដែលនៅជិតបំផុត, ផ្លាស់ទីចំណុចកណ្ដាល, ធ្វើម្ដងទៀត។
<b>ISODATA</b> ក៏បំបែកក្រុមធំ ច្របាច់បញ្ចូលក្រុមជិតគ្នា និងលុបក្រុមតូចៗ។<br>
បន្ទាប់មក អ្នកវិភាគត្រូវ <b>ដាក់ឈ្មោះ</b> ក្រុមនីមួយៗ (ឧ. ក្រុម 3 = ទឹក) ដោយផ្អែកលើរូបភាព និងចំណេះដឹងមូលដ្ឋាន។
ស្នើក្រុមច្រើនជាងថ្នាក់ ហើយបញ្ចូលគ្នាដោយដាក់ឈ្មោះដូចគ្នា។""",
    },
    "supervised": {
        "en": """<b>Supervised classification</b><ol>
<li>Define classes (id, name, colour).</li>
<li>Digitize <b>training polygons</b> over pure, typical areas of each class — spread them across the
whole image, at least 10–20 polygons per class.</li>
<li>Check the <b>signatures</b> and <b>separability</b> (Jeffries–Matusita &gt; 1.9 = good; &lt; 1.0 = classes overlap).</li>
<li>Choose a classifier: <i>Minimum Distance</i> (nearest class mean), <i>Parallelepiped</i> (box of ±k·σ),
<i>Maximum Likelihood</i> (Gaussian probability, uses covariance), <i>Spectral Angle Mapper</i>
(shape of the spectrum, ignores brightness), <i>Random Forest / SVM</i> (machine learning).</li>
<li>Smooth with a majority filter, then assess accuracy with <b>independent</b> reference data.</li></ol>""",
        "km": """<b>ចំណាត់ថ្នាក់ដោយមានការណែនាំ</b><ol>
<li>កំណត់ថ្នាក់ (លេខ ឈ្មោះ ពណ៌)។</li>
<li>គូរ <b>ពហុកោណហ្វឹកហាត់</b> លើតំបន់សុទ្ធ និងតំណាងនៃថ្នាក់នីមួយៗ — ចែកចាយលើរូបភាពទាំងមូល
យ៉ាងតិច 10–20 ពហុកោណក្នុងមួយថ្នាក់។</li>
<li>ពិនិត្យ <b>ហត្ថលេខាវិសាលគម</b> និង <b>ភាពបំបែកបាន</b> (Jeffries–Matusita &gt; 1.9 = ល្អ; &lt; 1.0 = ថ្នាក់ត្រួតគ្នា)។</li>
<li>ជ្រើសវិធី៖ <i>Minimum Distance</i> (មធ្យមថ្នាក់ជិតបំផុត), <i>Parallelepiped</i> (ប្រអប់ ±k·σ),
<i>Maximum Likelihood</i> (ប្រូបាប Gaussian ប្រើកូវ៉ារ្យង់), <i>Spectral Angle Mapper</i>
(រាងវិសាលគម មិនគិតពន្លឺ), <i>Random Forest / SVM</i> (រៀនម៉ាស៊ីន)។</li>
<li>ធ្វើឲ្យរលោងដោយ majority filter រួចវាយតម្លៃភាពត្រឹមត្រូវដោយទិន្នន័យយោង <b>ឯករាជ្យ</b>។</li></ol>""",
    },
    "accuracy": {
        "en": """<b>Accuracy assessment</b><br>
Compare the map with reference data that were <i>not</i> used for training. The <b>confusion
(error) matrix</b> has map classes in rows and reference classes in columns.<br>
<b>Overall accuracy</b> = correct / total. <b>Producer's accuracy</b> = correct / column total
(1 − omission error). <b>User's accuracy</b> = correct / row total (1 − commission error).
<b>Kappa</b> corrects for chance agreement (&gt; 0.8 strong, 0.4–0.8 moderate).<br>
Generate <i>stratified random points</i>, label each one from high-resolution imagery or field
visits in the field <i>ref_class</i>, then compute.""",
        "km": """<b>ការវាយតម្លៃភាពត្រឹមត្រូវ</b><br>
ប្រៀបធៀបផែនទីជាមួយទិន្នន័យយោងដែល <i>មិន</i> បានប្រើសម្រាប់ហ្វឹកហាត់។ <b>ម៉ាទ្រីសកំហុស</b>
មានថ្នាក់ផែនទីតាមជួរដេក និងថ្នាក់យោងតាមជួរឈរ។<br>
<b>ភាពត្រឹមត្រូវរួម</b> = ត្រូវ / សរុប។ <b>ភាពត្រឹមត្រូវអ្នកផលិត</b> = ត្រូវ / សរុបជួរឈរ
(1 − កំហុសខកខាន)។ <b>ភាពត្រឹមត្រូវអ្នកប្រើ</b> = ត្រូវ / សរុបជួរដេក (1 − កំហុសបញ្ចូលខុស)។
<b>Kappa</b> កែតម្រូវការចៃដន្យ (&gt; 0.8 ខ្លាំង, 0.4–0.8 មធ្យម)។<br>
បង្កើត <i>ចំណុចចៃដន្យតាមស្រទាប់</i> ដាក់ថ្នាក់ចំណុចនីមួយៗពីរូបភាពច្បាស់ ឬការចុះវាល
ក្នុងវាល <i>ref_class</i> រួចគណនា។""",
    },
    "change": {
        "en": """<b>Change detection</b><br>
<b>Image differencing</b>: subtract an index (e.g. NDVI) of date 1 from date 2. Large negative values =
loss (deforestation, flooding, harvest); large positive = gain. Thresholds at mean ± k·σ.<br>
<b>Post-classification comparison</b>: classify both dates, then cross-tabulate them in a
<i>change matrix</i> ("from → to"). It tells <i>what</i> changed into <i>what</i>, but errors of both
maps add up. Both images should be from the same season and sensor if possible.""",
        "km": """<b>ការរកមើលការប្រែប្រួល</b><br>
<b>ការដករូបភាព</b>៖ ដកសន្ទស្សន៍ (ឧ. NDVI) នៃកាលបរិច្ឆេទ 1 ពីកាលបរិច្ឆេទ 2។ តម្លៃអវិជ្ជមានធំ =
ការបាត់បង់ (កាប់ព្រៃ ទឹកជំនន់ ការប្រមូលផល); តម្លៃវិជ្ជមានធំ = ការកើនឡើង។ តម្លៃកំណត់ = មធ្យម ± k·σ។<br>
<b>ការប្រៀបធៀបក្រោយចំណាត់ថ្នាក់</b>៖ ចាត់ថ្នាក់រូបភាពទាំងពីរ រួចធ្វើតារាងឆ្លង
<i>ម៉ាទ្រីសការប្រែប្រួល</i> ("ពី → ទៅ")។ វាប្រាប់ថា <i>អ្វី</i> បានប្រែទៅជា <i>អ្វី</i> ប៉ុន្តែកំហុសនៃផែនទីទាំងពីរ
បូកបញ្ចូលគ្នា។ រូបភាពទាំងពីរគួរតែមកពីរដូវ និងឧបករណ៍ចាប់ដូចគ្នា។""",
    },
    "script": {
        "en": """<b>Scripting like Google Earth Engine — on your own computer (LEE)</b><br>
A script describes a <i>collection</i> of images (dataset, dates, place, cloud limit), reduces it to one image
(<code>median()</code>, <code>mean()</code>, <code>qualityMosaic()</code>…), computes new bands
(<code>normalizedDifference</code>, <code>expression</code>, <code>slope</code>…) and shows results with
<code>Map.addLayer</code>, <code>print</code> and charts. Only the AOI pixels are read, clouds are masked
automatically and reflectance is already 0–1. Choose an example, change dates or thresholds, press <b>Run</b>.
Large AOIs are computed at coarser pixels — set <code>lee.config(scale=30)</code> yourself to go faster.""",
        "km": """<b>សរសេរស្គ្រីបដូច Google Earth Engine — លើកុំព្យូទ័ររបស់អ្នក (LEE)</b><br>
ស្គ្រីបមួយពិពណ៌នា<i>បណ្ដុំ</i>រូបភាព (សំណុំទិន្នន័យ កាលបរិច្ឆេទ ទីតាំង កម្រិតពពក) បង្រួមវាទៅជារូបភាពតែមួយ
(<code>median()</code>, <code>mean()</code>, <code>qualityMosaic()</code>…) គណនាប៊ែនថ្មី
(<code>normalizedDifference</code>, <code>expression</code>, <code>slope</code>…) ហើយបង្ហាញលទ្ធផលដោយ
<code>Map.addLayer</code>, <code>print</code> និងគំនូសតាង។ អានតែភីកសែលក្នុងតំបន់សិក្សា បិទពពកដោយស្វ័យប្រវត្តិ
ហើយការចាំងផ្លាតមានតម្លៃ 0–1 រួចជាស្រេច។ ជ្រើសឧទាហរណ៍ កែកាលបរិច្ឆេទ ឬតម្លៃកំណត់ រួចចុច <b>ដំណើរការ</b>។
តំបន់ធំត្រូវគណនាដោយភីកសែលធំជាង — កំណត់ <code>lee.config(scale=30)</code> ដើម្បីឲ្យលឿនជាង។""",
    },
}


def lesson(key, lang):
    d = LESSONS.get(key, {})
    return d.get(lang) or d.get("en", "")
