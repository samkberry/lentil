import math
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import matplotlib

from lentil.constants_utils import *

class FieldPlot:
    def __init__(self):
        self.zdata = None
        self.xticks = None
        self.yticks = None
        self.xdata = None
        self.ydata = None
        self.wdata = None  # Mystical fourth dimension

        self.xmin = None
        self.ymin = None
        self.xmax = None
        self.ymax = None
        self.zmin = None
        self.zmax = None
        self.wmin = None
        self.wmax = None
        self.title = "Untitled"
        self.xlabel = "image location x"
        self.ylabel = "image location y"
        self.zlabel = "z-axis"

        self.xreverse = False
        self.yreverse = False

        self.contours = None

        self.hlines = None

        self.alpha = 0.6
    
    @property
    def _xmin(self):
        if self.xmin is None:
            return np.min(self.xticks)
        else:
            return self.xmin
    @property
    def _ymin(self):
        if self.ymin is None:
            return np.min(self.yticks)
        else:
            return self.ymin
    @property
    def _xmax(self):
        if self.xmax is None:
            return np.max(self.xticks)
        else:
            return self.xmax
    @property
    def _ymax(self):
        if self.ymax is None:
            return np.max(self.yticks)
        else:
            return self.ymax
    @property
    def _zmax(self):
        if self.zmax is None:
            return np.max(self.zdata)
        else:
            return self.zmax
    @property
    def _zmin(self):
        if self.zmin is None:
            return np.min(self.zdata)
        else:
            return self.zmin
    @property
    def _wmax(self):
        if self.wmax is None:
            return np.max(self.wdata)
        else:
            return self.wmax
    @property
    def _wmin(self):
        if self.wmin is None:
            return np.min(self.wdata)
        else:
            return self.wmin

    @property
    def _contours(self):
        inc = np.clip((self._zmax - self._zmin) / 20, 0.002, 0.05)
        contours = np.arange(int(self._zmin / inc) * inc - inc, self._zmax + inc, inc)
        return contours

    def set_diffraction_limits(self, freq=AUC, low_fstop=LOW_BENCHMARK_FSTOP, high_fstop=HIGH_BENCHBARK_FSTOP, graphaxis='z'):
        low_perf = diffraction_mtf(freq, low_fstop)
        high_perf = diffraction_mtf(freq, high_fstop)
        setattr(self, graphaxis + "min", low_perf)
        setattr(self, graphaxis + "max", high_perf)

    def contour2d(self, ax=None, show=True):
        # Move on to plotting results
        if ax is None:
            fig, ax = plt.subplots()

        inc = np.clip((self._zmax - self._zmin) / 20, 0.002, 0.05)
        contours = np.arange(int(self._zmin / inc) * inc - inc, self._zmax + inc, inc)
        # contours = np.arange(0.0, 1.0, 0.005)
        
        colors = []
        linspaced = np.linspace(0.0, 1.0, len(contours))
        for lin, line in zip(linspaced, contours):
            colors.append(plt.cm.jet(1.0 - lin))
            # colors.append(colorsys.hls_to_rgb(lin * 0.8, 0.4, 1.0))

        if self.xreverse:
            ax.set_xlim(self._xmax, self._xmin)
        else:
            ax.set_xlim(self._xmin, self._xmax)

        if self.yreverse:
            ax.set_ylim(self._ymax, self._ymin)
        else:
            ax.set_ylim(self._ymin, self._ymax)

        CS = ax.contourf(self.xticks, self.yticks, self.zdata, contours, colors=colors, extend='both')
        CS2 = ax.contour(self.xticks, self.yticks, self.zdata, contours, colors=('black',))
        plt.xlabel(self.xlabel)
        plt.ylabel(self.ylabel)
        plt.clabel(CS2, inline=1, fontsize=10)
        plt.title(self.title)
        if show:
            plt.show()
            return None
        return ax

    def smooth2d(self, ax=None, show=True):
        # Move on to plotting results
        # if ax is None:
        #     fig, ax = plt.subplots()

        # inc = np.clip((self._zmax - self._zmin) / 20, 0.002, 0.05)
        # contours = np.arange(int(self._zmin / inc) * inc - inc, self._zmax + inc, inc)
        # contours = np.arange(0.0, 1.0, 0.005)

        # colors = []
        # linspaced = np.linspace(0.0, 1.0, len(contours))
        # for lin, line in zip(linspaced, contours):
        #     colors.append(plt.cm.jet(1.0 - lin))
            # colors.append(colorsys.hls_to_rgb(lin * 0.8, 0.4, 1.0))

        # if self.xreverse:
        #     ax.set_xlim(self._xmax, self._xmin)
        # else:
        #     ax.set_xlim(self._xmin, self._xmax)
        #
        # if self.yreverse:
        #     ax.set_ylim(self._ymax, self._ymin)
        # else:
        #     ax.set_ylim(self._ymin, self._ymax)

        print(self.zdata.shape)
        print(self.zdata)

        print(self._zmin)
        print(self._zmax)
        print(self.zmin)
        print(self.zmax)
        imdata = np.clip(1.0 - (self.zdata - self._zmin) / (self._zmax - self._zmin), 0.0, 1.0)
        print(imdata)
        plt.imshow(imdata, cmap=plt.cm.jet, interpolation='lanczos', vmin=0.0, vmax=1.0)
        plt.xlabel(self.xlabel)
        plt.ylabel(self.ylabel)
        # plt.clabel(CS2, inline=1, fontsize=10)

        plt.title(self.title)
        if show:
            plt.show()
            return None
        return

    def projection3d(self, ax=None, show=True):
        if len(self.zdata.shape) is not 2:
            raise ValueError("zdata must be be 2d")
        if ax is None:
            fig = plt.figure()
            ax = fig.add_subplot(111, projection='3d')

        if self.xreverse:
            ax.set_xlim(self._xmax, self._xmin)
        else:
            ax.set_xlim(self._xmin, self._xmax)

        if self.yreverse:
            ax.set_ylim(self._ymax, self._ymin)
        else:
            ax.set_ylim(self._ymin, self._ymax)

        ax.set_zlim(self._zmin, self._zmax)

        x, y = np.meshgrid(self.xticks, self.yticks)

        alpha = self.alpha
        # print(self.wdata)
        # print(self._wmin)
        # print(self._wmax)
        # exit()

        cmap = plt.cm.jet  # Base colormap
        my_cmap = cmap(np.arange(cmap.N))  # Read colormap colours
        my_cmap[:, -1] = alpha  # Set colormap alpha
        new_cmap = np.ndarray((256, 4))

        if self.wdata is not None:
            facecolours = plt.cm.jet(np.clip(1.0 - ((self.wdata - self._wmin) / (self._wmax - self._wmin)), 0.0, 1.0))
            norm = matplotlib.colors.Normalize(vmin=self._wmin, vmax=self._wmax)
        else:
            facecolours = plt.cm.jet(np.clip(1.0 - ((self.zdata - self._zmin) / (self._zmax - self._zmin)), 0.0, 1.0))
            norm = matplotlib.colors.Normalize(vmin=self._zmin, vmax=self._zmax)

        edgecolours = 'b'
        facecolours[:, :, 3] = alpha

        for a in range(256):
            mod = 0.5 - math.cos(a / 256 * math.pi) * 0.5
            new_cmap[a, :] = my_cmap[int(mod * 256), :]

        surf = ax.plot_surface(x, y, self.zdata, facecolors=facecolours, norm=norm, edgecolors=edgecolours,
                               rstride=1, cstride=1, linewidth=1, antialiased=True)
        ax.set_xlabel(self.xlabel)
        ax.set_ylabel(self.ylabel)
        ax.set_zlabel(self.zlabel)
        ax.set_title(self.title)
        if show:
            plt.show()
            return None
        return ax

    def plot(self, plot_type=None, *args, **kwargs):
        if plot_type is None or plot_type == CONTOUR2D:
            return self.contour2d( *args, **kwargs)
        elif plot_type == PROJECTION3D:
            return self.projection3d(*args, **kwargs)
        elif plot_type == SMOOTH2D:
            return self.smooth2d(*args, **kwargs)



