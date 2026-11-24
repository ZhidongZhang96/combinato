"""Export one waveform density image per group, without cumulative curves."""

from argparse import ArgumentParser
import logging
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as mpl
import numpy as np
import tables

from .. import SIGNS, TYPE_MU, TYPE_SU
from .spike_heatmap import spike_heatmap


logger = logging.getLogger(__name__)


def iter_unit_spikes(datafile, sortingfile, group_ids=None):
    """Read grouped waveforms using the sorting file's extraction indices.

    Both HDF5 files are opened read-only. Only the current group's waveforms
    are loaded, and all its constituent classes are pooled.

    Args:
        datafile (str or Path): Extracted ``data_CHANNEL.h5`` file.
        sortingfile (str or Path): Grouped ``sort_cat.h5`` file.
        group_ids (sequence of int, optional): Groups to include. By default,
            include single- and multi-unit groups. Explicit IDs may also select
            unassigned or artifact groups.

    Yields:
        tuple: Integer group ID and waveform array of shape
        (n_spikes, n_samples), in the extraction file's amplitude units.

    Raises:
        ValueError: The sorting file has an unknown polarity.
    """
    with tables.open_file(str(sortingfile), 'r') as sorting, \
            tables.open_file(str(datafile), 'r') as data:
        sign = sorting.root._v_attrs.sign
        if isinstance(sign, bytes):
            sign = sign.decode('utf-8')
        if sign not in SIGNS:
            raise ValueError('Unknown sorting polarity: {!r}'.format(sign))
        index = sorting.root.index[:]
        classes = sorting.root.classes[:]
        groups = sorting.root.groups[:]
        types = sorting.root.types[:]
        if group_ids is None:
            gids = types[np.isin(types[:, 1], [TYPE_SU, TYPE_MU]), 0]
        else:
            gids = np.intersect1d(group_ids, groups[:, 1])
        waveforms = data.get_node('/' + sign, 'spikes')
        for gid in sorted(set(gids)):
            clids = groups[groups[:, 1] == gid, 0]
            rows = np.sort(index[np.isin(classes, clids)])
            if rows.size:
                yield int(gid), waveforms[rows.tolist(), :]


def plot_unit(spikes):
    """Create a density panel matching the sorted overview's waveform panel.

    The black line is the mean; dark gray lines are mean plus/minus one
    population standard deviation. The blue density includes every spike.
    Group identity belongs in the exported filename, keeping the panel clean.

    Args:
        spikes (numpy.ndarray): Shape (n_spikes, n_samples), in the extraction
            file's amplitude units. At least one spike and two samples required.

    Returns:
        matplotlib.figure.Figure: A 2-by-2-inch figure; the caller closes it.

    Raises:
        ValueError: Waveforms are empty or have an invalid shape.
    """
    spikes = np.asarray(spikes)
    if spikes.ndim != 2 or spikes.shape[0] == 0 or spikes.shape[1] < 2:
        raise ValueError('Expected at least one waveform with two samples')
    fig = mpl.figure(figsize=(2, 2))
    ax = fig.add_axes((0, 0, 1, 1))
    spike_heatmap(ax, spikes)
    ax.axis('off')
    return fig


def parse_args(argv=None):
    """Export selected units or batch all channels and both polarities.

    Args:
        argv (sequence of str, optional): Arguments; defaults to the command line.

    Returns:
        list of Path: Saved svg files.
    """
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'),
                        help='Recording directory containing channel folders (default: .)')
    parser.add_argument('--files', '--datafiles', type=Path, nargs='+',
                        help='Explicit extracted data files instead of scanning --root')
    parser.add_argument('--channel', '--channels', nargs='+', dest='channels',
                        help='Channel names, e.g. CSC129 CSC130 (default: all)')
    polarity = parser.add_mutually_exclusive_group()
    polarity.add_argument('--polarity', choices=('pos', 'neg', 'both'), default='both',
                          help='Polarity to export (default: both)')
    polarity.add_argument('--neg', action='store_const', const='neg', dest='polarity',
                          help='Alias for --polarity neg')
    parser.add_argument('--label', help='Sorting folder name (default: all grouped sortings)')
    parser.add_argument('--group-id', '--group-ids', type=int, nargs='+', dest='group_ids',
                        help='Group IDs (default: all single- and multi-unit groups)')
    parser.add_argument('--output', type=Path,
                        help='Output directory (default: ROOT/overview/units)')
    parser.add_argument('--dpi', type=int, default=300, help='svg resolution (default: 300)')
    args = parser.parse_args(argv)
    if args.dpi <= 0:
        parser.error('--dpi must be positive')

    output = args.output if args.output is not None else args.root / 'overview' / 'units'
    files = args.files if args.files is not None else sorted(set(
        args.root.glob('*/data_*.h5')) | set(args.root.glob('data_*.h5')))
    saved = []
    for datafile in files:
        channel = datafile.stem.removeprefix('data_')
        if args.channels is not None and channel not in args.channels:
            continue
        sortings = ([datafile.parent / args.label / 'sort_cat.h5'] if args.label
                    else sorted(datafile.parent.glob('*/sort_cat.h5')))
        for sortingfile in sortings:
            if not sortingfile.is_file():
                continue
            with tables.open_file(str(sortingfile), 'r') as sorting:
                sign = sorting.root._v_attrs.sign
                if isinstance(sign, bytes):
                    sign = sign.decode('utf-8')
            if args.polarity != 'both' and sign != args.polarity:
                continue
            for gid, spikes in iter_unit_spikes(datafile, sortingfile, args.group_ids):
                output.mkdir(parents=True, exist_ok=True)
                filename = '{}_{}_{}_{:03d}.svg'.format(
                    channel, sign, sortingfile.parent.name, gid)
                outname = output / filename
                fig = plot_unit(spikes)
                try:
                    fig.savefig(outname, dpi=args.dpi, facecolor='white',
                                bbox_inches=None, pad_inches=0)
                finally:
                    mpl.close(fig)
                saved.append(outname)
                logger.info('Saved %s (%d spikes)', outname, len(spikes))
    if not saved:
        parser.exit(1, 'No unit plots: no nonempty groups matched the selection.\n')
    print('Saved {} unit plot(s) to {}'.format(len(saved), output.resolve()))
    return saved
