"""Simulate segmentation predictions by writing saliency maps."""

from __future__ import annotations

import kwcoco
import kwimage
import numpy as np
import scriptconfig as scfg
import ubelt as ub
from scipy import ndimage


class PredictHeatmapConfig(scfg.DataConfig):
    """CLI options for writing saliency maps."""

    coco_fpath = scfg.Value(None, help="Input ground-truth kwcoco dataset")
    dst_coco_fpath = scfg.Value("pred_saliency.kwcoco.json", help="Output kwcoco file")
    aux_dpath = scfg.Value("saliency", help="Where to store written saliency maps")
    saliency_channel = scfg.Value("saliency", help="Name of the saliency channel")
    sigma = scfg.Value(7.0, help="Gaussian blur applied to binary mask")
    noise = scfg.Value(0.01, help="Amount of random noise added before clipping")
    seed = scfg.Value(0, help="Random seed for reproducible noise")

    @classmethod
    def main(cls, argv=1, **kwargs):
        config = cls.cli(argv=argv, data=kwargs, strict=True, verbose="auto")
        run_predict_heatmap(**config)


def run_predict_heatmap(
    coco_fpath,
    dst_coco_fpath="pred_saliency.kwcoco.json",
    aux_dpath="saliency",
    saliency_channel="saliency",
    sigma=7.0,
    noise=0.01,
    seed=0,
): 
    rng = np.random.default_rng(seed)

    src_coco = kwcoco.CocoDataset.coerce(coco_fpath)
    pred_coco = src_coco.copy()
    pred_coco.fpath = str(dst_coco_fpath)
    pred_coco.bundle_dpath = str(ub.Path(dst_coco_fpath).parent)

    aux_dpath = ub.Path(aux_dpath)
    aux_dpath.ensuredir()

    for gid, img in pred_coco.imgs.items():
        shape = (img["height"], img["width"])
        mask = _rasterize_segmentation(pred_coco, gid, shape)

        smooth = ndimage.gaussian_filter(mask.astype(float), sigma=sigma)
        if smooth.max() > 0:
            smooth = smooth / smooth.max()
        if noise:
            smooth = np.clip(smooth + rng.normal(scale=noise, size=smooth.shape), 0, 1)

        aux_fname = f"{ub.Path(img.get('name', f'image-{gid}')).stem}_saliency.png"
        aux_fpath = aux_dpath / aux_fname
        kwimage.imwrite(aux_fpath, (smooth * 255).astype(np.uint8))

        coco_img = pred_coco.coco_image(gid)
        coco_img.add_asset(
            file_name=ub.Path(aux_fpath).relative_to(ub.Path(dst_coco_fpath).parent),
            channels=saliency_channel,
            width=shape[1],
            height=shape[0],
            warp_aux_to_img=kwimage.Affine.eye(),
        )

    pred_coco.dump(dst_coco_fpath, newlines=True)
    return {
        "saliency_coco": dst_coco_fpath,
    }


def _rasterize_segmentation(coco: kwcoco.CocoDataset, gid: int, shape) -> np.ndarray:
    """Rasterize polygons or boxes for a single image."""

    annots = coco.annots(gid=gid)
    canvas = np.zeros(shape, dtype=np.float32)
    for ann in annots.objs:
        seg = ann.get("segmentation")
        if seg:
            mask = kwimage.Mask.coerce(seg, dims=shape).to_mask(dims=shape).data.astype(bool)
        elif ann.get("bbox"):
            boxes = kwimage.Boxes([ann["bbox"]], "xywh")
            mask = boxes.to_polygons().to_mask(dims=shape).data.astype(bool)
        else:
            continue
        canvas |= mask
    return canvas


if __name__ == "__main__":
    PredictHeatmapConfig.main()
