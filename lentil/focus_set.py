import csv
import math
import os
import logging
from logging import getLogger
from operator import itemgetter
from scipy import optimize, interpolate, stats

from lentil.sfr_point import SFRPoint
from lentil.sfr_field import SFRField, NotEnoughPointsException
from lentil.plot_utils import FieldPlot, Scatter2D, COLOURS
from lentil.constants_utils import *

import prysm

log = getLogger(__name__)
log.setLevel(logging.DEBUG)
log.setLevel(logging.INFO)

ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
log.addHandler(ch)

STORE = []

class FocusOb:
    """
    Stores focus data, including position, sharpness (mtf/sfr), fit curves and bounds
    """
    def __init__(self, focuspos=None, sharp=None, interpfn=None, curvefn=None, lowbound=None, highbound=None):
        self.focuspos = focuspos
        self.sharp = sharp
        self.interpfn = interpfn
        self.curvefn = curvefn
        self.lowbound = lowbound
        self.highbound = highbound
        self.focus_data = None
        self.sharp_data = None
        self.x_loc = None
        self.y_loc = None

    @classmethod
    def get_midpoint(cls, a, b):
        new = cls()
        mid_x = (a.focuspos + b.focuspos) * 0.5
        new.focuspos = mid_x

        def interp_merge(in_x):
            return (a.interpfn(in_x) + b.interpfn(in_x)) * 0.5
        def curve_merge(in_x):
            return (a.curvefn(in_x) + b.curvefn(in_x)) * 0.5

        new.sharp = interp_merge(mid_x)
        new.curvefn = curve_merge
        new.interpfn = interp_merge
        new.x_loc = (a.x_loc + b.x_loc) / 2
        new.y_loc = (a.y_loc + b.y_loc) / 2
        return new


class FitError(Exception):
    def __init__(self, error, fitpos: FocusOb):
        super().__init__(error)
        self.fitpos = fitpos


