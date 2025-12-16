"""Simulate segmentation predictions by writing saliency maps."""

from __future__ import annotations
import kwarray
import kwcoco
import kwimage
import scriptconfig as scfg
import ubelt as ub
import numpy as np


class PredictHeatmapConfig(scfg.DataConfig):
    """
    CLI options for writing saliency maps.
    """
    coco_fpath = scfg.Value(None, help="Input ground-truth kwcoco dataset")
    dst_coco_fpath = scfg.Value("heatmap.kwcoco.json", help="Output kwcoco file")
    asset_dpath = scfg.Value("assets/heatmaps", help="Where to store written heatmaps, best practice is to spcify this next to the dst coco file.")
    heatmap_channel = scfg.Value("salient", help="Name of the output heatmap channel")
    sigma = scfg.Value(7.0, help="Gaussian blur applied to binary mask")
    thresh = scfg.Value(0.0, help="Threshold for minimum heatmap value")

    @classmethod
    def main(cls, argv=1, **kwargs):
        """
        Example:
            >>> import sys, ubelt
            >>> sys.path.append(ubelt.expandpath('~/code/kwdagger/docs/source/manual/tutorials/heatmap_detection'))  # modify as needed
            >>> from heatmap_example.cli.predict_heatmap import PredictHeatmapConfig
            >>> import ubelt as ub
            >>> import kwcoco
            >>> dset = kwcoco.CocoDataset.demo('vidshapes8')
            >>> dpath = ub.Path.appdir('kwdagger/tutorial/tests/predict_heatmap').ensuredir()
            >>> kwargs = {}
            >>> kwargs['coco_fpath'] = dset.fpath
            >>> kwargs['dst_coco_fpath'] = dpath / 'out.kwcoco.json'
            >>> kwargs['asset_dpath'] = dpath / 'assets/heatmaps'
            >>> kwargs['thresh'] = 0
            >>> argv = False
            >>> PredictHeatmapConfig.main(argv=argv, **kwargs)
        """
        config = cls.cli(argv=argv, data=kwargs, strict=True, verbose="auto")
        run_predict_heatmap(**config)


def run_predict_heatmap(coco_fpath,
                        dst_coco_fpath="pred_saliency.kwcoco.json",
                        asset_dpath="saliency",
                        heatmap_channel="saliency",
                        sigma=7.0, thresh=0.0):
    """
    Run saliency prediction over all images in a COCO dataset.
    """
    src_coco = kwcoco.CocoDataset.coerce(coco_fpath)

    pred_coco = src_coco.copy()
    pred_coco.reroot(absolute=True)
    pred_coco.fpath = str(dst_coco_fpath)

    dst_parent = ub.Path(dst_coco_fpath).parent
    pred_coco.bundle_dpath = str(dst_parent)

    asset_dpath = ub.Path(asset_dpath).ensuredir()

    for image_id in pred_coco.imgs.keys():

        coco_img = pred_coco.coco_image(image_id)

        smooth = _predict_image_heatmap(
            coco_img=coco_img,
            thresh=thresh,
            sigma=sigma,
        )

        # Write saliency image
        img_name = coco_img.img.get("name", f"image-{image_id}")
        heatmap_fname = f"{ub.Path(img_name).stem}_saliency.png"
        heatmap_fpath = asset_dpath / heatmap_fname

        # For pngs we need to perform quantization.
        # We can mark this in the kwcoco file so the image loads as float32 in
        # subsequent pipelines.
        quantization = {
            'orig_min': 0.0,
            'orig_max': 1.0,
            'quant_min': 0,
            'quant_max': 255,
            'nodata': None,
        }
        smooth_uint8 = (smooth * 255).astype(np.uint8)

        kwimage.imwrite(heatmap_fpath, smooth_uint8)

        rel_path = ub.Path(heatmap_fpath).relative_to(dst_parent)
        # Register as an asset in the COCO dataset
        coco_img.add_asset(
            file_name=rel_path,
            channels=heatmap_channel,
            width=smooth.shape[1],
            height=smooth.shape[0],
            quantization=quantization,
        )

    pred_coco.dump(dst_coco_fpath, newlines=True)
    print(f'Wrote {dst_coco_fpath}')


def _predict_image_heatmap(
    coco_img,
    *,
    sigma: float,
    thresh: float,
) -> np.ndarray:
    """
    Compute a saliency heatmap for a single image.

    This tries to highlight "white blobby things" by:
        * converting RGB -> HSV
        * measuring whiteness = value * (1 - saturation)
        * smoothing with a Gaussian (blobby response)

    Example:
        >>> import kwcoco
        >>> dset = kwcoco.CocoDataset.demo('vidshapes8')
        >>> coco_img = dset.coco_image(1)
        >>> sigma = 7
        >>> thresh = 0.0
        >>> smooth = _predict_image_heatmap(coco_img, sigma=sigma, thresh=thresh)
        >>> # xdoctest: +REQUIRES(--show)
        >>> import kwplot
        >>> kwplot.autompl()
        >>> rgb = coco_img.imdelay().finalize()
        >>> kwplot.imshow(rgb, pnum=(1, 2, 1), title='RGB')
        >>> kwplot.imshow(smooth, pnum=(1, 2, 2), title='White-blob heatmap')
        >>> kwplot.show_if_requested()
    """
    # Load image: (H, W, C)
    img = coco_img.imdelay().finalize()
    img = kwarray.atleast_nd(img, 3)
    rgb01 = kwimage.ensure_float01(img)

    # Convert to HSV to separate brightness & saturation
    hsv = kwimage.convert_colorspace(rgb01, src_space="rgb", dst_space="hsv")
    sat = hsv[..., 1]
    val = hsv[..., 2]

    # Heuristic "whiteness":
    #   * whites are bright (high value)
    #   * and low saturation (near gray)
    whiteness = val * (1.0 - sat)

    # Smooth to emphasize blob-like regions
    smooth = kwimage.gaussian_blur(whiteness, sigma=sigma)

    # Optional threshold: zero-out weak responses
    if thresh is not None:
        smooth = smooth.astype(np.float32)
        smooth[smooth < thresh] = 0.0

    return smooth


if __name__ == "__main__":
    PredictHeatmapConfig.main()
