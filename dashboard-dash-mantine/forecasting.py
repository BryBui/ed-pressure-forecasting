"""Join Gold next-quarter predictions to the existing geographic trends panels."""
import json
from pathlib import Path

import pandas as pd

from trends import ROOT, load_trends, resolve_data_root


def forecast_measures(prediction, configuration):
    """Offer only trained targets with actual predictions, never model input features."""
    targets = configuration['targets']
    if not isinstance(targets, dict) or 'pressure_index' not in targets or 'pressure_index' not in prediction:
        raise ValueError('Forecast configuration and predictions must include pressure_index.')
    return [name for name in targets if name in prediction]


def load_forecasts(root=ROOT):
    root = resolve_data_root(root)
    historical, boundaries, districts = load_trends(root)
    frames = {}
    for level, folder in [('lhd', 'LHDs'), ('hospital', 'Hospitals')]:
        prediction = pd.read_csv(root / 'gold' / 'xgboost' / folder / 'next_quarter_predictions.csv')
        configuration = json.loads((root / 'gold' / 'xgboost' / folder / 'training_hyperparameters.json').read_text())
        measures = forecast_measures(prediction, configuration)
        identifier = f'{level}_id'
        if prediction.empty or prediction[identifier].isna().any() or prediction[identifier].duplicated().any():
            raise ValueError(f'Forecast {level} identifiers must be present and unique.')
        if not prediction.quarter.isin([1, 2, 3, 4]).all():
            raise ValueError('Invalid forecast quarter.')
        prediction['period'] = pd.PeriodIndex(prediction.year.astype(str) + 'Q' + prediction.quarter.astype(str), freq='Q')
        history = historical[level]
        if prediction.period.nunique() != 1 or prediction.period.iloc[0] != history.period.max() + 1:
            raise ValueError('Forecast must cover the quarter immediately after history.')
        if not prediction.pressure_index.notna().all() or not prediction.pressure_index.between(0, 1).all():
            raise ValueError('Forecast pressure index must be between zero and one.')
        reference = pd.read_csv(root / 'reference' / f'nsw_{"lhds" if level == "lhd" else "hospitals"}.csv')
        reference = reference.rename(columns={f'bhi_{level}_id': identifier, f'{level}_name': 'entity'})
        prediction = prediction.merge(reference[[identifier, 'entity']], on=identifier, how='left', validate='one_to_one')
        if prediction.entity.isna().any():
            raise ValueError(f'Unknown {level} forecast identifiers.')
        metadata = ['entity', 'x', 'y'] + (['geographic_lhd_name'] if level == 'hospital' else [])
        locations = history[metadata].drop_duplicates('entity')
        prediction = prediction.merge(locations, on='entity', how='left', validate='one_to_one')
        columns = metadata + ['year', 'quarter', 'period'] + measures
        # Retain only forecast entities and measures; never expose historical-only features.
        history = history.loc[history.entity.isin(prediction.entity)].reindex(columns=columns)
        frames[level] = pd.concat([history, prediction[columns]], ignore_index=True).sort_values('period')
    districts = districts.loc[districts.lhd_name.isin(frames['lhd'].entity)]
    return frames, boundaries, districts
