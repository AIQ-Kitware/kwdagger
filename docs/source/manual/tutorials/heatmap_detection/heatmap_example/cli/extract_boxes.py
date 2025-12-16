"""Turn saliency rasters into bounding box detections."""

from __future__ import annotations
import kwcoco
import scriptconfig as scfg
import ubelt as ub
import numpy as np
from skimage import measure


class ExtractBoxesConfig(scfg.DataConfig):
    """CLI options for extracting boxes from saliency maps."""

    coco_fpath = scfg.Value(None, help="Input kwcoco file with saliency aux data")
    dst_coco_fpath = scfg.Value("pred_boxes.kwcoco.json", help="Where to write box predictions")
    heatmap_channel = scfg.Value("saliency", help="Channel name to search for")
    threshold = scfg.Value(0.5, help="Threshold for binarizing saliency")
    min_area = scfg.Value(4, help="Filter out tiny components")

    @classmethod
    def main(cls, argv=1, **kwargs):
        config = cls.cli(argv=argv, data=kwargs, strict=True, verbose="auto")
        run_extract_boxes(**config)


def extract_boxes_from_heatmap(
    heatmap: np.ndarray,
    *,
    threshold: float,
    min_area: int,
):
    """
    Extract bounding boxes from a saliency heatmap.

    Args:
        heatmap (H, W ndarray): float array in [0,1] or uint8
        threshold (float): threshold for binarizing heatmap
        min_area (int): skip small connected components

    Returns:
        list of dicts:
            {
                "bbox": [x, y, w, h],
                "score": float
            }

    Example:
        >>> import numpy as np
        >>> # Create a heatmap with two bright blobs
        >>> heatmap = kwimage.checkerboard(dsize=(64, 64), num_squares=3)
        >>> heatmap = kwimage.morphology(heatmap, mode='erode', kernel=8)
        >>> detections = extract_boxes_from_heatmap(
        ...     heatmap, threshold=0.7, min_area=1
        ... )
        >>> # Two detections found
        >>> len(detections)
        >>> # xdoctest: +REQUIRES(--show)
        >>> # xdoctest: +REQUIRES(module:kwplot)
        >>> import kwimage
        >>> import kwplot
        >>> kwplot.autompl()
        >>> # Make a simple background image matching the heatmap size
        >>> canvas = kwimage.atleast_3channels(heatmap).copy()
        >>> from kwimage import Boxes
        >>> boxes_obj = Boxes([d["bbox"] for d in detections], "xywh")
        >>> boxes_obj.draw_on(canvas, color='kitware_blue')
        >>> kwplot.imshow(canvas, fnum=1, doclf=1,
        ...               title='extract_boxes_from_heatmap demo')
        >>> kwplot.show_if_requested()
    """
    import kwimage
    mask = heatmap >= threshold

    labeled = measure.label(mask)
    props = measure.regionprops(labeled, intensity_image=heatmap)

    detections = []
    for region in props:
        if region.area < min_area:
            continue
        min_row, min_col, max_row, max_col = region.bbox
        box = kwimage.Box.coerce([min_col, min_row, max_col, max_row], format='ltrb')
        bbox = list(map(float, box.to_xywh().data))
        score = float(region.mean_intensity) if region.mean_intensity is not None else 1.0
        detections.append({"bbox": bbox, "score": score})
    return detections


def run_extract_boxes(
    coco_fpath,
    dst_coco_fpath="pred_boxes.kwcoco.json",
    heatmap_channel="saliency",
    threshold=0.5,
    min_area=4,
):
    src_coco = kwcoco.CocoDataset.coerce(coco_fpath)
    pred_coco = src_coco.copy()
    pred_coco.clear_annotations()

    catid = pred_coco.ensure_category(name="object")

    for image_id in pred_coco.imgs.keys():
        coco_img = pred_coco.coco_image(image_id)

        # load saliency channel
        delayed = coco_img.imdelay(channels=heatmap_channel)
        heatmap = delayed.finalize()

        # Ensure the heatmap is 2d
        if len(heatmap.shape) == 3:
            assert heatmap.shape[2] == 1
            heatmap = heatmap[..., 0]

        detections = extract_boxes_from_heatmap(
            heatmap,
            threshold=float(threshold),
            min_area=int(min_area),
        )

        for det in detections:
            pred_coco.add_annotation(
                image_id=image_id,
                bbox=det["bbox"],
                score=det["score"],
                category_id=catid,
            )

    pred_coco.fpath = str(dst_coco_fpath)
    pred_coco.bundle_dpath = str(ub.Path(dst_coco_fpath).parent)
    ub.Path(dst_coco_fpath).parent.ensuredir()
    pred_coco.dump(dst_coco_fpath, newlines=True)
    print(f'Write to {dst_coco_fpath}')
    return {"boxes_coco": dst_coco_fpath}


if __name__ == "__main__":
    ExtractBoxesConfig.main()
