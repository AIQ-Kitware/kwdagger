"""Turn saliency rasters into bounding box detections."""

from __future__ import annotations

import kwcoco
import kwimage
import scriptconfig as scfg
from skimage import measure
import ubelt as ub


class ExtractBoxesConfig(scfg.DataConfig):
    """CLI options for extracting boxes from saliency maps."""

    coco_fpath = scfg.Value(None, help="Input kwcoco file with saliency aux data")
    dst_coco_fpath = scfg.Value("pred_boxes.kwcoco.json", help="Where to write box predictions")
    saliency_channel = scfg.Value("saliency", help="Channel name to search for")
    threshold = scfg.Value(0.5, help="Threshold for binarizing saliency")
    min_area = scfg.Value(4, help="Filter out tiny components")

    @classmethod
    def main(cls, argv=1, **kwargs):
        config = cls.cli(argv=argv, data=kwargs, strict=True, verbose="auto")
        run_extract_boxes(**config)


def run_extract_boxes(
    coco_fpath,
    dst_coco_fpath="pred_boxes.kwcoco.json",
    saliency_channel="saliency",
    threshold=0.5,
    min_area=4,
):
    src_coco = kwcoco.CocoDataset.coerce(coco_fpath)
    pred_coco = src_coco.copy()
    pred_coco.fpath = str(dst_coco_fpath)
    pred_coco.bundle_dpath = str(ub.Path(dst_coco_fpath).parent)

    catid = pred_coco.ensure_category(name="object")["id"]

    for gid, img in pred_coco.imgs.items():
        sal_fpath = _find_saliency_path(pred_coco, img, saliency_channel)
        if sal_fpath is None:
            continue
        sal_data = kwimage.imread(sal_fpath, space="gray").astype(float)
        if sal_data.max() > 1:
            sal_data = sal_data / 255.0
        mask = sal_data >= float(threshold)
        labeled = measure.label(mask)
        props = measure.regionprops(labeled, intensity_image=sal_data)
        for region in props:
            if region.area < min_area:
                continue
            y0, x0, y1, x1 = region.bbox
            w = x1 - x0
            h = y1 - y0
            bbox = [float(x0), float(y0), float(w), float(h)]
            score = float(region.mean_intensity) if region.mean_intensity is not None else 1.0
            pred_coco.add_annotation(
                image_id=gid,
                bbox=bbox,
                score=score,
                category_id=catid,
            )

    pred_coco.dump(dst_coco_fpath, newlines=True)
    return {"boxes_coco": dst_coco_fpath}


def _find_saliency_path(coco: kwcoco.CocoDataset, img: dict, channel_name: str) -> ub.Path | None:
    """Locate the auxiliary saliency asset on an image."""

    aux_items = img.get("auxiliary", [])
    bundle_root = coco.bundle_dpath or (coco.fpath and ub.Path(coco.fpath).parent)
    bundle_dpath = ub.Path(bundle_root or ".")
    for aux in aux_items:
        channels = kwcoco.FusedChannelSpec.coerce(aux.get("channels", ""))
        if channel_name in channels:
            file_name = aux["file_name"]
            return bundle_dpath / file_name
    return None


if __name__ == "__main__":
    ExtractBoxesConfig.main()
