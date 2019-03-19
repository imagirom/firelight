from ..utils.dim_utils import SpecFunction, convert_dim
import matplotlib.cm as cm
import matplotlib.colors as colors
from matplotlib.pyplot import get_cmap
import torch
import numpy as np
import collections

def hsv_to_rgb(h, s, v):  # TODO: remove colorsys dependency
    """
    Converts a color from HSV to RGB

    Parameters
    ----------
    h : float
    s : float
    v : float

    Returns
    -------
        np.ndarray
    """
    i = np.floor(h*6.0)
    f = h * 6 - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    i = i % 6

    if i == 0:
        rgb = (v, t, p)
    elif i == 1:
        rgb = (q, v, p)
    elif i == 2:
        rgb = (p, v, t)
    elif i == 3:
        rgb = (p, q, v)
    elif i == 4:
        rgb = (t, p, v)
    else:
        rgb = (v, p, q)

    return np.array(rgb, dtype=np.float32)


def get_distinct_colors(n, min_sat=.5, min_val=.5):
    """
    Generates a list of distinct colors, evenly separated in HSV space.
    Parameters
    ----------
    n : int
        Number of colors to generate.
    min_sat : float
        Minimum saturation.
    min_val : float
        Minimum brightness

    Returns
    -------
        np.ndarray
    """
    huePartition = 1.0 / (n + 1)
    hues = np.arange(0, n) * huePartition
    saturations = np.random.rand(n) * (1-min_sat) + min_sat
    values = np.random.rand(n) * (1-min_val) + min_val
    return np.stack([hsv_to_rgb(h, s, v) for h, s, v in zip(hues, saturations, values)], axis=0)


def colorize_segmentation(seg, ignore_label=None, ignore_color=(0, 0, 0)):
    """
    Randomly colorize a segmentation with a set of distinct colors.

    Parameters
    ----------
    seg : np.ndarray
        Segmentation to be colorized. Can have any shape, but data type must be discrete.
    ignore_label : int
        Label of segment to be colored with ignore_color.
    ignore_color : tuple
        RGB values of colors of segment labeled with ignore_label.

    Returns
    -------
        np.ndarray
    """
    assert isinstance(seg, np.ndarray)
    assert seg.dtype.kind in ('u', 'i')
    if ignore_label is not None:
        ignore_ind = seg == ignore_label
    seg = seg - np.min(seg)
    colors = get_distinct_colors(np.max(seg) + 1)
    np.random.shuffle(colors)
    result = colors[seg]
    if ignore_label is not None:
        result[ignore_ind] = ignore_color
    return result


def _from_matplotlib_cmap(cmap):
    """
    Converts the name of a matplotlib colormap to a colormap function that can be applied to a numpy array.
    arrays.

    Parameters
    ----------
    cmap : str
        Name of the matplotlib colormap

    Returns
    -------
        callable
    """
    if isinstance(cmap, str):
        cmap = get_cmap(cmap)
    cNorm = colors.Normalize(vmin=0, vmax=1)
    scalarMap = cm.ScalarMappable(norm=cNorm, cmap=cmap)
    return scalarMap.to_rgba


def _add_alpha(img):
    """
    Adds an alpha channel to a tensor, whose last dimension corresponds to RGB color.

    Parameters.
    ----------
    img : torch.Tensor

    Returns
    -------
        torch.Tensor
    """
    alpha_shape = list(img.shape)
    alpha_shape[-1] = 1
    return torch.cat([img, torch.ones(alpha_shape, dtype=img.dtype)], dim=-1)