class Scatter2D(FieldPlot):
    def __init__(self):
        super().__init__()
        self.ylog = False
        self.xlog = False

    @property
    def _xmin(self):
        if self.xmin is None:
            return np.min(self._xdata[self.isfinite])
        else:
            return self.xmin

    @property
    def _ymin(self):
        if self.ymin is None:
            print(self.isfinite)
            print(self.ydata)
            # print(self.ydata[self.isfinite])
            return np.min(self._ydata[self.isfinite])
        else:
            return self.ymin

    @property
    def _xmax(self):
        if self.xmax is None:
            return np.max(self._xdata[self.isfinite])
        else:
            return self.xmax

    @property
    def _ymax(self):
        if self.ymax is None:
            return np.max(self._ydata[self.isfinite])
        else:
            return self.ymax

    @property
    def isfinite(self):
        x = np.isfinite(self.xdata)
        y = np.isfinite(self.ydata)
        return x * y

    @property
    def _ydatanonan(self):
        return self._ydata[self.isfinite]
    @property
    def _xdatanonan(self):
        return self._xdata[self.isfinite]

    @property
    def _xdata(self):
        return np.array(self.xdata)
    @property
    def _ydata(self):
        return np.array(self.ydata)

    def smoothplot(self,
                   color=None,
                   label=None,
                   lineformat="-",
                   marker=None,
                   remove_outliers_sigma=None,
                   k=1,
                   span=None,
                   span_auto_factor=4.0,
                   points_limit=10.0,
                   extra_args=None,
                   extra_kwargs=None,
                   show=True):
        x_data = np.array(self.xdata)
        y_data = np.array(self.ydata)
        if extra_args is None:
            extra_args = []
        if extra_kwargs is None:
            extra_kwargs = {}
        x_plot = np.linspace(x_data.min(), x_data.max(), 100)
        OK = np.isfinite(y_data)
        if span is None:
            span = (x_data.max() - x_data.min()) / span_auto_factor
        while True:
            y_plot = []
            for x in x_plot:
                weights = get_rcos_window2(x_data[OK], x, span)
                points = weights.sum()
                if points > points_limit:
                    ixs = np.argsort(x_data[OK])

                    fn = interpolate.UnivariateSpline(x_data[OK][ixs], y_data[OK][ixs], weights, k=k, s=float("inf"))
                    y_plot.append(fn(x))
                else:
                    y_plot.append(float("nan"))
            if remove_outliers_sigma is None:
                break
            x_plot_valid = x_plot[np.isfinite(y_plot)]
            y_plot_valid = np.array(y_plot)[np.isfinite(y_plot)]
            smooth_interpolator = interpolate.InterpolatedUnivariateSpline(x_plot_valid, y_plot_valid, k=1)
            diffs_squared = (smooth_interpolator(x_data) - y_data) ** 2
            variance = diffs_squared.mean()
            OK = diffs_squared < (variance * remove_outliers_sigma)
            remove_outliers_sigma = None
        if marker is not None:
            plt.plot(x_data[OK], y_data[OK], '.', alpha=0.05, label='_nolegend_', marker=marker, color=color, **extra_kwargs)

        plt.grid(True, which='both', color='black', alpha=0.14, linestyle='-', linewidth=1, zorder=-999)
        plt.ylim(self._ymin, self._ymax)
        plt.xlim(self._xmin, self._xmax)
        plt.title(self.title)
        plt.xlabel(self.xlabel)
        plt.ylabel(self.ylabel)
        if self.hlines is not None:
            for line in self.hlines:
                plt.hlines(line, 0.0, 1.0, linestyles='dotted',colors='gray')
        if self.ylog:
            plt.yscale('log')
        if self.xlog:
            plt.xscale('log')
        plt.plot(x_plot, y_plot, lineformat, *extra_args, label=label, color=color, **extra_kwargs)
        plt.legend()
        if show:
            plt.show()


def get_rcos_window2(times, centre, half_span):
    deltatimes = np.clip((times - centre) / half_span, -1.0, 1.0)
    return np.cos(math.pi * deltatimes) + 1.0000001


COLOURS = ['red',
           'orangered',
           'darkorange',
           'green',
           'blue',
           'darkviolet',
           'deeppink',
           'black']

