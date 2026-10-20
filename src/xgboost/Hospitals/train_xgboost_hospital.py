"""Pool hospital panels to forecast overall BHI measures one calendar quarter ahead.

Run from the repository root with .venv-wsl/bin/python followed by this path.
Inputs are observations available at the END of the origin quarter. Static 2021
SEIFA values are retrospective covariates, not historical as-of observations.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = Path(__file__).with_name('xgboost_hospital.toml')


def read_panels(directory):
    rows, columns, keys = [], set(), set()
    paths = sorted(Path(directory).glob('*.csv'))
    if not paths:
        raise ValueError(f'No panel CSVs in {directory}')
    for path in paths:
        with path.open(newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            if len(fields) != len(set(fields)):
                raise ValueError(f'Duplicate columns in {path}')
            columns.update(fields)
            panel = list(reader)
        if not panel:
            raise ValueError(f'Empty panel: {path}')
        for row in panel:
            year, quarter = int(row['year']), int(row['quarter'])
            if year < 1 or quarter not in range(1, 5):
                raise ValueError('Invalid year/quarter')
            key = row['hospital_id'], year * 4 + quarter - 1
            if key in keys:
                raise ValueError(f'Duplicate hospital-quarter: {key}')
            keys.add(key)
            row['_period'] = key[1]
            rows.append(row)
    return sorted(rows, key=lambda r: (r['_period'], r['hospital_id'])), columns


def numeric(value):
    if value in ('', None):
        return float('nan')
    if value in ('True', 'False'):
        return float(value == 'True')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f'Nonfinite panel value: {value}')
    return result


def prepare(rows, columns, targets):
    if not targets or len(set(targets)) != len(targets) or set(targets) - columns:
        raise ValueError('Targets must be unique existing panel columns')
    features = sorted(columns - {'hospital_id', 'hospital_name', 'year', 'quarter'})
    ids = sorted({r['hospital_id'] for r in rows})
    names = ['origin_period', 'forecast_quarter', *['hospital_' + i for i in ids], *features]
    # Station-specific columns make the union of hospital schemas very sparse.
    # Omit missing values, but retain explicit zeros: zero is an observation.
    data, indices, indptr = [], [], [0]
    for r in rows:
        values = [r['_period'], (r['_period'] + 1) % 4 + 1,
                  *[float(r['hospital_id'] == i) for i in ids],
                  *[numeric(r.get(c)) for c in features]]
        for column, value in enumerate(values):
            if math.isfinite(value):
                data.append(value)
                indices.append(column)
        indptr.append(len(data))
    x = csr_matrix((np.asarray(data, dtype=np.float32),
                    np.asarray(indices, dtype=np.int32),
                    np.asarray(indptr, dtype=np.int32)), shape=(len(rows), len(names)))
    lookup = {(r['hospital_id'], r['_period']): r for r in rows}
    y = np.asarray([[numeric(lookup.get((r['hospital_id'], r['_period'] + 1), {}).get(t))
                     for t in targets] for r in rows], dtype=np.float32)
    return x, y, names


def rolling_splits(periods, folds, validation_quarters, min_train_quarters):
    latest = int(max(periods))
    splits = []
    for fold in reversed(range(folds)):
        end = latest - fold * validation_quarters
        start = end - validation_quarters + 1
        train, valid = periods < start, (periods >= start) & (periods <= end)
        if len(set(periods[train])) < min_train_quarters or not valid.any():
            raise ValueError('Insufficient history for chronological validation')
        splits.append((train, valid))
    return splits



def write_hyperparameters(report, output):
    """Export the effective settings used to fit each saved target model."""
    parameters = {
        'xgboost_version': report['xgboost_version'],
        'validation': report['config']['validation'],
        'targets': {target: {**report['config']['model'], **result['selected']}
                    for target, result in report['targets'].items()},
    }
    (Path(output) / 'training_hyperparameters.json').write_text(
        json.dumps(parameters, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def train(config_path=DEFAULT_CONFIG, panels=None, output=None, tune=False, model_dir=None):
    import xgboost as xgb
    with Path(config_path).open('rb') as stream:
        config = tomllib.load(stream)
    panels = Path(panels or ROOT / config['data']['panels'])
    output = Path(output or ROOT / config['data']['output'])
    model_dir = Path(model_dir or ROOT / config['data'].get('models', 'models/xgboost/Hospitals'))
    targets = config['targets']['columns']
    rows, columns = read_panels(panels)
    x, y, features = prepare(rows, columns, targets)
    periods = np.asarray([r['_period'] + 1 for r in rows])
    output.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    report = {'xgboost_version': xgb.__version__, 'features': features,
              'config': config, 'targets': {}}
    forecasts = {}
    for j, target in enumerate(targets):
        labelled = np.isfinite(y[:, j])
        tx, ty, tp = x[labelled], y[labelled, j], periods[labelled]
        splits = rolling_splits(tp, **config['validation'])
        candidates = config['search']['candidates'] if tune else [config.get('selected', {}).get(target, config['hyperparameters'])]
        scores = []
        for candidate in candidates:
            params = {**config['model'], **candidate}
            rounds = params.pop('num_boost_round')
            errors, baseline = [], []
            for tr, va in splits:
                model = xgb.train(params, xgb.DMatrix(tx[tr], label=ty[tr]), num_boost_round=rounds)
                prediction = model.predict(xgb.DMatrix(tx[va]))
                errors.extend((prediction - ty[va]).astype(float).tolist())
                # Persistence: the origin quarter's value of this same measure.
                # Sparse missing values materialize as zero; recover the true
                # origin values for a persistence comparison on observed pairs.
                origin = np.asarray([numeric(r.get(target)) for r, keep in zip(rows, labelled)
                                     if keep], dtype=np.float32)[va]
                baseline.extend((origin - ty[va]).astype(float).tolist())
            scores.append({'parameters': candidate, 'mae': float(np.mean(np.abs(errors))),
                           'persistence_mae': float(np.nanmean(np.abs(baseline)))})
        best = min(scores, key=lambda s: s['mae'])
        params = {**config['model'], **best['parameters']}
        rounds = params.pop('num_boost_round')
        model = xgb.train(params, xgb.DMatrix(tx, label=ty), num_boost_round=rounds)
        model.save_model(model_dir / f'{target}.ubj')
        report['targets'][target] = {'training_rows': int(labelled.sum()),
                                     'first_label_period': int(tp.min()),
                                     'last_label_period': int(tp.max()), 'candidates': scores,
                                     'selected': best['parameters'], 'validation_mae': best['mae']}
        # Use one origin per hospital, even when some measures stopped being reported.
        # Exclude trailing weather-only rows using any configured BHI outcome.
        for hospital in sorted({r['hospital_id'] for r in rows}):
            indices = [i for i, r in enumerate(rows) if r['hospital_id'] == hospital
                       and any(math.isfinite(numeric(r.get(t))) for t in targets)]
            if not indices:
                raise ValueError(f'No observations for {hospital}/{target}')
            i = indices[-1]
            p = rows[i]['_period'] + 1
            key = hospital, p
            forecasts.setdefault(key, {'hospital_id': hospital, 'year': p // 4, 'quarter': p % 4 + 1})[target] = float(model.predict(xgb.DMatrix(x[i:i+1]))[0])
        print(f'{target}: {labelled.sum()} training pairs, validation MAE {best["mae"]:.3f}', flush=True)
    (output / 'training_report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    write_hyperparameters(report, output)
    if tune:
        (output / 'tuning_report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    with (output / 'next_quarter_predictions.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['hospital_id', 'year', 'quarter', *targets])
        writer.writeheader()
        writer.writerows(forecasts[k] for k in sorted(forecasts))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--panels', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--model-dir', type=Path, help='Directory for saved XGBoost models')
    parser.add_argument('--tune', action='store_true', help='Compare TOML search candidates chronologically')
    args = parser.parse_args()
    train(args.config, args.panels, args.output_dir, args.tune, args.model_dir)


if __name__ == '__main__':
    main()
