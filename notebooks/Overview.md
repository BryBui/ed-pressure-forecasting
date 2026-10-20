# ED Pressure Forecasting Data Sources

Direct predictors measure ED demand, congestion, waiting, or patient flow. Indirect predictors describe conditions that may influence demand or hospital capacity but do not measure ED pressure themselves.

## Direct predictors

<div style="overflow-x: auto;">

| Data source | Main purpose | Retrieval type | Retrieval frequency | Typical granularity | Key measures / fields | Likely join keys | Required transformation for dashboard use | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **NSW Bureau of Health Information (BHI)** | Current NSW ED activity and performance | Downloadable Excel files / BHI Data Portal | Quarterly | Hospital, Local Health District (LHD), peer group and NSW | ED attendances, treatment started on time, time in ED, admitted-patient flow, ambulance transfer performance | Hospital/LHD name, reporting quarter | Reshape workbook tables, standardise hospital and LHD names, parse quarters, align measure definitions, calculate rolling and composite pressure scores | **Very High** |

</div>

## Indirect predictors

<div style="overflow-x: auto;">

| Data source | Main purpose | Retrieval type | Retrieval frequency | Typical granularity | Key measures / fields | Likely join keys | Required transformation for dashboard use | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Weather / BoM** | Weather-related changes in ED demand | Download/feed; interface depends on selected BoM product | Hourly to daily depending on product | Weather station × timestamp | Temperature, rainfall, humidity, wind and extreme-weather measures | Station ID, latitude/longitude, timestamp | Parse observations, standardise units, derive daily summaries and extreme-weather flags, map stations to hospitals/LHDs, create lagged variables | **High** if short-term forecasting is pursued |
| **NSW Air Quality** | Environmental contribution to ED demand | API / near-real-time feed | Hourly / near-real-time | Monitoring station × timestamp | PM2.5, PM10, ozone, NO₂, other pollutants and air-quality categories | Station ID, latitude/longitude, timestamp | Clean timestamps, remove invalid readings, derive daily/hourly summaries, map stations to hospitals/LHDs, create lagged exposure variables | **High** if environmental-demand analysis remains in scope |
| **ABS Data API** | Population and demographic context | SDMX API | Dataset-dependent: monthly, quarterly, annual; Census every 5 years | SA1/SA2/SA3/LGA/state depending on dataset | Population, age, sex, household characteristics and demographic counts/rates | SA2/SA3, LGA, postcode correspondence, state, year | Decode SDMX dimensions, aggregate to the selected geography, calculate population denominators/rates and align reporting years | **High** |
| **ABS SEIFA** | Socioeconomic disadvantage and advantage | Static CSV / spreadsheet download | Every 5 years | SA1/SA2/LGA/postcode-related geography depending on release | IRSD, IRSAD, IEO and IER scores, ranks and deciles | SA1/SA2/LGA/geographic code | Select relevant index, standardise geography codes, join correspondence tables, aggregate where needed and label ranks clearly | **Medium–High** |
| **HealthStats NSW** | Public-health outcomes and disease burden | Mainly periodic download / batch extraction | Indicator-dependent; often annual or periodic | NSW, LHD, LGA or other regional level depending on indicator | Chronic disease prevalence, mortality, morbidity, behavioural and health indicators | LHD/LGA/region, year, indicator | Extract required indicators, harmonise geographic labels, reshape data, standardise rates and align reporting periods | **Medium** |
| **Medicare / MBS statistics** | Healthcare utilisation and access outside hospitals | Download / dataset-dependent interface | Monthly, quarterly or annual depending on dataset | Geographic area, service/item and period | Service counts, benefits, utilisation and MBS item activity | Geography, MBS item code, reporting period | Map MBS codes to categories, aggregate services, calculate per-capita utilisation and align geographies with ABS/LHD boundaries | **Low–Medium** |

</div>

## Supporting reference data

These sources enable joins and geographic aggregation but are not predictors by themselves.

<div style="overflow-x: auto;">

| Data source | Main purpose | Retrieval type | Update frequency | Typical granularity | Key fields | Required transformation | Priority |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **AIHW MyHospitals spatial service** | Resolve BHI hospital coordinates and naming differences | Reference API | When hospital references are rebuilt | Hospital point | Reporting-unit code, hospital name, latitude and longitude | Exact-name matching plus reviewed aliases; do not ingest performance measures | **Very High** for weather mapping only |
| **Geographic correspondence / reference tables** | Cross-source integration | Static reference files | When classifications or service boundaries change | Geographic-code mapping | Postcode↔SA2/LGA/LHD, hospital↔LHD, station↔region | Standardise codes, resolve many-to-many relationships and maintain effective dates/versioning | **Very High** |

</div>