class FocusSet:
    """
    A range of fields with stepped focus, in order
    """

    def __init__(self, rootpath=None, rescan=False, include_all=False, use_calibration=True):
        self.fields = []
        self.lens_name = rootpath
        self._focus_data = None
        self.focus_scale_label = "Focus position (arbritary units)"
        calibration = None
        self.calibration = None
        self.base_calibration = np.ones((64,))
        try:
            if len(use_calibration) == 32:
                calibration = use_calibration
                self.base_calibration = calibration
                use_calibration = True
        except TypeError:
            pass

        self.use_calibration = use_calibration
        filenames = []

        if use_calibration and calibration is None:
            try:
                with open("calibration.csv", 'r') as csvfile:
                    reader = csv.reader(csvfile, delimiter=',', quotechar='|')
                    reader.__next__()
                    calibration = np.array([float(cell) for cell in reader.__next__()])
                    self.base_calibration = calibration
            except FileNotFoundError:
                pass
        if rootpath is None:
            log.warning("Warning, initialising with no field data!")
            return
        try:
            # Attempt to open lentil_data
            with open(os.path.join(rootpath, "slfjsadf" if rescan or include_all else "lentil_data.csv"), 'r')\
                    as csvfile:
                print("Found lentildata")
                csvreader = csv.reader(csvfile, delimiter=',', quotechar='|')
                for row in csvreader:
                    if row[0] == "Relevant filenames":
                        stubnames = row[1:]
                    elif row[0] == "lens_name":
                        self.lens_name = row[1]
                pathnames = [os.path.join(rootpath, stubname) for stubname in stubnames]
                exif = EXIF(pathnames[0])
                self.exif = exif
                for pathname in pathnames:
                    try:
                        self.fields.append(SFRField(pathname=pathname, calibration=calibration, exif=exif))
                    except NotEnoughPointsException:
                        pass
        except FileNotFoundError:
            print("Did not find lentildata, finding files...")
            with os.scandir(rootpath) as it:
                for entry in it:
                    print(entry.path)
                    try:
                        entrynumber = int("".join([s for s in entry.name if s.isdigit()]))
                    except ValueError:
                        continue

                    if entry.is_dir():
                        fullpathname = os.path.join(rootpath, entry.path, SFRFILENAME)
                        print(fullpathname)
                        sfr_file_exists = os.path.isfile(fullpathname)
                        if not sfr_file_exists:
                            continue
                        stubname = os.path.join(entry.name, SFRFILENAME)
                    elif entry.is_file() and entry.name.endswith("sfr"):
                        print("Found {}".format(entry.name))
                        fullpathname = entry.path
                        stubname = entry.name
                    else:
                        continue
                    filenames.append((entrynumber, fullpathname, stubname))

                    filenames.sort()

            if len(filenames) is 0:
                raise ValueError("No fields found! Path '{}'".format(rootpath))

            exif = EXIF(filenames[0][1])
            self.exif = exif
            for entrynumber, pathname, filename in filenames:
                print("Opening file {}".format(pathname))
                try:
                    field = SFRField(pathname=pathname, calibration=calibration, exif=exif)
                except NotEnoughPointsException:
                    continue
                field.filenumber = entrynumber
                field.filename = filename
                self.fields.append(field)
            if not include_all:
                self.remove_duplicated_fields()
                self.find_relevant_fields(writepath=rootpath, freq=AUC)

    def find_relevant_fields(self, freq=DEFAULT_FREQ, detail=1, writepath=None):
        min_ = float("inf")
        max_ = float("-inf")
        x_values, y_values = self.fields[0].build_axis_points(24 * detail, 16 * detail)
        totalpoints = len(x_values) * len(y_values) * 2
        done = 0
        for axis in MERIDIONAL, SAGITTAL:
            for x in x_values:
                for y in y_values:
                    if done % 100 == 0:
                        print("Finding relevant field for point {} of {}".format(done, totalpoints))
                    done += 1
                    fopt = self.find_best_focus(x, y, freq=freq, axis=axis).focuspos
                    if fopt > max_:
                        max_ = fopt
                    if fopt < min_:
                        min_ = fopt
        print("Searched from {} fields".format(len(self.fields)))
        print("Found {} to {} contained all peaks".format(min_, max_))
        minmargin = int(max(0, min_ - 2.0))
        maxmargin = int(min(len(self.fields)-1, max_+ 2.0)+1.0)
        print("Keeping {} to {}".format(minmargin, maxmargin))
        filenumbers = []
        filenames = []
        for field in self.fields[minmargin:maxmargin+1]:
            filenumbers.append(field.filenumber)
            filenames.append(field.filename)
        if writepath:
            with open(os.path.join(writepath, "lentil_data.csv"), 'w') as datafile:
                csvwriter = csv.writer(datafile, delimiter=',', quotechar='|',)
                csvwriter.writerow(["Relevant filenames"]+filenames)
                print("Data saved to lentil_data.csv")

    def plot_ideal_focus_field(self, freq=AUC, detail=1.0, axis=MEDIAL, plot_curvature=True,
                               plot_type=PROJECTION3D, show=True, ax=None, skewplane=False,
                               alpha=0.7, title=None, fix_zlim=None):
        """
        Plots peak sharpness / curvature at each point in field across all focus

        :param freq: Frequency of interest in cy/px (-1 for MTF50)
        :param detail: Alters number of points in plot (relative to 1.0)
        :param axis: SAGGITAL or MERIDIONAL or MEDIAL
        :param plot_curvature: Show curvature if 1, else show mtf/sfr
        :param plot_type: CONTOUR2D or PROJECTION3D
        :param show: Displays plot if True
        :param ax: Pass existing matplotlib axis to use
        :param skewplane: True to build and use deskew plane, or pass existing plane
        :param alpha: plot plane transparency
        :param title: title for plot
        :return: matplotlib axis for reuse, skewplane for reuse
        """

        if axis == ALL_THREE_AXES:
            if not plot_curvature or plot_type is not PROJECTION3D:
                raise ValueError("Plot must be field curvature in 3d for ALL THREE AXES")
            ax, skew = self.plot_ideal_focus_field(freq, detail, MERIDIONAL, show=False, skewplane=skewplane,
                                                   alpha=alpha*0.3)
            ax, skew = self.plot_ideal_focus_field(freq, detail, SAGITTAL, show=False, ax=ax, skewplane=skew, alpha=alpha*0.3)
            return self.plot_ideal_focus_field(freq, detail, MEDIAL, show=show, ax=ax, skewplane=skew, alpha=alpha, fix_zlim=fix_zlim)


        num_sheets = 0  # Number of acceptable focus sheets each side of peak focus
        if title is None:
            title = "Ideal focus field " + self.exif.summary

        gridit, focus_posits, x_values, y_values = self.get_grids(detail)
        sharps = focus_posits.copy()
        if plot_curvature:
            colours = focus_posits.copy()
            z_values_low = focus_posits.copy()
            z_values_high = focus_posits.copy()

        tot_locs = len(focus_posits.flatten())
        locs = 1

        for x_idx, y_idx, x, y in gridit:
            if locs % 50 == 0:
                print("Finding best focus for location {} / {}".format(locs, tot_locs))
            locs += 1
            bestfocus = self.find_best_focus(x, y, freq, axis)

            sharps[y_idx, x_idx] = bestfocus.sharp
            if plot_curvature:
                focus_posits[y_idx, x_idx] = bestfocus.focuspos
                z_values_low[y_idx, x_idx] = bestfocus.lowbound
                z_values_high[y_idx, x_idx] = bestfocus.highbound

        if plot_curvature and skewplane:
            if "__call__" not in dir(skewplane):
                x_int, y_int = np.meshgrid(x_values, y_values)
                print(x_values)
                print(x_int)
                print(y_int)
                print(focus_posits.flatten())
                skewplane = interpolate.SmoothBivariateSpline(x_int.flatten(), y_int.flatten(),
                                                              focus_posits.flatten(), kx=1, ky=1, s=float("inf"))

            for x_idx, x in enumerate(x_values):
                for y_idx, y in enumerate(y_values):
                    sheet = skewplane(x, y)
                    focus_posits[y_idx, x_idx] -= sheet
                    z_values_low[y_idx, x_idx] -= sheet
                    z_values_high[y_idx, x_idx] -= sheet

        if plot_type == CONTOUR2D or plot_type == SMOOTH2D:
            plot = FieldPlot()
            plot.xticks = x_values
            plot.yticks = y_values
            plot.set_diffraction_limits(freq)

            if plot_curvature:
                # contours = np.arange(int(np.amin(focus_posits)*2)/2.0 - 0.5, np.amax(focus_posits)+0.5, 0.5)
                plot.zdata = focus_posits
            else:
                plot.zdata = sharps
            plot.yreverse = True
            plot.xlabel = "Image position x"
            plot.ylabel = "Image position y"
            plot.title = title
            ax = plot.plot(plot_type, ax, show=show)
        else:
            plot = FieldPlot()

            plot.set_diffraction_limits(freq, graphaxis="w")

            plot.xticks = x_values
            plot.yticks = y_values
            plot.set_diffraction_limits(freq, graphaxis='w')
            plot.yreverse = True
            plot.xlabel = "Image position x"
            plot.ylabel = "Image position y"
            plot.zlabel = self.focus_scale_label
            plot.title = title
            plot.zdata = focus_posits
            plot.wdata = sharps
            plot.alpha = alpha
            # print(focus_posits.min(), focus_posits.max())
            if fix_zlim is not None:
                plot.zmin = fix_zlim[0]
                plot.zmax = fix_zlim[1]
            else:
                if ax is not None:
                    # print(ax.get_zlim())
                    plot.zmin = min(focus_posits.min(), ax.get_zlim()[0])
                    plot.zmax = max(focus_posits.max(), ax.get_zlim()[1])
                else:
                    plot.zmin = focus_posits.min()
                    plot.zmax = focus_posits.max()
            ax = plot.projection3d(ax, show=show)
        return ax, skewplane

    def plot_field_curvature_strip_contour(self, freq=DEFAULT_FREQ, axis=MERIDIONAL, theta=THETA_TOP_RIGHT, radius=1.0):
        maxradius = IMAGE_DIAGONAL / 2 * radius
        heights = np.linspace(-1, 1, 41)
        plot_focuses = self.focus_data
        field_focus_locs = np.arange(0, len(self.fields))
        sfrs = np.ndarray((len(plot_focuses), len(heights)))
        for hn, height in enumerate(heights):
            x = np.cos(theta) * height * maxradius + IMAGE_WIDTH/2
            y = np.sin(theta) * height * maxradius + IMAGE_HEIGHT/2
            interpfn = self.get_interpolation_fn_at_point(x, y, freq, axis=axis).interpfn
            height_sfr_interpolated = interpfn(plot_focuses)
            sfrs[:, hn] = height_sfr_interpolated

        plot = FieldPlot()
        plot.set_diffraction_limits(freq=freq)
        plot.xticks = heights
        plot.yticks = plot_focuses
        plot.zdata = sfrs
        plot.xlabel = "Image height"
        plot.ylabel = self.focus_scale_label
        plot.ymin = -110
        plot.ymax = 110
        plot.title = "Edge SFR vs focus position for varying height from centre"
        plot.contour2d()

    def get_interpolation_fn_at_point(self, x, y, freq=DEFAULT_FREQ, axis=MEDIAL, limit=None, skip=1, order=2):
        y_values = []
        if limit is None:
            lowlim = 0
            highlim = len(self.fields)
            fields = self.fields[::skip]
        else:
            lowlim = max(0, limit[0])
            highlim = min(len(self.fields), max(limit[1], lowlim + 3))
            fields = self.fields[lowlim: highlim:skip]

        if axis == MEDIAL:
            for field in fields:
                s = field.interpolate_value(x, y, freq, SAGITTAL)
                m = field.interpolate_value(x, y, freq, MERIDIONAL)
                y_values.append((m + s) * 0.5)
        else:
            for n, field in enumerate(fields):
                y_values.append(field.interpolate_value(x, y, freq, axis))
        y_values = np.array(y_values)
        x_ixs = np.arange(lowlim, highlim, skip)  # Arbitrary focus units
        x_values = self.focus_data[x_ixs]
        interpfn = interpolate.InterpolatedUnivariateSpline(x_values, y_values, k=order)
        pos = FocusOb(interpfn=interpfn)
        pos.focus_data = x_values
        pos.sharp_data = y_values
        return pos

    def find_focus_spacing(self, plot=True):
        # data = np.array(data)
        # plot = FieldPlot()
        # plot.zdata = data
        # plot.xticks = np.linspace(0,1, data.shape[0])
        # plot.yticks = np.linspace(0,1, data.shape[1])
        # plot.smooth2d(show=1)
        freqs = np.arange(3, 32, 1) / 64

        use_minimize = True

        pos = self.get_interpolation_fn_at_point(IMAGE_WIDTH/2, IMAGE_HEIGHT/2, AUC, SAGITTAL)
        focus_values = pos.focus_data[:]

        mtf_means = pos.sharp_data
        # chart_mtf_values = mtf_means
        # data = np.array(data)
        if 0 and plot:
            plot = FieldPlot()
            plot.zdata = chart_mtf_values
            plot.xticks = np.linspace(0,1, chart_mtf_values.shape[0])
            plot.yticks = np.linspace(0,1, chart_mtf_values.shape[1])
            plot.smooth2d(show=1)

        meanpeak_idx = np.argmax(mtf_means)
        meanpeak_pos = focus_values[meanpeak_idx]
        meanpeak = mtf_means[meanpeak_idx]
        # highest_data_y = y_values[highest_data_x_idx]

        # print(highest_data_x_idx)

        if meanpeak_idx > 0:
            x_inc = focus_values[meanpeak_idx] - focus_values[meanpeak_idx-1]
        else:
            x_inc = focus_values[meanpeak_idx+1] - focus_values[meanpeak_idx]

        # y_values = np.cos(np.linspace(-6, 6, len(focus_values))) + 1
        absgrad = np.abs(np.gradient(mtf_means)) / meanpeak
        gradsum = np.cumsum(absgrad)
        distances_from_peak = np.abs(gradsum - np.mean(gradsum[meanpeak_idx:meanpeak_idx+1]))
        shifted_distances = interpolate.InterpolatedUnivariateSpline(focus_values, distances_from_peak, k=1)(focus_values - x_inc*0.5)
        weights = np.clip(1.0 - shifted_distances * 1.3 , 1e-1, 1.0) ** 5

        if 0 and "DEBUGPLOT":
            print(absgrad)
            print(gradsum)
            print(distances_from_peak)
            plt.plot(mtf_means, label="yvals")
            plt.plot(absgrad, label="grad")
            plt.plot(gradsum, label="gradsum")
            plt.plot(distances_from_peak, label="distfrompeak")
            plt.plot(shifted_distances, label="distfrompeakshift")
            plt.plot(weights, label="weights")
            plt.legend()
            plt.show()
            exit()

        fitfn = cauchy

        bounds = fitfn.bounds(meanpeak_pos, meanpeak, x_inc)

        sigmas = 1. / weights
        initial = fitfn.initial(meanpeak_pos, meanpeak, x_inc)
        fitted_params, _ = optimize.curve_fit(fitfn, focus_values, mtf_means,
                                              bounds=bounds, sigma=sigmas, ftol=1e-5, xtol=1e-5,
                                              p0=initial)
        cauchy_peak_x = fitted_params[1]
        cauchy_peak_y = fitted_params[0]
        print("Found peak {:.3f} at {:.3f}".format(cauchy_peak_y, cauchy_peak_x))

        # Move on to get full frequency data

        size = 2
        skip = 3
        slicelow = max(0, int(cauchy_peak_x) - size*skip)
        slicehigh = slicelow + size * skip * 2 + 1
        limit = (slicelow, slicehigh)
        print("Limit", limit)
        datalst = []
        for freq in freqs:
            pos = self.get_interpolation_fn_at_point(IMAGE_WIDTH/2, IMAGE_HEIGHT/2, freq, SAGITTAL, limit=limit, skip=skip)
            pos1 = self.get_interpolation_fn_at_point(IMAGE_WIDTH/2, IMAGE_HEIGHT/2, freq, MERIDIONAL, limit=limit, skip=skip)
            datalst.append((pos.sharp_data[:] + pos1.sharp_data)*0.5)

        chart_mtf_values = np.array(datalst) # [:,::skip]
        mtf_means = chart_mtf_values.mean(axis=0)# [::skip]
        focus_values = pos.focus_data # [::skip]
        max_pos = focus_values[np.argmax(mtf_means)]

        count = 0
        weights = (chart_mtf_values + 20.001) ** 1

        def prysmfit(*params, plot=False):
            if len(params) == 1:
                p = []
                for param in params[0]:
                    p.append(param)
                while len(p) < 6:
                    p.append(0)
                defocus_offset, defocus_step, aberr, a2 , loca, spca , z37 = p
            else:
                p = []
                for param in params:
                    p.append(param)
                while len(p) < 6:
                    p.append(0)
                _, defocus_offset, defocus_step, aberr, a2, loca, spca, z37 = p

            z11 = aberr# * (1.0 - a2)
            z22 = a2
            out = []
            basewv = 0.575
            for n, defocus in enumerate(focus_values):
                mul = 1
                pupil = prysm.NollZernike(Z4=((defocus - defocus_offset) * defocus_step*mul - loca)*basewv,
                                          dia=10, norm=True,
                                          z11=(z11*mul - spca)*basewv,
                                          z22=(z22*mul)*basewv,
                                          z37=(z37*mul)*basewv,
                                          wavelength=0.656,
                                          opd_unit="um",
                                          samples=128)
                pupil2 = prysm.NollZernike(Z4=((defocus - defocus_offset) * defocus_step*mul - loca)*basewv,
                                          dia=10, norm=True,
                                          z11=(z11*mul - spca)*basewv,
                                          z22=(z22*mul)*basewv,
                                          z37=(z37*mul)*basewv,
                                          wavelength=0.486,
                                          opd_unit="um",
                                          samples=128)
                pupil3 = prysm.NollZernike(Z4=((defocus - defocus_offset) * defocus_step*mul)*basewv,
                                          dia=10, norm=True,
                                          z11=(z11*mul)*basewv,
                                          z22=(z22*mul)*basewv,
                                          z37=(z37*mul)*basewv,
                                          wavelength=0.546,
                                          opd_unit="um",
                                          samples=128)
                pupilall = (pupil+pupil+pupil2+ pupil3 + pupil3+pupil3)

                if plot and defocus == max_pos:
                    pupilall.plot2d()
                    plt.show()
                    print(pupilall.slice_x[1])
                    print(pupilall.slice_x[1])
                    plt.plot(np.diff(pupilall.slice_x[1]))
                    plt.show()

                # prysm.PSF.from_pupil(pupil, efl=10*self.exif.aperture).plot2d()
                # plt.show()
                # m = prysm.MTF.from_pupil(pupil, efl=self.exif.aperture * 10)
                # out.append(m.exact_sag(freqs / DEFAULT_PIXEL_SIZE * 1e-3))
                m = prysm.MTF.from_pupil(pupil, efl=self.exif.aperture * 10)
                a1 = m.exact_sag(freqs / DEFAULT_PIXEL_SIZE * 1e-3)
                m = prysm.MTF.from_pupil(pupil2, efl=self.exif.aperture * 10)
                a2 = m.exact_sag(freqs / DEFAULT_PIXEL_SIZE * 1e-3)
                m = prysm.MTF.from_pupil(pupil3, efl=self.exif.aperture * 10)
                a3 = m.exact_sag(freqs / DEFAULT_PIXEL_SIZE * 1e-3)
                out.append((a1 * 2 + a2 + a3 * 3) / 6)
            model_mtf_values = np.array(out).T
            if plot:
                # pass
                # pupil.plot2d()
                # plt.show()
                # plt.plot(model_mtf_values.flatten(), label="Prysm Model {:.3f}λ Z4 step size, {:.3f}λ Z11".format(defocus_step, aberr))
                plt.plot(model_mtf_values.flatten(), label="Prysm Model {:.3f}λ Z4 step size, {:.3f}λ Z11, {:.3f}λ Z22".format(defocus_step, z11, z22))
                plt.plot(chart_mtf_values.flatten(), label="MTF Mapper data")
                plt.xlabel("Focus position (arbitrary units)")
                plt.ylabel("MTF")
                plt.title("Prysm model vs chart ({:.32}-{:.2f}cy/px AUC) {}".format(LOWAVG_NOMBINS[0] / 64, LOWAVG_NOMBINS[-1] / 64, self.exif.summary))
                plt.ylim(0, 1)
                plt.legend()
                plt.show()
            global count
            count += 1
            # print("count", count)
            cost = np.sum((model_mtf_values - chart_mtf_values) ** 2 * weights) * 1e0
            print("Cost {:.0f}: {:.1f} (offset {:.1f}, Z4 step {:.3f}, Z11 {:.3f}, Z22 {:.3f}, Z37 {:.3f}, LoCA{:.3f} SpCA {:.3f}".format(count, cost, defocus_offset, defocus_step, z11, z22, z37, loca, spca))
            if len(params) > 1:
                return model_mtf_values.flatten()


            if plot or count % 1000 == 0:
                if 0:
                    plt.plot(focus_values, out, color="red", label="Model")
                    plt.plot(focus_values, mtf_means, color="green", label="Chart")
                else:

                    for n, (freq, model, chart) in enumerate(zip(freqs, model_mtf_values, chart_mtf_values)):
                        # print(model)
                        # print(chart)
                        color = COLOURS[n % 8]
                        plt.plot(focus_values, model, '--', label="Model", color=color)
                        plt.plot(focus_values, chart, '-', label="Chart", color=color )
                plt.show()

            # print(params)
            # print(meansquare)
            # print(model_mtf_values - chart_mtf_values)
            return cost

        initial = (cauchy_peak_x, 0.15, 0.0, 0.0, 0.0, 0.0, 0.0)
        # initial = [1.60397130e+01, 1.55009658e-01, 1.09410582e-02, 5.32265328e-02]
        # initial = [15.52297645,  0.1583259 ,  0.09895199,  0.02686031,  0.17051755,
        # 0.11403228, -0.01808591]
        """90 mm [ 1.59369683e+01,  1.32080098e-01,  1.13042016e-02,  4.40596034e-02,
        1.43557853e-02,  9.39752958e-03, -3.77584896e-02]"""
        """ 90mm Cost 296: 5.8 (offset 15.9, Z4 step 0.132, Z11 0.011, Z22 0.044 LoCA0.014 SpCA 0.009 Z37 -0.038"""
        """60mm f2.4   1.13582531e-01  1.22821521e-01  1.20100764e-01             nan]
Cost 377: 5.6 (offset 6.8, Z4 step 0.052, Z11 0.018, Z22 0.040 LoCA0.006 SpCA -0.019 Z37 -0.052"""
        "95mm f5.6  widefocus Cost 688: 0.9 (offset 5.5, Z4 step 0.055, Z11 -0.058, Z22 0.008, Z37 -0.004, LoCA0.063 SpCA -0.025"

        """200mm f5/6 [ 6.73759172e+00,  5.51791026e-02, -3.32219646e-02,  5.16100558e-03,
       -7.30845229e-02,  2.46997798e-02,  3.56122742e-03]"""
        bounds = ((-3, max(focus_values)+3), (0.03, 1.0), (-0.5, 0.5), (0.0, 1.0), (-0.4, 0.4), (-0.4, 0.4),  (-0.4, 0.4))
        bounds = ((-3, max(focus_values)+3), (0.03, 1.0), (-0.5, 0.5), (-0.5, 0.5), (-0.4, 0.4), (-0.4, 0.4), (-0.4, 0.4))
        curve_fit_bounds = [], []
        for tup in bounds:
            curve_fit_bounds[0].append(tup[0])
            curve_fit_bounds[1].append(tup[1])
        if 0:
            initial = initial[:4]
            bounds = bounds[:4]
        s = np.array([1.61411274e+01, 1.53759443e-01, 4.44602888e-03, 7.74675870e-02])
        # prysmfit(0, *initial, plot=1)
        if 1:
            # opt = optimize.minimize(prysmfit, initial, method="trust-constr", bounds=bounds)
            opt = optimize.minimize(prysmfit, initial, method="L-BFGS-B" , bounds=bounds)
            print(opt)
            print(opt.x)
            prysmfit(opt.x, plot=True)
            est_defocus_rms_wfe_step = opt.x[1]
            # print(len(list(focus_values)*len(freqs)))
            # print(len(chart_mtf_values.flatten()))
            # exit()
            est_defocus_rms_wfe_step = opt.x[1]
        else:
            fit, _ = optimize.curve_fit(prysmfit, list(focus_values)*len(freqs), chart_mtf_values.flatten(), p0=initial, sigma=1.0 / weights.flatten(), bounds=curve_fit_bounds)
            print(fit)
            prysmfit(0, *fit, plot=1)

            est_defocus_rms_wfe_step = fit[1]



        # log.debug("Fn fit peak is {:.3f} at {:.2f}".format(fitted_params[0], fitted_params[1]))
        # log.debug("Fn sigma: {:.3f}".format(fitted_params[2]))

        # ---
        # Estimate defocus step size
        # ---
        # if "_fixed_defocus_step_wfe" in dir(self):
        #     est_defocus_rms_wfe_step = self._fixed_defocus_step_wfe
        est_defocus_pv_wfe_step = est_defocus_rms_wfe_step * 2 * 3 ** 0.5

        log.info("--- Focus step size estimates ---")
        log.info("    RMS Wavefront defocus error {:8.4f} λ".format(est_defocus_rms_wfe_step))

        longitude_defocus_step_um = est_defocus_pv_wfe_step * self.exif.aperture**2 * 8 * 0.55
        log.info("    Image side focus shift      {:8.3f} µm".format(longitude_defocus_step_um))

        na = 1 / (2.0 * self.exif.aperture)
        theta = np.arcsin(na)
        coc_step = np.tan(theta) * longitude_defocus_step_um * 2

        focal_length_m = self.exif.focal_length * 1e-3

        def get_opposide_dist(dist):
            return 1.0 / (1.0 / focal_length_m - 1.0 / dist)

        lens_angle_of_view = self.exif.angle_of_view
        # print(lens_angle_of_view)
        subject_distance = CHART_DIAGONAL * 0.5 / np.sin(lens_angle_of_view / 2)
        image_distance = get_opposide_dist(subject_distance)

        log.info("    Subject side focus shift    {:8.2f} mm".format((get_opposide_dist(image_distance-longitude_defocus_step_um *1e-6) - get_opposide_dist(image_distance)) * 1e3))
        log.info("    Blur circle  (CoC)          {:8.2f} µm".format(coc_step))

        log.info("Nominal subject distance {:8.2f} mm".format(subject_distance * 1e3))
        log.info("Nominal image distance   {:8.2f} mm".format(image_distance * 1e3))

        return est_defocus_rms_wfe_step, longitude_defocus_step_um, coc_step, image_distance, subject_distance, cauchy_peak_y

    def find_best_focus(self, x, y, freq=DEFAULT_FREQ, axis=MEDIAL, plot=False, show=True, strict=False, fitfn=cauchy,
                        _pos=None, _return_step_data_only=False, _step_est_offset=0.3, _step_estimation_posh=False):
        """
        Get peak SFR at specified location and frequency vs focus, optionally plot.

        :param x: x loc
        :param y: y loc
        :param freq: spacial freq (or MTF50 or AUC)
        :param axis: MERIDIONAL, SAGITTAL or MEDIAL
        :param plot: plot to pyplot if True
        :param strict: do not raise exception if peak cannot be determined definitively
        :return: peak_focus_loc, peak_sfr, low_bound_focus, high_bound_focus, spline interpolation fn, curve_fn
        """
        # print(x,y, freq, axis)
        # print("---------")
        # Go recursive if both planes needed
        if axis == MEDIAL and _pos is None:
            best_s = self.find_best_focus(x, y, freq, SAGITTAL, plot, show, strict)
            best_m = self.find_best_focus(x, y, freq, MERIDIONAL, plot, show, strict)
            mid = FocusOb.get_midpoint(best_s, best_m)
            if 0 and 0.6<calc_image_height(x, y)<0.7:
                print("     ", best_s.x_loc, best_s.y_loc, best_s.focuspos)
                print("     ", best_m.x_loc, best_m.y_loc, best_m.focuspos)
                print("     ", mid.x_loc, mid.y_loc, mid.focuspos)
            # STORE.append(mid.focuspos)
            # print(sum(STORE) / len(STORE))
            return mid

        if _pos is None:
            pos = self.get_interpolation_fn_at_point(x, y, freq, axis)
        else:
            pos = _pos

        x_values = pos.focus_data
        y_values = pos.sharp_data
        interp_fn = pos.interpfn
        # print(x_values)
        # print(y_values)
        # exit()
        # Initial fit guesses
        highest_data_x_idx = np.argmax(y_values)
        highest_data_y = y_values[highest_data_x_idx]
        # print(highest_data_x_idx)

        if 0 and "OLD":
            if highest_data_x_idx < (len(y_values) - 2):
                highest_within_tolerance = y_values > highest_data_y * 0.95
                filtered_x_idxs = (np.arange(len(x_values)) * highest_within_tolerance)[highest_within_tolerance]
                # print(filtered_x_idxs)
                mean_peak_x_idx = filtered_x_idxs.mean()
                mean_peak_x = x_values[filtered_x_idxs].mean()
            else:  # Peak is (maybe almost) off edge
                mean_peak_x = x_values[highest_data_x_idx]
                mean_peak_x_idx = highest_data_x_idx
                if highest_data_x_idx == (len(y_values) - 1):
                    log.warning("Peak is at very end of data, true peak is probably missing")
                else:
                    log.warning("Peak is near end of data, this is not ideal")

        if highest_data_x_idx > 0:
            x_inc = x_values[highest_data_x_idx] - x_values[highest_data_x_idx-1]
        else:
            x_inc = x_values[highest_data_x_idx+1] - x_values[highest_data_x_idx]

        # y_values = np.cos(np.linspace(-6, 6, len(x_values))) + 1
        absgrad = np.abs(np.gradient(y_values)) / highest_data_y
        gradsum = np.cumsum(absgrad)
        distances_from_peak = np.abs(gradsum - np.mean(gradsum[highest_data_x_idx:highest_data_x_idx+1]))
        shifted_distances = interpolate.InterpolatedUnivariateSpline(x_values, distances_from_peak, k=1)(x_values - x_inc*0.5)
        weights = np.clip(1.0 - shifted_distances * 1.3 , 1e-1, 1.0) ** 5
        mean_peak_x = x_values[highest_data_x_idx]

        # shifted_distances = np.concatenate(([distances_from_peak[0]], distances_from_peak[1:] + distances_from_peak[:-1] / 2))
        if 0 and "DEBUGPLOT":
            print(absgrad)
            print(gradsum)
            print(distances_from_peak)
            plt.plot(y_values, label="yvals")
            plt.plot(absgrad, label="grad")
            plt.plot(gradsum, label="gradsum")
            plt.plot(distances_from_peak, label="distfrompeak")
            plt.plot(shifted_distances, label="distfrompeakshift")
            plt.plot(weights, label="weights")
            plt.legend()
            plt.show()
            exit()


        # print(mean_peak_x_idx)
        # print(mean_peak_x)
        # Define optimisation bounds
        # bounds = ((highest_data_y * 0.95, mean_peak_x_idx - 0.9, 0.8, -0.3),
        #           (highest_data_y * 1.15, mean_peak_x_idx + 0.9, 50.0, 1.3))
        # bounds = ((highest_data_y * 0.98, mean_peak_x - x_inc * 2, 0.4 * x_inc,),
        #           (highest_data_y * 1.15, mean_peak_x + x_inc * 2, 100.0 * x_inc,))

        bounds = fitfn.bounds(mean_peak_x, highest_data_y, x_inc)

        if 0 and "OLD":
            offsets = np.arange(len(y_values)) - mean_peak_x_idx  # x-index vs peak estimate
            weights_a = np.clip(1.2 - np.abs(offsets) / 11, 0.1, 1.0) ** 2  # Weight small offsets higher
            norm_y_values = y_values / y_values.max()  # Normalise to 1.0
            weights_b = np.clip(norm_y_values - 0.4, 0.0001, 1.0)  # Weight higher points higher, ignore below 0.4
            weights = weights_a * weights_b  # Merge
            weights = weights / weights.max()  # Normalise
        # print(bounds)
        sigmas = 1. / weights
        # initial = (highest_data_y, mean_peak_x, 3.0 * x_inc,)
        initial = fitfn.initial(mean_peak_x, highest_data_y, x_inc)
        fitted_params, _ = optimize.curve_fit(fitfn, x_values, y_values,
                                              bounds=bounds, sigma=sigmas, ftol=1e-5, xtol=1e-5,
                                              p0=initial)
        #
        fit_peak_x = fitted_params[1]
        fit_peak_y = fitted_params[0]

        count = 0

        def prysmfit(defocuss, defocus_offset, defocus_step, aberr, a2=0, plot=False):
            # freqs = np.arange(0, 32, 1) / 64 * 250
            freqs = LOWAVG_NOMBINS / 64 * 250
            # print(defocus_offset, defocus_step, aberr)
            out = []
            for defocus in defocuss:
                pupil = prysm.NollZernike(Z4=(defocus - defocus_offset) * defocus_step * 0.575 - aberr / 2,
                                          dia=10, norm=True,
                                          z11=aberr,
                                          z22=a2,
                                          wavelength=0.575,
                                          opd_unit="um",
                                          samples=256)
                # prysm.PSF.from_pupil(pupil, efl=10*self.exif.aperture).plot2d()
                # plt.show()
                m = prysm.MTF.from_pupil(pupil, efl=self.exif.aperture * 10)
                out.append(np.mean(m.exact_tan(freqs)))
            if plot:
                # pupil.plot2d()
                # plt.show()
                # plt.plot(out, label="Prysm Model {:.3f}λ Z4 step size, {:.3f}λ Z11".format(defocus_step, aberr / 0.575))
                plt.plot(out, label="Prysm Model {:.3f}λ Z4 step size, {:.3f}λ Z11, {:.3f}λ Z22".format(defocus_step, aberr / 0.575, a2 / 0.575))
                plt.plot(y_values, label="MTF Mapper data")
                plt.xlabel("Focus position (arbitrary units)")
                plt.ylabel("MTF")
                plt.title("Prysm model vs chart ({:.32}-{:.2f}cy/px AUC) {}".format(LOWAVG_NOMBINS[0] / 64, LOWAVG_NOMBINS[-1] / 64, self.exif.summary))
                plt.ylim(0, 1)
                plt.legend()
                plt.show()
            global count
            count += 1
            # print(count)
            return np.array(out)

        if _step_estimation_posh and _return_step_data_only:
            fit_peak_y = 0
            #
            initial = [fit_peak_x, 0.3, (1.0 - fitted_params[0]) / 5,(1.0 - fitted_params[0]) / 5, ]#[:3]
            bounds = (min(x_values), 0.001, 0.0, 0), (max(x_values), 0.35, 0.45, 0.45)
            # bounds = bounds[0][:3], bounds[1][:3]
            sigmas = 1.0 / (y_values ** 1)
            prysm_params, _ = optimize.curve_fit(prysmfit, x_values, y_values,
                                                  bounds=bounds, sigma=sigmas, ftol=1e-3, xtol=1e-3,
                                                  p0=initial)
            # print("Prysm fit params", prysm_params)
            if plot:
                prysmfit(x_values, *prysm_params, plot=True)

        log.debug("Fn fit peak is {:.3f} at {:.2f}".format(fitted_params[0], fitted_params[1]))
        log.debug("Fn sigma: {:.3f}".format(fitted_params[2]))

        if _return_step_data_only:
            # ---
            # Estimate defocus step size
            # ---
            if _step_estimation_posh:
                est_defocus_rms_wfe_step = prysm_params[1]
                log.info("Using posh estimation model")
            else:
                est_defocus_rms_wfe_step = (4.387 / fitted_params[2] / (fit_peak_y + _step_est_offset)) / self.exif.aperture
            if "_fixed_defocus_step_wfe" in dir(self):
                est_defocus_rms_wfe_step = self._fixed_defocus_step_wfe
            est_defocus_pv_wfe_step = est_defocus_rms_wfe_step * 2 * 3 ** 0.5

            log.info("--- Focus step size estimates ---")
            log.info("    RMS Wavefront defocus error {:8.4f} λ".format(est_defocus_rms_wfe_step))

            longitude_defocus_step_um = est_defocus_pv_wfe_step * self.exif.aperture**2 * 8 * 0.55
            log.info("    Image side focus shift      {:8.3f} µm".format(longitude_defocus_step_um))

            na = 1 / (2.0 * self.exif.aperture)
            theta = np.arcsin(na)
            coc_step = np.tan(theta) * longitude_defocus_step_um * 2

            focal_length_m = self.exif.focal_length * 1e-3

            def get_opposide_dist(dist):
                return 1.0 / (1.0 / focal_length_m - 1.0 / dist)

            lens_angle_of_view = self.exif.angle_of_view
            # print(lens_angle_of_view)
            subject_distance = CHART_DIAGONAL * 0.5 / np.sin(lens_angle_of_view / 2)
            image_distance = get_opposide_dist(subject_distance)

            log.info("    Subject side focus shift    {:8.2f} mm".format((get_opposide_dist(image_distance-longitude_defocus_step_um *1e-6) - get_opposide_dist(image_distance)) * 1e3))
            log.info("    Blur circle  (CoC)          {:8.2f} µm".format(coc_step))

            log.info("Nominal subject distance {:8.2f} mm".format(subject_distance * 1e3))
            log.info("Nominal image distance   {:8.2f} mm".format(image_distance * 1e3))

            # def curvefn(xvals):
            #     image_distances = get_opposide_dist(xvals)
            #     step = longitude_defocus_step_um * 1e-6
            #     return fitfn(image_distances, fit_peak_y, image_distance, fitted_params[2] * step)
            # x_values = get_opposide_dist((x_values - fit_peak_x) * longitude_defocus_step_um * 1e-6 + image_distance)

            return est_defocus_rms_wfe_step, longitude_defocus_step_um, coc_step, image_distance, subject_distance, fit_peak_y

        # def curvefn(xvals):
        #     return fitfn(xvals, fit_peak_y, 0.0, fitted_params[2] * coc_step)
        def curvefn(xvals):
            return fitfn(xvals, *fitted_params)
        # x_values = (x_values - fit_peak_x) * coc_step

        # fit_peak_x = 0  # X-values have been normalised around zero

        fit_y = curvefn(x_values)
        errorweights = np.clip((y_values - y_values.max() * 0.8), 0.000001, 1.0)**1
        mean_abs_error = np.average(np.abs(fit_y - y_values), weights=errorweights)
        mean_abs_error_rel = mean_abs_error / highest_data_y

        log.debug("RMS fit error (normalised 1.0): {:.3f}".format(mean_abs_error_rel))
        # print(mean_abs_error_rel)
        if mean_abs_error_rel > 0.09:
            errorstr = "Very high fit error: {:.3f}".format(mean_abs_error_rel)
            log.warning(errorstr)
            print("{}, {}, {}, {}".format(x, y, freq, axis))
            if PLOT_ON_FIT_ERROR:
                plt.plot(x_values, y_values)
                plt.show()
            pos = FocusOb(fit_peak_x, fit_peak_y, interp_fn, curvefn)
            raise FitError(errorstr, fitpos=pos)
        elif mean_abs_error_rel > 0.05:
            errorstr = "High fit error: {:.3f}".format(mean_abs_error_rel)
            log.warning(errorstr)
            # print(x, y, freq, axis)
            # plt.plot(x_values, y_values)
            # plt.show()
            if strict:
                errorstr = "Strict mode, fit aborted".format(mean_abs_error_rel)
                log.warning(errorstr)
                pos = FocusOb(fit_peak_x, fit_peak_y, interp_fn, curvefn)
                raise FitError(errorstr, fitpos=pos)

        if plot or 0 and 0.6 < calc_image_height(x, y) < 0.7:
            # print(x,y,freq, axis, fit_peak_x)
            # Plot original data
            plt.plot(x_values, y_values, '.', marker='s', color='forestgreen', label="Original data points", zorder=11)
            plt.plot(x_values, y_values, '-', color='forestgreen', alpha=0.3, label="Original data line", zorder=-1)

            # Plot fit curve
            x_plot = np.linspace(x_values.min(), x_values.max(), 100)
            y_gaussplot = curvefn(x_plot)
            plt.plot(x_plot, y_gaussplot, color='red', label="Gaussian curve fit", zorder=14)
            # plt.plot(x_values, errorweights / errorweights.max() * y_values.max(), '--', color='gray', label="Sanity checking weighting")

            # Plot interpolation curve
            y_interpplot = interp_fn(x_plot)
            # plt.plot(x_plot, y_interpplot, color='seagreen', label="Interpolated quadratic spline fit", zorder=3)

            # Plot weights
            # x_label = "Field/image number (focus position)"
            plt.plot(x_values, weights * fit_peak_y, '--', color='royalblue', label="Curve fit weighting", zorder=1)
            plt.xlabel(self.focus_scale_label)
            plt.ylabel("Spacial frequency response")
            plt.title("SFR vs focus position")
            plt.legend()
            if show:
                plt.show()
        ob = FocusOb(fit_peak_x, fit_peak_y, interp_fn, curvefn)
        ob.x_loc = x
        ob.y_loc = y
        return ob

    def attempt_to_calibrate_focus(self, x=IMAGE_WIDTH/2, y=IMAGE_HEIGHT/2, freq=AUC, plot=False,
                                   unit=FOCUS_SCALE_COC, pixelsize=DEFAULT_PIXEL_SIZE, posh=True):
        tup = self.find_best_focus(x, y, LOWAVG if posh else LOWAVG, MERIDIONAL, plot=plot,
                                   _return_step_data_only=True, _step_estimation_posh=posh)
        if unit is not None:
            est_defocus_rms_wfe_step, longitude_defocus_step_um, coc_step, image_distance, subject_distance, _ = tup

            pos = self.find_best_focus(x, y, freq, MERIDIONAL)
            log.debug("Best focus index {}".format(pos.focuspos))
            old_x = np.arange(0, len(self.fields))
            if unit == FOCUS_SCALE_COC:
                step = coc_step
                self.focus_scale_label = FOCUS_SCALE_COC
            elif unit == FOCUS_SCALE_COC_PIXELS:
                step = coc_step / pixelsize * 1e-6
                self.focus_scale_label = FOCUS_SCALE_COC_PIXELS
            elif unit == FOCUS_SCALE_RMS_WFE:
                step = est_defocus_rms_wfe_step
                self.focus_scale_label = FOCUS_SCALE_RMS_WFE
            elif unit == FOCUS_SCALE_FOCUS_SHIFT:
                step = longitude_defocus_step_um
                self.focus_scale_label = FOCUS_SCALE_FOCUS_SHIFT
            else:
                raise ValueError("Unknown units")
            new_x = (old_x - pos.focuspos) * step
            self._focus_data = new_x

    @property
    def focus_data(self):
        if self._focus_data is None:
            return np.arange(len(self.fields))
        return self._focus_data

    def interpolate_value(self, x, y, focus, freq=AUC, axis=MEDIAL, posh=False):
        if int(focus) == 0:
            limit_low = 0
            limit_high = int(focus) + 3
        elif int(focus+1) >= len(self.fields):
            limit_low = int(focus) - 2
            limit_high = int(focus) + 2
        else:
            limit_low = int(focus) - 1
            limit_high = int(focus) + 2
        if posh:

            point_pos = self.find_best_focus(x, y, freq, axis=axis)
            return point_pos.curvefn(focus)

        else:
            point_pos = self.get_interpolation_fn_at_point(x, y, freq, axis=axis,
                                                           limit=(limit_low, limit_high))
            return point_pos.interpfn(focus)

    def plot_field_curvature_strip(self, freq, show=True):
        sag = []
        sagl = []
        sagh = []
        mer = []
        merl = []
        merh = []
        x_rng = range(100, IMAGE_WIDTH, 200)
        for n in x_rng:
            x = n
            y = IMAGE_HEIGHT - n * IMAGE_HEIGHT / IMAGE_WIDTH
            focuspos, sharpness, l, h = self.find_best_focus(x, y, freq, SAGITTAL)
            sag.append(focuspos)
            sagl.append(l)
            sagh.append(h)
            focuspos, sharpness, l, h = self.find_best_focus(x, y, freq, MERIDIONAL)
            mer.append(focuspos)
            merl.append(l)
            merh.append(h)

        plt.plot(x_rng, sag, color='green')
        plt.plot(x_rng, sagl, '--', color='green')
        plt.plot(x_rng, sagh, '--', color='green')
        plt.plot(x_rng, mer, color='blue')
        plt.plot(x_rng, merl, '--', color='blue')
        plt.plot(x_rng, merh, '--', color='blue')
        if show:
            plt.show()

    def remove_duplicated_fields(self, plot=False, train=[]):
        fields = self.fields[:]
        prev = fields[0].points
        new_fields = [fields[0]]
        for n, field in enumerate(fields[1:]):
            tuplist=[]
            dup = (n+1) in train
            for pointa, pointb in zip(prev, field.points):
                tup = pointa.is_match_to(pointb)
                tuplist.append([dup] + [n + 1] + list(tup))
            prev = field.points
            tuplist = np.array(tuplist)
            duplikely = np.percentile(tuplist[:,6], 80) < 0.15
            if not duplikely:
                new_fields.append(field)
            else:
                log.info("Field {} removed as duplicate".format(n+1))
        log.info("Removed {} out of {} field as duplicates".format(len(self.fields) - len(new_fields), len(self.fields)))
        self.fields = new_fields

    def plot_best_sfr_vs_freq_at_point(self, x, y, axis=MEDIAL, x_values=None, secondline_fn=None, show=True):
        if x_values is None:
            x_values = RAW_SFR_FREQUENCIES[:32]
        y = [self.find_best_focus(x, y, f, axis).sharp for f in x_values]
        plt.plot(x_values, y)
        plt.ylim(0,1)
        if secondline_fn:
            plt.plot(x_values, secondline_fn(x_values))
        if show:
            plt.show()

    def plot_sfr_vs_freq_at_point_for_each_field(self, x, y, axis=MEDIAL, waterfall=False):
        fig = plt.figure()
        if waterfall:
            ax = fig.add_subplot(111, projection='3d')
        else:
            ax = fig.add_subplot(111)
        freqs = np.concatenate((RAW_SFR_FREQUENCIES[:32:1], [AUC]))
        for nfreq, freq in enumerate(freqs):
            print("Running frequency {:.2f}...".format(freq))
            responses = []
            for fn, field in enumerate(self.fields):
                res = field.interpolate_value(x, y, freq, axis)
                if res > 0.01:
                    responses.append(res)
                else:
                    responses.append(0.01)

            if waterfall:
                if freq == AUC:
                    colour = 'black'
                else:
                    colour = 'black'
                    colour = plt.cm.brg(nfreq / len(freqs))
                plt.plot([nfreq / 65] * len(self.fields), np.arange(len(self.fields)),
                         np.log(responses) / (np.log(10) / 20.0),
                         label="Field {}".format(fn), color=colour, alpha=0.8)
            else:
                if freq == AUC:
                    colour = 'black'
                else:
                    colour = 'black'
                    colour = plt.cm.jet(nfreq / len(freqs))
                if freq == AUC:
                    label = "Mean / Area under curve"
                else:
                    label = "{:.2f} cy/px".format(freq)
                plt.plot(np.arange(len(self.fields)), np.log(responses) / (np.log(10) / 20.0),
                         label=label, color=colour, alpha=1.0 if freq==AUC else 0.9)
        if waterfall:
            ax.set_xlabel("Spacial Frequency (cy/px")
            ax.set_ylabel("Focus position")
            ax.set_zlabel("SFR (dB - log scale)")
            ax.set_zlim(-40, 0)
        else:
            ax.set_xlabel("Focus Position")
            ax.set_ylabel("SFR (dB (log scale))")
            ax.set_ylim(-40,0)
            ax.legend()

        ax.set_title("SFR vs Frequency for {}".format(self.exif.summary))
        # ax.legend()
        plt.show()

    def get_peak_sfr(self, x=None, y=None, freq=AUC, axis=BOTH_AXES, plot=False, show=False):
        """
        Get entire SFR at specified location at best focus determined by 'focus' passed

        :param x:
        :param y:
        :param freq:
        :param axis:
        :param plot:
        :param show:
        :return:
        """
        if axis == BOTH_AXES:
            best = -np.inf, "", 0, 0, 0
            for axis in [SAGITTAL, MERIDIONAL]:
                print("Testing axis {}".format(axis))

                if x is None or y is None:
                    ob = self.find_sharpest_location(freq, axis)
                    x = ob.x_loc
                    y = ob.y_loc
                else:
                    ob = self.find_best_focus(x, y, freq, axis)
                focuspos = ob.focuspos
                if ob.sharp > best[0]:
                    best = ob.sharp, axis, x, y, focuspos
            axis = best[1]
            focuspos = best[4]
            x = best[2]
            y = best[3]
            print("Found best point {:.3f} on {} axis at ({:.0f}, {:.0f}".format(best[0], axis, x, y))
        else:
            if x is None or y is None:
                ob = self.find_sharpest_location(freq, axis)
                x = ob.x_loc
                y = ob.y_loc
            else:
                ob = self.find_best_focus(x, y, freq, axis)
            focuspos = ob.focuspos

        # Build sfr at that point
        data_sfr = []
        for f in RAW_SFR_FREQUENCIES[:]:
            data_sfr.append(self.interpolate_value(x, y, focuspos, f, axis))

        data_sfr = np.array(data_sfr)
        print("Acutance {:.3f}".format(calc_acutance(data_sfr)))
        if plot:
            plt.plot(RAW_SFR_FREQUENCIES[:], data_sfr)
            plt.ylim(0.0, data_sfr.max())
            if show:
                plt.show()
        return SFRPoint(rawdata=data_sfr)

    def find_sharpest_raw_point(self):
        best = -np.inf, None
        for field in self.fields:
            for point in field.points:
                sharp = point.get_freq(AUC)
                if sharp > best[0]:
                    best = sharp, point
        return best[1]

    def find_sharpest_raw_points_avg_sfr(self, n=5, skip=5):
        best = -np.inf, None
        all = []
        for field in self.fields:
            for point in field.points:
                sharp = point.get_freq(AUC)
                all.append((sharp, point))
        all.sort(reverse=True, key=itemgetter(0))
        best = all[skip: skip + n]
        print(best)
        sum_ = sum([tup[1].raw_sfr_data for tup in best])
        return sum_ / n

    def find_sharpest_location(self, freq=AUC, axis=MEDIAL, detail=1.0):
        gridit, numparr, x_values, y_values = self.get_grids(detail=detail)
        heights = numparr.copy()
        focusposs = numparr.copy()
        # axes = numparr.copy()
        searchradius = 0.15
        lastsearchradius = 0.0
        while searchradius < 0.5:
            for nx, ny, x, y in gridit:
                imgheight = calc_image_height(x, y)
                if lastsearchradius < imgheight < searchradius:
                    focusob = self.find_best_focus(x, y, freq, axis)
                    numparr[ny, nx] = focusob.sharp
                    # axes[ny, nx] = focusob.axis
                    focusposs[ny, nx] = focusob.focuspos
                    heights[ny, nx] = imgheight
            maxcell = np.argmax(numparr)
            best_x_idx = maxcell % len(x_values)
            best_y_idx = int(maxcell / len(x_values))
            winning_height = heights[best_y_idx, best_x_idx]
            if winning_height < (searchradius * 0.7):
                break
            lastsearchradius = searchradius
            searchradius = searchradius / 0.6
            print("Upping search radius to {:.2f}".format(searchradius))

        best_x = x_values[best_x_idx]
        best_y = y_values[best_y_idx]
        bestpos = focusposs[best_y_idx, best_x_idx]
        print("Found best point {:.3f} at ({:.0f}, {:.0f}) (image height {:.2f})"
              "".format(numparr.max(), best_x, best_y, winning_height))
        print("   at focus position {:.2f}".format(bestpos))
        ob = FocusOb(focuspos=bestpos, sharp=numparr.max())
        ob.x_loc = best_x
        ob.y_loc = best_y
        return ob

    def estimate_wavefront_error(self, max_fnumber_error=0.33):
        f_range = RAW_SFR_FREQUENCIES[:30]
        # data_sfr = self.get_peak_sfr(freq=opt_freq, axis=BOTH_AXES).raw_sfr_data[:]
        data_sfr = self.get_peak_sfr(IMAGE_WIDTH/2, IMAGE_HEIGHT/2, axis=BOTH_AXES).sfr[:30]
        data_sfr2 = self.find_sharpest_raw_points_avg_sfr()[:30] * self.fields[0].points[0].calibration[:30]
        # data_sfr = -0.2 + data_sfr * 1.2
        data_mean = np.mean(data_sfr)

        plt.plot(f_range, data_sfr, label="Lens SFR Through interp")
        # plt.plot(f_range, data_sfr2, label="Lens SFR raw point")

        diff = diffraction_mtf(f_range, self.exif.aperture)

        # plt.plot(f_range, diff, label="Diffraction fn")

        print(data_mean)
        print(np.mean(diff))

        def prysmsfr(fin, z11, fstop, plot=False):
            # old_z11 = z11
            # z11 = np.abs(z11)
            pupil = prysm.NollZernike(z22=z11, dia=10, norm=True, wavelength=0.575)
            m = prysm.MTF.from_pupil(pupil, efl=10*fstop)
            modelmtf = m.exact_xy(fin / DEFAULT_PIXEL_SIZE * 1e-3)
            test_mean = np.mean(modelmtf)
            if plot:
                plt.plot(f_range, modelmtf, label="Model SFR WFE Z11 {:.3f}λ f/{:.2f}".format(z11, fstop))
            print(z11, test_mean)
            return modelmtf

        fchange = np.clip(2 ** (max_fnumber_error / 2.0), 1.0001, 10.0)
        params, _ = optimize.curve_fit(prysmsfr, f_range, data_sfr, bounds=([0, self.exif.aperture/fchange], [0.22, self.exif.aperture*fchange]), p0=[0.1, self.exif.aperture])
        wfr, fstop = params
        prysmsfr(f_range, wfr, fstop, True)
        prysmsfr(f_range, 0, fstop, True)
        stops_out = np.log(fstop / self.exif.aperture) / np.log(2)
        print("Est. F number inaccuracy vs exif {:.2f} stops".format(stops_out))
        print("Z11 {:.3f} Fstop {:.2f}".format(wfr, fstop))
        # exit()
        # bounds=bounds, sigma=sigmas, ftol=1e-6, xtol=1e-6,
        #                                       p0=initial)
        # wfr = optimize.bisect(prysmsfr, 0.0, 0.5, xtol=1e-3, rtol=1e-3)
        # prysmsfr(wfr, plot=True)
        print("Wavefront error {:.3f}".format(wfr))
        print("Wavefront error {:.3f} f/2.8 equivalent".format(wfr / 2.8 * self.exif.aperture))
        print("Strehl {:.3f}".format(data_sfr.mean()/diff.mean()))
        plt.legend()
        plt.ylim(0, 1)
        plt.xlim(0, 0.5)
        plt.xlabel("Spacial Frequency (cy/px)")
        plt.ylabel("'Calibrated' MTF")
        plt.title(self.exif.summary)
        plt.show()


    def build_calibration(self, fstop=None, opt_freq=AUC, plot=True, writetofile=False, use_centre=False):
        """
        Assume diffraction limited lens to build calibration data

        :param fstop: Taking f-stop
        :param plot: Plot if True
        :param writetofile: Write to calibration.csv file
        :return: Numpy array of correction data
        """

        if fstop is None:
            fstop = self.exif.aperture
        f_range = RAW_SFR_FREQUENCIES[:40]
        if not use_centre:
            data_sfr = self.get_peak_sfr(freq=opt_freq, axis=BOTH_AXES).raw_sfr_data[:40]
        else:
            data_sfr = self.get_peak_sfr(x=IMAGE_WIDTH/8*7, y=IMAGE_HEIGHT/8*7, freq=opt_freq, axis=BOTH_AXES).raw_sfr_data[:40]
        # data_sfr = self.find_sharpest_raw_points_avg_sfr()[:40]

        if self.use_calibration:
            if writetofile:
                # pass
                raise AttributeError("Focusset must be loaded without existing calibration")
            else:
                log.warning("Existing calibration loaded (will compare calibrations)")
        # Get best AUC focus postion


        # if not writetofile:
        #     data_sfr *= self.base_calibration[:40]

        diffraction_sfr = diffraction_mtf(f_range, fstop/1.00)  # Ideal sfr

        correction = np.clip(diffraction_sfr / data_sfr, 0, 50.0)

        print("Calibration correction:")
        print(correction)

        if writetofile:
            with open("calibration.csv", 'w') as csvfile:
                csvwriter = csv.writer(csvfile, delimiter=',', quotechar='|')
                csvwriter.writerow(list(f_range))
                csvwriter.writerow(list(correction))
                print("Calibration written!")

        if plot:
            plt.ylim(0, max(correction))
            plt.plot(f_range, data_sfr)
            plt.plot(f_range, diffraction_sfr, '--')
            plt.plot(f_range, correction)
            plt.title(self.lens_name)
            plt.show()
        return data_sfr, diffraction_sfr, correction

    def set_calibration_sharpen(self, amount, radius, stack=False):
        for field in self.fields:
            field.set_calibration_sharpen(amount, radius, stack)
        self.calibration = self.fields[0].calibration

    def get_grids(self, *args, **kwargs):
        return self.fields[0].get_grids(*args, **kwargs)

    def find_compromise_focus(self, freq=AUC, axis=MEDIAL, detail=1.0, plot_freq=None, weighting_fn=EVEN_WEIGHTED,
                              plot_type=PROJECTION3D, midfield_bias_comp=True, precision=0.1):
        """
        Finds optimial compromise flat-field focus postion

        :param freq: Frequency to use for optimisation
        :param axis: SAGITTAL, MERIDIONAL or MEDIAL
        :param detail: Change number of analysis points (default is 1.0)
        :param plot_freq: Frequency to use for plot of result if different to optimisation frequency
        :param weighting_fn: Pass a function which accepts an image height (0-1) and returns weight (0-1)
        :param plot_type: CONTOUR2D or PROJECTION3D
        :param midfield_bias_comp: Specified whether bias due to large number of mid-field points should be compensated
        :return:
        """

        gridit, numpyarray, x_values, y_values = self.get_grids(detail)
        n_fields = len(self.fields)
        inc = precision
        field_locs = np.arange(0, n_fields, inc)  # Analyse with 0.1 field accuracy

        # Made sfr data array
        sharps = np.repeat(numpyarray[:,:,np.newaxis], len(field_locs), axis=2)

        xm, ym = np.meshgrid(x_values, y_values)
        heights = ((xm - (IMAGE_WIDTH / 2))**2 + (ym - (IMAGE_HEIGHT / 2))**2)**0.5 / ((IMAGE_WIDTH / 2)**2+(IMAGE_HEIGHT / 2)**2)**0.5
        weights = np.ndarray((len(y_values), len(x_values)))

        # Iterate over all locations
        for n_x, n_y, x, y in gridit:
            # Get sharpness data at location
            try:
                interpfn = self.find_best_focus(x, y, freq, axis=axis).interpfn
            except FitError as e:
                # Keep calm and ignore crap fit errors
                interpfn = e.fitpos.interpfn
                # plt.plot(field_locs, interpfn(field_locs))
                # plt.show()

            # Gather data at all sub-points
            pos_sharps = interpfn(field_locs)
            sharps[n_y, n_x] = pos_sharps

            # Build weighting
            img_height = calc_image_height(x, y)
            weights[n_y, n_x] = np.clip(weighting_fn(img_height), 1e-6, 1.0)

        if midfield_bias_comp:
            # Build kernal density model to de-bias mid-field due to large number of points
            height_kde = stats.gaussian_kde(heights.flatten(), bw_method=0.8)
            height_density_weight_mods = height_kde(heights.flatten()).reshape(weights.shape)

            plot_kde_info = 0
            if plot_kde_info:
                px = np.linspace(0, 1, 30)
                plt.plot(px, height_kde(px))
                plt.show()
                fig = plt.figure()
                ax = fig.add_subplot(111, projection='3d')
                ax.scatter(xm.flatten(), ym.flatten(), (weights / height_density_weight_mods).flatten(), c='b', marker='.')
                plt.show()
            weights = weights / height_density_weight_mods

        weights = np.repeat(weights[:, :, np.newaxis], len(field_locs), axis=2)
        average = np.average(sharps, axis=(0, 1), weights=weights)

        # slice_ = average > (average.max() * 0.9)
        # poly = np.polyfit(field_locs[slice_], average[slice_], 2)
        # polyder = np.polyder(poly, 1)
        # peak_focus_pos = np.roots(polyder)[0]
        peak_focus_pos = np.argmax(average) * inc
        print("Found peak focus at position {:.2f}".format(peak_focus_pos))

        interpfn = interpolate.InterpolatedUnivariateSpline(field_locs, average)

        if not 0 < peak_focus_pos < (n_fields - 1):
            # _fit = np.polyval(poly, field_locs)
            plt.plot(field_locs, average)
            # plt.plot(field_locs, _fit)
            plt.show()

        if plot_freq and plot_freq != freq:
            # Plot frequency different to optimisation frequency

            sharpfield = numpyarray
            for n_x, n_y, x, y in gridit:
                # Get sharpness data at location

                interpfn = self.find_best_focus(x, y, plot_freq, axis=axis).interpfn
                sharpfield[n_y, n_x] = interpfn(peak_focus_pos)
        else:
            sharpfield = sharps[:, :, int(peak_focus_pos/inc + 0.5)]

        # Move on to plotting results
        if plot_type is not None:
            plot = FieldPlot()
            plot.zdata = sharpfield
            plot.xticks = x_values
            plot.yticks = y_values
            plot.yreverse = True
            print(9, self.calibration)
            plot.zmin = diffraction_mtf(freq, LOW_BENCHMARK_FSTOP, calibration=1.0 / self.base_calibration)
            plot.zmax = diffraction_mtf(freq, HIGH_BENCHBARK_FSTOP, calibration=1.0 / self.base_calibration)
            plot.zmin = diffraction_mtf(freq, LOW_BENCHMARK_FSTOP, calibration=None)
            plot.zmax = diffraction_mtf(freq, HIGH_BENCHBARK_FSTOP, calibration=None)
            # print(55, plot.zmin)
            # print(55, plot.zmax)
            plot.title = "Compromise focus flat-field " + self.exif.summary
            plot.xlabel = "x image location"
            plot.ylabel = "y image location"
            plot.plot(plot_type)
        return FocusOb(peak_focus_pos, average[int(peak_focus_pos * 10)], interpfn)

    def get_mtf_vs_image_height(self, analysis_pos=None, freq=AUC, detail=0.5, axis=MEDIAL, posh=False):
        gridit, numpyarr, x_vals, y_vals = self.get_grids(detail=detail)
        heights = numpyarr.copy()
        arrs = []
        # if axis == MEDIAL:
        #     axis = [SAGITTAL, MERIDIONAL]
        #     axis = [SAGITTAL, MERIDIONAL]
        # else:
        #     axis = [axis]
        fns = []
        arr = numpyarr.copy()
        arrs.append(arr)
        for nx, ny, x, y in gridit:
            heights[ny, nx] = calc_image_height(x, y)
            if analysis_pos is None:
                try:
                    ob = self.find_best_focus(x, y, freq, axis)
                    arr[ny, nx] = ob.sharp
                    # if ob.sharp > 0.95:
                    #     print(ob.sharp, x, y, heights[ny, nx], loopaxis)
                except FitError as e:
                    arr[ny, nx] = np.nan
            else:
                arr[ny, nx] = self.interpolate_value(x, y, analysis_pos.focuspos, freq, axis, posh=posh)

        def fn(hei, width=0.2):
            flatheights = heights.flatten()
            sharps = arr.flatten()
            weights = 1.0001 - np.clip(np.abs(hei - flatheights) / width, 0.0, 1.0)
            return np.average(sharps, weights=weights)
        return fn
        fns.append(fn)
        if len(axis) == 1:
            return fns[0]
        else:
            def combine(hei, width=0.25):
                return (fns[0](hei, width) + fns[1](hei, width)) * 0.5
            return combine



    def plot_mtf_vs_image_height(self, analysis_pos=None, freqs=(15 / 250, 45/250, 75/250), detail=0.5, axis=MEDIAL, show=True,
                                 show_diffraction=None, posh=False):
        gridit, numpyarr, x_vals, y_vals = self.get_grids(detail=detail)
        heights = numpyarr.copy()
        arrs = []
        legends = []
        fig = plt.figure()
        if axis == MEDIAL:
            axis = [SAGITTAL, MERIDIONAL]
            axis = [SAGITTAL, MERIDIONAL]
        else:
            axis = [axis]
        for nfreq, freq in enumerate(freqs):
            for loopaxis in axis:
                arr = numpyarr.copy()
                arrs.append(arr)
                for nx, ny, x, y in gridit:
                    heights[ny, nx] = calc_image_height(x, y)
                    if analysis_pos is None:
                        try:
                            ob = self.find_best_focus(x, y, freq, loopaxis)
                            arr[ny, nx] = ob.sharp
                            # if ob.sharp > 0.95:
                            #     print(ob.sharp, x, y, heights[ny, nx], loopaxis)
                        except FitError as e:
                            arr[ny, nx] = np.nan
                    else:
                        arr[ny, nx] = self.interpolate_value(x, y, analysis_pos.focuspos, freq, loopaxis, posh=posh)

                if loopaxis == SAGITTAL:
                    lineformat = '--'
                else:
                    lineformat = '-'
                plot = Scatter2D()
                plot.xdata = heights.flatten()
                plot.ydata = arr.flatten()
                plot.ymin = 0
                plot.ymax = 1.0
                plot.xmin = 0.0
                plot.xmax = 1.0
                plot.xlabel = "Normalised Image Height"
                plot.ylabel = "Modulation Transfer Function"
                plot.title = self.exif.summary
                if show_diffraction:
                    if show_diffraction is True:
                        show_diffraction = self.exif.aperture
                        print(show_diffraction)
                    plot.hlines = diffraction_mtf(np.array(freqs), show_diffraction)
                    plot.hlinelabels = "f{} diffraction".format(show_diffraction)
                plot.smoothplot(lineformat=lineformat, show=False, color=COLOURS[[0, 3, 4][nfreq]],
                                label="{:.2f} lp/mm {}".format(freq * 250, loopaxis[0]),
                                marker="^" if loopaxis is SAGITTAL else "s",points_limit=4.0)
                # legends.append("{:.2f} cy/px {}".format(freq, loopaxis))
                # legends.append("{:.2f} cy/px {}".format(freq, loopaxis))
                # legends.append(None)
                # legends.append()
        plt.legend()
        if show:
            plt.show()

    def guess_focus_shift_field(self, detail=1.0, axis=MEDIAL):
        gridit, numarr, x_values, y_values = self.get_grids(detail=detail)
        arrs = []
        for freq in (0.02, 0.3):
            arr = numarr.copy()
            arrs.append(arr)
            for nx, ny, x, y in gridit:
                try:
                    arr[ny, nx] = self.find_best_focus(x, y, freq, axis=axis).focuspos
                except FitError as e:
                    for a in arrs:
                        a[ny, nx] = 0.0
        dif = arrs[1] - arrs[0]
        plot = FieldPlot()
        plot.xticks = x_values
        plot.yticks = y_values
        plot.zdata = dif
        plot.title = "Guessed focus shift"
        plot.zlabel = "Relative focus shift"
        plot.projection3d()

    def plot_best_focus_vs_frequency(self, x, y, axis=MEDIAL):
        freqs = np.logspace(-2, -0.3, 20)
        bests = []
        for freq in freqs:
            try:
                bestpos = self.find_best_focus(x, y, freq, axis=axis).focuspos
            except FitError as e:
                bestpos = float("NaN")
            bests.append(bestpos)
        plot = Scatter2D()
        plot.xdata = freqs
        plot.ydata = bests
        plot.xlog = True
        plot.smoothplot(plot_used_original_data=1)

    def skip_fields_and_check_accuracy(self):
        sharpestpoint = self.find_sharpest_raw_point()
        x = sharpestpoint.x
        y = sharpestpoint.y
        fields = self.fields
        fieldnumbers = list(range(len(fields)))
        skips = [1, 7]
        sharps = []
        sharps = []
        numpoints = []
        count = 1
        print("Inc  Start  Points     SFR   SFR+-  BestFocus  Bestfocus+-")
        for skip in skips:
            sharplst = []
            focusposlst = []
            counts = []
            for start in np.arange(skip):
                usefields = fields[start::skip]
                # Temporarily replace self.fields #naughty
                self.fields = usefields
                focusob = self.find_best_focus(x, y, axis=MERIDIONAL, plot=1)
                sharplst.append(focusob.sharp)
                focusposlst.append(focusob.focuspos)
                counts.append(count)
                count += 1
                text = ""
                if skip == 1 and start == 0:
                    baseline = focusob.sharp, focusob.focuspos
                    text = "** BASELINE ***"
                delta = focusob.sharp - baseline[0]
                percent = delta / baseline[0] * 100
                bestfocus = (focusob.focuspos * skip) + start
                bestfocusdelta = bestfocus - baseline[1]

                print("{:3.0f}  {:5.0f}  {:6.0f}  {:6.3f} {:7.3f} {:10.3f} {:10.3f} {}".format(skip,
                                                                                          start,
                                                                                          len(self.fields),
                                                                                          focusob.sharp,
                                                                                          delta,
                                                                                          bestfocus,
                                                                                          bestfocusdelta,
                                                                                               text))
            # plt.plot(counts, sharplst, '.', color=COLOURS[skip])
            # plt.plot([len(usefields)] * skip, sharplst, '.', color=COLOURS[skip])
            numpoints.append(len(usefields))
        self.fields = fields
        print(count)
        plt.legend(numpoints)
        # plt.xlabel("Testrun number")
        plt.xlabel("Number of images in sequence")
        plt.ylabel("Peak Spacial frequency response")
        plt.title("Peak detection vs number of images in sequence")
        # plt.plot([0, count], [baseline[0], baseline[0]], '--', color='gray')
        plt.plot([3, len(fields)], [baseline[0], baseline[0]], '--', color='gray')
        plt.ylim(baseline[0]-0.1, baseline[0]+0.1)
        plt.show()


"photos@scs.co.uk"
"S 0065-3858491"