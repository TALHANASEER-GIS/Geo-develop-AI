"""GeoDevelop SAM 3 Colab pipeline. Candidate regions, not verified buildings."""
import base64
import hashlib
import io
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

SCHEMA = 'geodevelop.sam3.result.v1'

def decode_image(value):
    if not isinstance(value, str) or not value.startswith('data:image/png;base64,'):
        raise ValueError('Expected dashboard PNG input.')
    raw = base64.b64decode(value.split(',', 1)[1], validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > 24000000:
            raise ValueError('Image exceeds pilot size limit.')
        rgba = image.convert('RGBA')
        if np.any(np.asarray(rgba)[:, :, 3] != 255):
            raise ValueError('Use opaque RGB images, without transparent/no-data areas.')
        rgb = rgba.convert('RGB')
    return rgb, hashlib.sha256(raw).hexdigest()

def load_job(path):
    if Path(path).stat().st_size > 90 * 1024 * 1024:
        raise ValueError('Job exceeds 90 MB.')
    job = json.loads(Path(path).read_text())
    if job.get('schema') != 'geodevelop.sam3.job.v1' or job.get('alignmentConfirmed') is not True:
        raise ValueError('Export a job from GeoDevelop and confirm alignment first.')
    before, hash_before = decode_image(job['before']['image'])
    after, hash_after = decode_image(job['after']['image'])
    if before.size != after.size:
        raise ValueError('Images must have the same dimensions and aligned extent.')
    if max(before.size) > 4096 or before.width * before.height > 8000000:
        raise ValueError('Use an aligned pilot crop up to 4096 pixels per side and 8 megapixels.')
    return job, before, after, hash_before, hash_after

def png_data(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()

def starts(length, tile, overlap):
    if length <= tile:
        return [0]
    return sorted(set(list(range(0, length-tile+1, tile-overlap)) + [length-tile]))

def infer_union(image, processor, prompt='building', tile=768, overlap=128):
    import torch
    union = np.zeros((image.height, image.width), dtype=bool)
    detections = 0
    xs, ys = starts(image.width, tile, overlap), starts(image.height, tile, overlap)
    for y in ys:
        for x in xs:
            crop = image.crop((x, y, min(x+tile, image.width), min(y+tile, image.height)))
            # SAM3's official example uses BF16. On older GPUs use FP32 for compatibility.
            with torch.inference_mode():
                state = processor.set_image(crop)
                result = processor.set_text_prompt(state=state, prompt=prompt)
                masks = result['masks'].detach().cpu().numpy()
                masks = masks.reshape((-1, crop.height, crop.width))
                merged = masks.any(axis=0) if len(masks) else np.zeros((crop.height, crop.width), bool)
                union[y:y+crop.height, x:x+crop.width] |= merged
                detections += len(masks)
            del state, result, masks
            torch.cuda.empty_cache()
            print(f'Tile ({x}, {y}) complete', flush=True)
    # Tile detections can duplicate objects: do not present them as a building count.
    return union, detections

def filter_regions(mask, minimum_pixels):
    labels, count = ndimage.label(mask)
    sizes = np.bincount(labels.ravel())
    keep = sizes >= minimum_pixels
    keep[0] = False
    filtered = keep[labels]
    return filtered, int(np.count_nonzero(keep))

def compare_masks(before, after, tolerance=2, minimum_pixels=25):
    if before.shape != after.shape:
        raise ValueError('Mask shapes differ.')
    # Buffer suppresses small edge offsets; it does not perform image registration.
    earlier_buffer = ndimage.binary_dilation(before, iterations=tolerance) if tolerance else before
    later_buffer = ndimage.binary_dilation(after, iterations=tolerance) if tolerance else after
    additions, n_add = filter_regions(after & ~earlier_buffer, minimum_pixels)
    removals, n_remove = filter_regions(before & ~later_buffer, minimum_pixels)
    return additions, removals, n_add, n_remove

def overlay(image, mask, color):
    rgb = np.asarray(image).copy()
    rgb[mask] = (rgb[mask] * .45 + np.array(color) * .55).astype(np.uint8)
    return Image.fromarray(rgb)

def run_job(path, processor, output_directory, confidence=.5, tolerance=2, minimum_pixels=25):
    import torch
    job, before, after, hash_before, hash_after = load_job(path)
    prompt = 'building'
    before_mask, _ = infer_union(before, processor, prompt)
    after_mask, _ = infer_union(after, processor, prompt)
    additions, removals, n_add, n_remove = compare_masks(before_mask, after_mask, tolerance, minimum_pixels)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    before_mask_image = Image.fromarray(before_mask.astype('uint8')*255)
    after_mask_image = Image.fromarray(after_mask.astype('uint8')*255)
    before_mask_image.save(output/'before_building_mask.png')
    after_mask_image.save(output/'after_building_mask.png')
    Image.fromarray(additions.astype('uint8')*255).save(output/'candidate_additions_mask.png')
    Image.fromarray(removals.astype('uint8')*255).save(output/'candidate_removals_mask.png')
    visual = overlay(overlay(after, additions, [0,220,110]), removals, [255,65,75])
    visual.save(output/'building_change_overlay.png')
    # Keep browser imports lightweight; original-resolution masks are in the ZIP.
    def preview(image):
        result = image.copy()
        result.thumbnail((1400,1400))
        return png_data(result)
    try:
        commit = subprocess.check_output(['git','-C','/content/sam3','rev-parse','HEAD'], text=True).strip()
    except Exception:
        commit = 'unknown'
    report = dict(schema=SCHEMA, status='completed', model='Meta SAM 3', jobId=job['jobId'],
        generated=datetime.now(timezone.utc).isoformat(), modelSourceCommit=commit, illustrative=bool(job.get('illustrative',False)),
        modelWeights='facebook/sam3/sam3.pt', prompt=prompt,
        confidenceThreshold=confidence, maskProbabilityThreshold=.5,
        tileSize=768, tileOverlap=128, tolerancePixels=tolerance, minimumRegionPixels=minimum_pixels,
        width=before.width, height=before.height, beforeName=job['before']['name'], afterName=job['after']['name'],
        beforeDate=job['before'].get('date'), afterDate=job['after'].get('date'),
        inputSHA256={'beforePNG':hash_before,'afterPNG':hash_after},
        candidateAdditionRegions=n_add, candidateRemovalRegions=n_remove,
        candidateAdditionPixels=int(additions.sum()), candidateRemovalPixels=int(removals.sum()),
        beforeBuildingMaskPixels=int(before_mask.sum()), afterBuildingMaskPixels=int(after_mask.sum()),
        georeferenced=False, alignment='User confirmed; not automatically registered',
        beforePreview=preview(before), afterPreview=preview(after), overlayPreview=preview(visual),
        limitations='Candidate connected pixel regions are NOT building counts. No ground area or geolocation. Changes may be segmentation errors, shadows, vegetation, occlusion or misalignment. Manual review required.',
        runtime={'torch':torch.__version__,'cuda':torch.version.cuda,'gpu':torch.cuda.get_device_name(0)})
    (output/'geodevelop_result.json').write_text(json.dumps(report, indent=2))
    before.thumbnail((1400,1400)); after.thumbnail((1400,1400))
    before.save(output/'before_preview.png'); after.save(output/'after_preview.png')
    return report, output

def write_pdf(report, output):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as PDFImage
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.utils import ImageReader
    from xml.sax.saxutils import escape
    styles=getSampleStyleSheet()
    doc=SimpleDocTemplate(str(output/'GeoDevelop-SAM3-report.pdf'))
    story=[Paragraph('GeoDevelop AI - SAM 3 comparison',styles['Title'])]
    if report.get('illustrative'):
        story.append(Paragraph('ILLUSTRATIVE INPUT - not real aerial observations.',styles['Heading2']))
    for text in [f"Job: {report['jobId']}",f"Before: {report['beforeName']} | {report['beforeDate'] or 'Date not supplied'}",f"After: {report['afterName']} | {report['afterDate'] or 'Date not supplied'}",f"Candidate addition regions: {report['candidateAdditionRegions']}; candidate removal regions: {report['candidateRemovalRegions']}",f"Confidence filter: {report['confidenceThreshold']}; edge tolerance: {report['tolerancePixels']} pixels; minimum region: {report['minimumRegionPixels']} pixels",report['limitations']]:
        story.extend([Paragraph(escape(text),styles['BodyText']),Spacer(1,8)])
    for title,name in [('Before','before_preview.png'),('After','after_preview.png'),('Candidates: green additions / red removals','building_change_overlay.png')]:
        story.append(Paragraph(title,styles['Heading2']))
        w,h=ImageReader(str(output/name)).getSize();factor=min(450/w,300/h)
        story.append(PDFImage(str(output/name),width=w*factor,height=h*factor))
    story.extend([Spacer(1,10),Paragraph(escape('SAM3 source commit: '+report['modelSourceCommit']),styles['BodyText'])])
    doc.build(story)
