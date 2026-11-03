# GeoDevelop Processing Lab

The public GitHub Pages dashboard now includes **Processing Lab**. Heavy work runs in an authenticated Python backend, not on GitHub Pages. No keys, imagery or results belong in Git commits.

## Implemented workflows

| Workflow | Required inputs | Output and limits |
|---|---|---|
| Natural-language agent | Configured Ollama/OpenAI-compatible open-source model endpoint, or optional local Transformers Qwen model | Reads uploaded data and job status, searches places/scenes, and prepares concrete jobs. It cannot execute generated code or mark results verified. Users submit proposed jobs. |
| Automatic alignment | Overlapping RGB orthomosaics or ordinary equal-size RGB images | SIFT/affine registration with inlier, spatial-spread, scale and rotation checks. Failed checks stop change calculation. No local elastic registration. |
| Large GeoTIFF processing | Georeferenced rasters, optionally a geographic crop | Streamed upload (default 5 GiB limit), windowed raster IO, 512-pixel cores plus halo; GeoTIFF outputs. Jobs can still take a long time over large areas. Raw photographs require separate orthomosaic generation. |
| Building and road changes | Two RGB observations and configured approved SAM 3 weights on CUDA | Before/after masks, candidate changes, previews and instance features for buildings. Road prompting is experimental. Neither is a validated inventory. |
| Landsat temperature | Landsat 8/9 C2 L2 ST_B10 DN plus QA_PIXEL for both dates, or authorized Earth Engine acquisition | QA-screened Celsius temperature differences. Surface temperature is not air temperature or a climate trend. |
| Flood screening | Same-relative-orbit calibrated Sentinel-1 VV dB rasters, or authorized Earth Engine acquisition | Threshold/change-based candidate flood masks and area. Uploaded imagery lacks automatic terrain/JRC exclusions; the Earth Engine path includes them. Vegetation/urban flooding can be missed. |
| Exposure and review | Completed georeferenced building and flood jobs | Candidate building exposure. Recorded reviewer identity, evidence, decisions and human-confirmed counts. Exposure is not structural damage. |
| Reports | Completed job | GeoTIFF masks, JSON provenance, previews, and PDF with limitations and recorded reviews. |

**Verification:** predictions start unverified. The app does not independently verify damage or building counts. A human must inspect each instance and record dated evidence. No trained structural-damage classifier or ground-truth validation dataset is bundled.

## Windows/local setup

Use Python 3.12 and run commands from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
$env:GEODEVELOP_API_KEY = '<private random access key>'
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/lab.html` or the GitHub Pages **Processing Lab** tab. Enter the runtime URL and access key. Keys stay in the browser tab's memory; they are not saved in localStorage. A random key is printed on backend startup if none is supplied. The `.env.example` file documents variables; the application does not automatically load `.env` files.

The core backend runs without a GPU. Building/road inference requires official SAM 3, PyTorch with CUDA, approved weights, and `HF_TOKEN` or `SAM3_CHECKPOINT`. The GPU image installs SAM 3. CPU tasks do not silently substitute fake masks.

For a local open-source language model:

```powershell
ollama pull qwen2.5:7b
$env:GEODEVELOP_LLM_URL = 'http://127.0.0.1:11434/v1'
$env:GEODEVELOP_LLM_MODEL = 'qwen2.5:7b'
```

Alternatively install `transformers accelerate torch` and set `GEODEVELOP_LLM_PROVIDER=transformers`; the default is `Qwen/Qwen2.5-1.5B-Instruct`. This small prototype model can make planning mistakes. Review its proposals.

For satellite acquisition, authorize Earth Engine in the runtime and set `EE_PROJECT`. The Earth Engine project must be registered for the applicable use. Never upload a service-account key to GitHub Pages. Before/after date windows are end-exclusive and limited to 120 days; study bounds are limited to 0.25 degrees per side.

## Google Colab

Open `docs/GeoDevelop-Processing-Colab.ipynb`. It installs the backend and official SAM 3, reads `HF_TOKEN` from Colab Secrets and can authenticate Earth Engine. The notebook starts a local API in the Colab runtime. A public HTTPS tunnel is a separate explicit step and must retain API-key authentication. Free Colab GPU availability and session duration are not guaranteed.

## Google Cloud

Two container recipes are included:

```text
docker build -t geodevelop-cpu .
docker build -f Dockerfile.gpu -t geodevelop-gpu .
```

`Dockerfile` supports CPU analysis. `Dockerfile.gpu` requires an NVIDIA CUDA-capable service and downloads approved weights only at runtime from private secrets. Set the server's `PORT`, API key, origins and data path. The GPU Docker build and a live Cloud deployment must be tested in the target environment before calling them deployed.

For an initial Cloud Run prototype:

- Enable billing and the relevant Cloud Run/build/registry APIs only after reviewing cost.
- GPU inference needs a supported GPU region, quota and configuration. The CPU container alone does not run SAM 3.
- This version has one in-process worker and local job state. Use one instance. Background processing needs CPU allocation outside requests; request-only CPU allocation is unsuitable.
- Cloud Run local disk is ephemeral. Download results before the instance is recycled. For durable multi-instance production, replace the local queue/state with Cloud Tasks/Cloud Run Jobs and durable object/database storage; that production architecture is not included here.
- Keep the application bearer key even if the service is reachable publicly, restrict CORS, and store HF/LLM credentials in Secret Manager. Per-user accounts and tenant isolation are not implemented.

No paid cloud resources are created by this repository.

## Validation

```powershell
python -m pip install pytest
python -m pytest backend/tests -q
```

Tests exercise known-shift registration, failed alignment, Landsat scaling/QA masks, permanent-water and missing-data exclusions, tiled temperature processing, duplicate-instance suppression, authentication and evidence-required review. They do not establish real-world flood, road or damage accuracy.

## Scientific sources

- USGS Landsat C2 ST scaling: https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature
- Earth Engine Landsat 8: https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LC08_C02_T1_L2
- Earth Engine Sentinel-1: https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD
- Official SAM 3: https://github.com/facebookresearch/sam3
