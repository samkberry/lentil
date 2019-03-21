import numpy as np

DEFAULT_PIXEL_SIZE = 4e-6
SAGITTAL = "SAGITTAL"
MERIDIONAL = "MERIDIONAL"
MEDIAL = "BOTH AXES"
SFR_HEADER = [
    'blockid',
    'edgex',
    'edgey',
    'edgeangle',
    'radialangle'
]

MTF50 = -1
AUC = -2


def diffraction_mtf(freq, fstop=8):
    if type(freq) is int and freq == AUC:
        return diffraction_mtf(np.linspace(0, 0.5-1.0/32, 32), fstop).mean()
    mulfreq = np.clip(freq / 8.0 * fstop, 0, 1)
    return 2.0 / np.pi * (np.arccos(mulfreq) - mulfreq * (1 - mulfreq ** 2) ** 0.5)


def pixel_aperture_mtf(freq):
    freq = np.clip(freq, 0.0001, 1.0)
    return np.sin(np.pi*freq) / np.pi / freq


RAW_SFR_FREQUENCIES = [x / 64 for x in range(64)]  # List of sfr frequencies in cycles/pixel

GOOD = [1.        , 0.98582051, 0.95216779, 0.91605742, 0.88585631, 0.86172936,
     0.84093781, 0.82116408, 0.80170952, 0.78201686, 0.76154796, 0.73985244,
     0.7166293, 0.69158089, 0.66423885, 0.63510484, 0.60407738, 0.57122645,
     0.53737249, 0.50266147, 0.46764089, 0.43269842, 0.39822897, 0.36466347,
     0.33236667, 0.30161039, 0.27266122, 0.24569197, 0.2208242, 0.19810618,
     0.17752172, 0.15900566, 0.14245044, 0.1277121, 0.11462787, 0.10302666,
     0.09274069, 0.08361389, 0.07550579, 0.06829461, 0.06187432, 0.05615253,
     0.05104666, 0.04648352, 0.04239983, 0.03874731, 0.03549705, 0.03264138,
     0.03019484, 0.0281874, 0.0266599, 0.02565582, 0.02520846, 0.02533362,
     0.02601429, 0.02719823, 0.02879615, 0.03068963, 0.03274225, 0.03481336,
     0.0367723, 0.03850572, 0.03992789, 0.04098472]