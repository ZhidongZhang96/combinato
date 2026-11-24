"""Integration checks for standalone group density exports."""

import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pytest
import tables


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def recording(tmp_path):
    """Create two channels with deliberately nonidentity spike indices."""
    waveforms = np.array([
        [0, -2, 12, 5, 1, 0],
        [0, -1, 80, 20, 2, 0],
        [0, -3, 20, 8, 2, 0],
        [0, -4, 24, 9, 3, 0],
    ], dtype=float)
    for channel in ('CSC1', 'CSC2'):
        folder = tmp_path / channel
        folder.mkdir()
        with tables.open_file(folder / ('data_' + channel + '.h5'), 'w') as data:
            for sign, factor in (('pos', 1), ('neg', -1)):
                node = data.create_group('/', sign)
                data.create_array(node, 'spikes', factor * waveforms)
                data.create_array(node, 'times', np.arange(4) * 1000.)
        for sign in ('pos', 'neg'):
            sorting = folder / ('sort_' + sign + '_test')
            sorting.mkdir()
            with tables.open_file(sorting / 'sort_cat.h5', 'w') as data:
                data.root._v_attrs.sign = sign.encode() if channel == 'CSC2' else sign
                data.create_array('/', 'index', np.array([3, 0, 2, 1]))
                data.create_array('/', 'classes', np.array([7, 42, 7, 99]))
                data.create_array('/', 'groups', np.array([[7, 1], [42, 2], [99, 0]]))
                data.create_array('/', 'types', np.array([[0, 0], [1, 2], [2, 1]]))
    return tmp_path


def run_command(recording, *arguments):
    env = dict(os.environ, PYTHONPATH=str(ROOT), MPLBACKEND='Agg')
    return subprocess.run(
        [sys.executable, str(ROOT / 'css-plot-units'), '--root', str(recording),
         *arguments], cwd=recording, env=env, capture_output=True, text=True)


def test_batch_exports_each_unit_across_channels_and_polarities(recording):
    result = run_command(recording)
    assert result.returncode == 0, result.stderr
    images = list((recording / 'overview' / 'units').glob('*.svg'))
    assert len(images) == 8
    assert {p.name for p in images} == {
        '{}_{}_sort_{}_test_{:03d}.svg'.format(channel, sign, sign, gid)
        for channel in ('CSC1', 'CSC2')
        for sign in ('pos', 'neg') for gid in (1, 2)
    }
    for path in images:
        svg = ET.parse(path).getroot()
        assert svg.tag == '{http://www.w3.org/2000/svg}svg'
        assert svg.get('viewBox') == '0 0 144 144'


def test_filters_export_only_requested_group(recording):
    result = run_command(recording, '--channel', 'CSC2', '--polarity', 'neg',
                         '--label', 'sort_neg_test', '--group-id', '2',
                         '--output', str(recording / 'chosen'))
    assert result.returncode == 0, result.stderr
    assert [p.name for p in (recording / 'chosen').glob('*.svg')] == [
        'CSC2_neg_sort_neg_test_002.svg']


def test_group_uses_sorting_indices_and_can_select_unassigned(recording):
    from combinato.plot.plot_units import iter_unit_spikes

    fname = recording / 'CSC1' / 'data_CSC1.h5'
    sorting = fname.parent / 'sort_pos_test' / 'sort_cat.h5'
    groups = list(iter_unit_spikes(fname, sorting))
    assert [gid for gid, spikes in groups] == [1, 2]
    np.testing.assert_array_equal(groups[0][1][:, 2], [20, 24])
    np.testing.assert_array_equal(groups[1][1][:, 2], [12])
    unassigned = list(iter_unit_spikes(fname, sorting, group_ids=[0]))
    np.testing.assert_array_equal(unassigned[0][1][:, 2], [80])


def test_density_has_one_panel_three_waveform_lines_and_no_text(tmp_path):
    from combinato.plot.plot_units import plot_unit

    spikes = np.array([[0, -2, 10, 3, 0], [0, -4, 14, 5, 0]])
    fig = plot_unit(spikes)
    try:
        assert len(fig.axes) == 1
        ax = fig.axes[0]
        assert len(ax.images) == 1
        assert len(ax.lines) == 3
        np.testing.assert_array_equal(ax.lines[0].get_ydata(), [0, -3, 12, 4, 0])
        assert not ax.texts
        assert not ax.axison
    finally:
        plt.close(fig)


@pytest.mark.parametrize('spikes', [np.full((2, 6), -5.), np.zeros((1, 6))])
def test_density_handles_negative_only_and_constant_waveforms(spikes):
    from combinato.plot.plot_units import plot_unit

    fig = plot_unit(spikes)
    try:
        assert np.isfinite(fig.axes[0].images[0].get_array()).all()
    finally:
        plt.close(fig)


def test_unmatched_selection_reports_failure(recording):
    result = run_command(recording, '--channel', 'CSC404')
    assert result.returncode != 0
    assert 'No unit plots' in result.stderr


def test_explicit_files_and_negative_alias(recording):
    result = run_command(recording, '--files',
                         str(recording / 'CSC1' / 'data_CSC1.h5'), '--neg')
    assert result.returncode == 0, result.stderr
    assert {p.name for p in (recording / 'overview' / 'units').glob('*.svg')} == {
        'CSC1_neg_sort_neg_test_001.svg',
        'CSC1_neg_sort_neg_test_002.svg',
    }


def test_source_files_are_unchanged_by_export(recording):
    import hashlib

    files = sorted(recording.glob('CSC*/*.h5')) + sorted(recording.glob('CSC*/*/*.h5'))
    before = [hashlib.sha256(path.read_bytes()).digest() for path in files]
    result = run_command(recording, '--channel', 'CSC1', '--group-id', '0', '1')
    assert result.returncode == 0, result.stderr
    assert [hashlib.sha256(path.read_bytes()).digest() for path in files] == before
    assert len(list((recording / 'overview' / 'units').glob('*.svg'))) == 4