class ScaleTensor(SpecFunction):
    def __init__(self, invert=False, value_range=None, scale_robust=False, quantiles=(0.05, 0.95), keep_centered=False):
        super(ScaleTensor, self).__init__(
            in_specs={'tensor': ['Pixels']},
            out_spec=['Pixels']
        )
        # TODO: decouple quantlies from scale axis (allow e.g. 0.1 -> 0.05)
        self.invert = invert
        self.value_range = value_range
        self.scale_robust = scale_robust
        self.quantiles = quantiles
        self.keep_centered = keep_centered
        self.eps = 1e-12

    def quantile_scale(self, tensor, quantiles=None, return_params=False):
        quantiles = self.quantiles if quantiles is None else quantiles
        q_min = np.percentile(tensor.numpy(), 100 * self.quantiles[0])
        q_max = np.percentile(tensor.numpy(), 100 * self.quantiles[1])
        scale = (quantiles[1] - quantiles[0]) / max(q_max - q_min, self.eps)
        offset = quantiles[0] - q_min * scale
        # scaled tensor is tensor * scale + offset
        if return_params:
            return scale, offset
        else:
            return tensor * scale + offset

    def scale_tails(self, tensor):
        t_min, t_max = torch.min(tensor), torch.max(tensor)
        if t_min < 0:
            ind = tensor < self.quantiles[0]
            tensor[ind] -= t_min
            tensor[ind] *= self.quantiles[0] / max(self.quantiles[0] - t_min, self.eps)
        if t_max > 1:
            ind = tensor > self.quantiles[1]
            tensor[ind] -= self.quantiles[1]
            tensor[ind] *= (1 - self.quantiles[1]) / max(t_max - self.quantiles[1], self.eps)
            tensor[ind] += self.quantiles[1]
        return tensor

    def internal(self, tensor):
        if self.invert:
            tensor *= -1
        if not self.keep_centered:
            if self.value_range is not None or not self.scale_robust:
                # just scale to [0, 1], nothing fancy
                value_range = (torch.min(tensor), torch.max(tensor)) if self.value_range is None else self.value_range
                tensor -= value_range[0]
                tensor /= max(value_range[1] - value_range[0], self.eps)
            else:
                quantiles = list(self.quantiles)
                tensor = self.quantile_scale(tensor, quantiles=quantiles)
                # if less than the whole range is used, do so
                rescale = False
                if torch.min(tensor) > 0:
                    quantiles[0] = 0
                    rescale = True
                if torch.max(tensor) < 1:
                    quantiles[1] = 0
                    rescale = True
                if rescale:
                    tensor = self.quantile_scale(tensor, quantiles=quantiles)
                # if the tails lie outside the range, rescale them
                tensor = self.scale_tails(tensor)

        else:
            if self.value_range is not None or not self.scale_robust:
                value_range = (torch.min(tensor), torch.max(tensor)) if self.value_range is None else self.value_range
                value_range = (-max(*value_range), max(*value_range))
                tensor -= value_range[0]
                tensor /= max(value_range[1] - value_range[0], self.eps)
            else:
                quantile = self.quantiles[0] if isinstance(self.quantiles, (tuple, list)) else self.quantiles
                symmetrized_tensor = torch.cat([tensor, -tensor])
                scale, offset = self.quantile_scale(symmetrized_tensor, (quantile, 1-quantile), return_params=True)
                tensor = tensor * scale + offset
                tensor = self.scale_tails(tensor)
        tensor = tensor.clamp(0, 1)
        return tensor


