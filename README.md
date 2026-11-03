# GeoDevelop AI

A geospatial dashboard with an authenticated Python processing backend.

**Public dashboard:** https://talhanaseer-gis.github.io/Geo-develop-AI/docs/#lab

## Processing Lab (v0.2)

The new lab connects to a local, Colab or cloud runtime for a tool-using natural-language agent, automatic alignment, tiled GeoTIFF processing, Landsat surface temperature, Sentinel-1 flood screening, SAM 3 building/road candidates, exposure and evidence-based human review.

Read [PROCESSING.md](PROCESSING.md) for setup, input requirements, cloud deployment limits and validation. The interface can be live while the backend is disconnected. A GPU, approved weights, a configured language model and Earth Engine authorization are separate runtime dependencies. These workflows are prototypes, not independently validated damage or building inventories.

## Earlier MVP

A geospatial dashboard for satellite scene discovery, two-date drone image comparison, and a Google Colab workflow for Meta SAM 3 building segmentation.

## Open the dashboard

Publish the `docs` folder with GitHub Pages: repository Settings → Pages → Deploy from a branch → main → /docs → Save. Use the URL GitHub reports after a successful deployment.

The application is a static HTML/CSS/JavaScript site. No build step or API secret is required for the current interface. You can also serve `docs` locally with `python -m http.server 8766 --directory docs`, then open http://127.0.0.1:8766/.

## Features and current status

- Satellite map, place search, polygon drawing and GeoJSON boundary upload.
- Sentinel-2 scene metadata search through Microsoft Planetary Computer.
- Before/after PNG, JPEG and WebP inputs, swipe viewer, RGB-difference screening, visual export and print-to-PDF.
- SAM 3 image-job export and Colab notebook; import of completed result JSON, candidate-region overlays, review notes and reporting controls.
- Recorded Lahore Earth Engine NDVI pilot, clearly distinguished from live calculations.

SAM 3 runs in the user's authenticated Colab GPU session, not on GitHub Pages. Model access approval and a compatible GPU runtime are required. Model inference and its generated PDF have not yet been verified. Candidate pixel regions are not verified building counts. GeoTIFF georeferencing, automatic image registration, Landsat processing and a general language-model assistant are not implemented.

## Colab

Open [the prepared notebook](https://colab.research.google.com/drive/1xHGMiSlRj-pvuxDYD4zPKjoE6KTEhZQy) or upload `docs/GeoDevelop-SAM3-Colab.ipynb` to Colab. Request model access at https://huggingface.co/facebook/sam3. Keep HF_TOKEN in Colab Secrets; never commit tokens or private imagery to this repository.

See [SETUP.md](SETUP.md) for processing limits, runtime compatibility and verification status.

## Data sources

- [Planetary Computer Sentinel-2](https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a)
- [OpenStreetMap](https://www.openstreetmap.org/copyright)
- [Meta SAM 3](https://github.com/facebookresearch/sam3)

Uploaded images stay in browser memory until the user exports a job and uploads it to Colab. Basemap, place-search and satellite-catalog requests use external services. No secret is bundled.