class Colorize(SpecFunction):
    def __init__(self, background_label=None, background_color=None, opacity=1.0, value_range=None, cmap=None,
                 colorize_jointly=None, scaling_options=None):
        """
        Constructs an object used for the colorization / color normalization of tensors. The output tensor has a
        length 4 RGBA output dimension labeled 'Color'.

        Parameters
        ----------
        background_label : int or tuple
            Value of input tensor that will be colored with background color
        background_color : int or tuple
            Color that will be assigned to regions of the input having the value background_label.
        opacity : float
            Multiplier that will be applied to alpha channel. Useful to blend images with OverlayVisualizer
        value_range : tuple
            Range the input data will lie in (e.g. [-1, 1] for l2-normalized vectors). This range will be mapped
            linearly to the unit interval [0, 1].
            If not specified, the output data will be scaled to use the full range [0, 1].
        cmap : str or callable
            If str, has to be the name of a matplotlib colormap, to be used to color grayscale data. (see
            https://matplotlib.org/examples/color/colormaps_reference.html for a list of available colormaps).
            If callable, has to be function that adds a RGBA color dimension at the end, to an input numpy array with
            values between 0 and 1.
        colorize_jointly : list
            List of the names of dimensions that should be colored jointly. Default: ['W', 'H', 'D'].
            data points separated only in these dimensions will be scaled equally. See StackVisualizer for an example
            usage.
        """
        colorize_jointly = ('W', 'H', 'D') if colorize_jointly is None else list(colorize_jointly)
        collapse_into = {'rest': 'B'}
        collapse_into.update({d: 'Pixels' for d in colorize_jointly})
        super(Colorize, self).__init__(in_specs={'tensor': ['B', 'Pixels', 'Color']},
                                       out_spec=['B', 'Pixels', 'Color'],
                                       collapse_into=collapse_into)
        self.cmap = _from_matplotlib_cmap(cmap) if isinstance(cmap, str) else cmap
        self.background_label = background_label
        self.background_color = (0, 0, 0, 0) if background_color is None else tuple(background_color)
        if len(self.background_color) == 3:
            self.background_color += (1,)
        assert len(self.background_color) == 4, f'{len(self.background_color)}'
        self.opacity = opacity

        scaling_options = dict() if scaling_options is None else scaling_options
        if value_range is not None:
            scaling_options['value_range'] = value_range
        self.scale_tensor = ScaleTensor(**scaling_options)

    def add_alpha(self, img):
        return _add_alpha(img)

    def normalize_colors(self, tensor):
        tensor = tensor.permute(2, 0, 1)
        # TODO: vectorize
        # shape Color, Batch, Pixel
        for i in range(min(tensor.shape[0], 3)):  # do not scale alpha channel
            for j in range(tensor.shape[1]):
                tensor[i, j] = self.scale_tensor(tensor=(tensor[i, j], ['Pixels']))
        tensor = tensor.permute(1, 2, 0)
        return tensor

    def internal(self, tensor):
        if self.background_label is not None:
            bg_mask = tensor == self.background_label
            bg_mask = bg_mask[..., 0]
        else:
            bg_mask = None

        # add color if there is none
        if tensor.shape[-1] == 1:  # no color yet
            # if continuous, normalize colors
            if (tensor % 1 != 0).any():
                tensor = self.normalize_colors(tensor)

            # if a colormap is specified, apply it
            if self.cmap is not None:
                dtype = tensor.dtype
                tensor = self.cmap(tensor.numpy()[..., 0])[..., :3]  # TODO: Why truncate alpha channel?
                tensor = torch.tensor(tensor, dtype=dtype)
            # if continuous and no cmap, use grayscale
            elif (tensor % 1 != 0).any() or (torch.min(tensor) == 0 and torch.max(tensor) == 1):
                # if tensor is continuous or greyscale, default to greyscale with intensity in alpha channel
                tensor = torch.cat([torch.zeros_like(tensor.repeat(1, 1, 3)), tensor], dim=-1)

            else:  # tensor is discrete with not all values in {0, 1}, hence color the segments randomly
                tensor = torch.Tensor(colorize_segmentation(tensor[..., 0].numpy().astype(np.int32)))
        elif tensor.shape[-1] in [3, 4]:
            assert self.cmap is None
            tensor = self.normalize_colors(tensor)
        else:
            assert False, f'{tensor.shape}'

        # add alpha channel
        if tensor.shape[-1] == 3:
            tensor = self.add_alpha(tensor)
        assert tensor.shape[-1] == 4
        tensor[..., -1] *= self.opacity  # multiply alpha channel with opacity

        if bg_mask is not None and torch.sum(bg_mask) > 0:
            assert tensor.shape[-1] == len(self.background_color)
            tensor[bg_mask.byte()] = torch.Tensor(np.array(self.background_color)).type_as(tensor)

        return tensor


if __name__ == '__main__':

    print('-'*100)
    tensor = torch.Tensor([0, 1, 2, 3, 4])
    colorize = Colorize(cmap='inferno')
    out, spec = colorize(tensor=(tensor, 'W'), out_spec=['W', 'Color'], return_spec=True)
    print(out)
    print(out.shape)
    print(spec)

