import datetime
import itertools
from pathlib import Path
from typing import IO, Any, Callable, Dict, Hashable, Iterable, List, Optional, Union

import numpy as np
import pandas as pd
from loguru import logger

from .. import _alias_selection
from .._units import ureg
from . import _conversion
from ._interchange_format import (
    INTERCHANGE_FORMAT_COLUMN_ORDER,
    INTERCHANGE_FORMAT_MANDATORY_COLUMNS,
    INTERCHANGE_FORMAT_OPTIONAL_COLUMNS,
)

SEC_CATS_PREFIX = "sec_cats__"


def read_wide_csv_file_if(
    filepath_or_buffer: Union[str, Path, IO],
    *,
    coords_cols: Dict[str, str],
    coords_defaults: Optional[Dict[str, Any]] = None,
    coords_terminologies: Dict[str, str],
    coords_value_mapping: Optional[Dict[str, Any]] = None,
    filter_keep: Optional[Dict[str, Dict[str, Any]]] = None,
    filter_remove: Optional[Dict[str, Dict[str, Any]]] = None,
    meta_data: Optional[Dict[str, Any]] = None,
    time_format: str = "%Y",
) -> pd.DataFrame:
    """Read a CSV file in wide format into the PRIMAP2 interchange format.

    Columns can be renamed or filled with default vales to match the PRIMAP2 structure.
    Where we refer to "dimensions" in the parameter description below we mean the basic
    dimension names without the added terminology (e.g. "area" not "area (ISO3)"). The
    terminology information will be added by this function. You can not use the short
    dimension names in the attributes (e.g. "cat" instead of "category").

    TODO: Currently duplicate data points will not be detected.

    TODO: enable filtering through query strings

    TODO: enable specification of the entity terminology

    Parameters
    ----------
    filepath_or_buffer: str, pathlib.Path, or file-like
        Wide CSV file which will be read.

    coords_cols : dict
        Dict where the keys are column names in the files to be read and the value is
        the dimension in PRIMAP2. For secondary categories use a ``sec_cats__`` prefix.

    coords_defaults : dict, optional
        Dict for default values of coordinates / dimensions not given in the csv files.
        The keys are the dimension names and the values are the values for
        the dimensions. For secondary categories use a ``sec_cats__`` prefix.

    coords_terminologies : dict
        Dict defining the terminologies used for the different coordinates (e.g. ISO3
        for area). Only possible coordinates here are: area, category, scenario,
        entity, and secondary categories. For secondary categories use a ``sec_cats__``
        prefix. All entries different from "area", "category", "scenario", "entity", and
        ``sec_cats__<name>`` will raise a ValueError.

    coords_value_mapping : dict, optional
        A dict with primap2 dimension names as keys. Values are dicts with input values
        as keys and output values as values. A standard use case is to map gas names
        from input data to the standardized names used in primap2.
        Alternatively a value can also be a function which transforms one CSV metadata
        value into the new metadata value.
        A third possibility is to give a string as a value, which defines a rule for
        translating metadata values. The only defined rule at the moment is "PRIMAP1"
        which can be used for the "category", "entity", and "unit" columns to translate
        from PRIMAP1 metadata to PRIMAP2 metadata.

    filter_keep : dict, optional
        Dict defining filters of data to keep. Filtering is done before metadata
        mapping, so use original metadata values to define the filter. Column names are
        as in the csv file. Each entry in the dict defines an individual filter.
        The names of the filters have no relevance. Default: keep all data.

    filter_remove : dict, optional
        Dict defining filters of data to remove. Filtering is done before metadata
        mapping, so use original metadata values to define the filter. Column names are
        as in the csv file. Each entry in the dict defines an individual filter.
        The names of the filters have no relevance.

    meta_data : dict, optional
        Meta data for the whole dataset. Will end up in the dataset-wide attrs. Allowed
        keys are "references", "rights", "contact", "title", "comment", "institution",
        and "history". Documentation about the format and meaning of the meta data can
        be found in the
        `data format documentation <https://primap2.readthedocs.io/en/stable/data_format_details.html#dataset-attributes>`_.  # noqa: E501

    time_format : str, optional
        strftime style format used to parse the time information for the data columns.
        Default: "%Y", which will match years.

    Returns
    -------
    obj: pd.DataFrame
        pandas DataFrame with the read data

    Examples
    --------
    *Example for meta_mapping*::

        meta_mapping = {
            'pyCPA_col_1': {'col_1_value_1_in': 'col_1_value_1_out',
                            'col_1_value_2_in': 'col_1_value_2_out',
                            },
            'pyCPA_col_2': {'col_2_value_1_in': 'col_2_value_1_out',
                            'col_2_value_2_in': 'col_2_value_2_out',
                            },
        }

    *Example for filter_keep*::

        filter_keep = {
            'f_1': {'variable': ['CO2', 'CH4'], 'region': 'USA'},
            'f_2': {'variable': 'N2O'}
        }

    This example filter keeps all CO2 and CH4 data for the USA and N2O data for all
    countries

    *Example for filter_remove*::

        filter_remove = {
            'f_1': {'scenario': 'HISTORY'},
        }

    This filter removes all data with 'HISTORY' as scenario

    """
    # Check and prepare arguments
    if coords_defaults is None:
        coords_defaults = {}
    if meta_data is None:
        attrs = {}
    else:
        attrs = meta_data.copy()

    check_mandatory_dimensions(coords_cols, coords_defaults)
    check_overlapping_specifications(coords_cols, coords_defaults)

    data, time_columns = read_wide_csv(
        filepath_or_buffer, coords_cols, time_format=time_format
    )

    filter_data(data, filter_keep, filter_remove)

    add_dimensions_from_defaults(data, coords_defaults)

    naming_attrs = rename_columns(
        data, coords_cols, coords_defaults, coords_terminologies
    )
    attrs.update(naming_attrs)

    if coords_value_mapping is not None:
        map_metadata(data, attrs=attrs, meta_mapping=coords_value_mapping)

    coords = list(set(data.columns.values) - set(time_columns))

    harmonize_units(data, dimensions=coords)

    data = sort_columns_and_rows(data, dimensions=coords)

    data.attrs = interchange_format_attrs_dict(
        xr_attrs=attrs, time_format=time_format, dimensions=coords
    )

    return data


def interchange_format_attrs_dict(
    *, xr_attrs: dict, time_format: str, dimensions
) -> dict:
    return {
        "attrs": xr_attrs,
        "time_format": time_format,
        "dimensions": {"*": dimensions.copy()},
    }


def check_mandatory_dimensions(
    coords_cols: Dict[str, str],
    coords_defaults: Dict[str, Any],
):
    """Check if all mandatory dimensions are specified."""
    for coord in INTERCHANGE_FORMAT_MANDATORY_COLUMNS:
        if coord not in coords_cols and coord not in coords_defaults:
            logger.error(
                f"Mandatory dimension {coord} not found in coords_cols={coords_cols} or"
                f" coords_defaults={coords_defaults}."
            )
            raise ValueError(f"Mandatory dimension {coord} not defined.")


def check_overlapping_specifications(
    coords_cols: Dict[str, str],
    coords_defaults: Dict[str, Any],
):
    both = set(coords_cols.keys()).intersection(set(coords_defaults.keys()))
    if both:
        logger.error(
            f"{both!r} is given in coords_cols and coords_defaults, but"
            f" it must only be given in one of them."
        )
        raise ValueError(f"{both!r} given in coords_cols and coords_defaults.")


def matches_time_format(value: str, time_format: str):
    try:
        datetime.datetime.strptime(value, time_format)
        return True
    except ValueError:
        return False


def read_wide_csv(
    filepath_or_buffer,
    coords_cols: Dict[str, str],
    time_format: str,
) -> (pd.DataFrame, List[str]):

    na_values = [
        "nan",
        "NE",
        "-",
        "NA, NE",
        "NO,NE",
        "NA,NE",
        "NE,NO",
        "NE0",
        "NO, NE",
    ]
    data = pd.read_csv(filepath_or_buffer, na_values=na_values)

    # get all the columns that are actual data not metadata (usually the years)
    time_cols = [
        col for col in data.columns.values if matches_time_format(col, time_format)
    ]

    # remove all non-numeric values from year columns
    # (what is left after mapping to nan when reading data)
    for col in time_cols:
        data[col] = data[col][pd.to_numeric(data[col], errors="coerce").notnull()]

    # remove all cols not in the specification
    columns = data.columns.values
    data.drop(
        columns=list(set(columns) - set(coords_cols.values()) - set(time_cols)),
        inplace=True,
    )

    # check that all cols in the specification could be read
    missing = set(coords_cols.values()) - set(data.columns.values)
    if missing:
        logger.error(
            f"Column(s) {missing} specified in coords_cols, but not found in "
            f"the CSV file {filepath_or_buffer!r}."
        )
        raise ValueError(f"Columns {missing} not found in CSV.")

    return data, time_cols


def spec_to_query_string(filter_spec: Dict[str, Union[list, Any]]) -> str:
    """Convert filter specification to query string.

    All column conditions in the filter are combined with &."""
    queries = []
    for col in filter_spec:
        if isinstance(filter_spec[col], list):
            itemlist = ", ".join((repr(x) for x in filter_spec[col]))
            filter_query = f"{col} in [{itemlist}]"
        else:
            filter_query = f"{col} == {filter_spec[col]!r}"
        queries.append(filter_query)

    return " & ".join(queries)


def filter_data(
    data: pd.DataFrame,
    filter_keep: Optional[Dict[str, Dict[str, Any]]] = None,
    filter_remove: Optional[Dict[str, Dict[str, Any]]] = None,
):
    # Filters for keeping data are combined with "or" so that
    # everything matching at least one rule is kept.
    if filter_keep:
        queries = []
        for filter_spec in filter_keep.values():
            q = spec_to_query_string(filter_spec)
            queries.append(f"({q})")
        query = " | ".join(queries)
        data.query(query, inplace=True)

    # Filters for removing data are negated and combined with "and" so that
    # only rows which don't match any rule are kept.
    if filter_remove:
        queries = []
        for filter_spec in filter_remove.values():
            q = spec_to_query_string(filter_spec)
            queries.append(f"~({q})")
        query = " & ".join(queries)
        data.query(query, inplace=True)

    data.reset_index(drop=True, inplace=True)


def add_dimensions_from_defaults(
    data: pd.DataFrame,
    coords_defaults: Dict[str, Any],
):
    if_columns = (
        INTERCHANGE_FORMAT_OPTIONAL_COLUMNS + INTERCHANGE_FORMAT_MANDATORY_COLUMNS
    )
    for coord in coords_defaults.keys():
        if coord in if_columns or coord.startswith(SEC_CATS_PREFIX):
            # add column to dataframe with default value
            data[coord] = coords_defaults[coord]
        else:
            raise ValueError(
                f"{coord!r} given in coords_defaults is unknown - prefix with "
                f"{SEC_CATS_PREFIX!r} to add a secondary category."
            )


def map_metadata(
    data: pd.DataFrame,
    *,
    meta_mapping: Dict[str, Union[str, Callable, dict]],
    attrs: Dict[str, Any],
):
    """Map the metadata according to specifications given in meta_mapping."""
    dim_aliases = _alias_selection.translations_from_attrs(attrs, include_entity=True)

    # TODO: add additional mapping functions here
    # values: (function, additional arguments)
    mapping_functions = {
        "PRIMAP1": {
            "category": (_conversion.convert_ipcc_code_primap_to_primap2, []),
            "entity": (_conversion.convert_entity_gwp_primap_to_primap2, []),
            "unit": (
                _conversion.convert_unit_primap_to_primap2,
                [dim_aliases.get("entity", "entity")],
            ),
        }
    }

    meta_mapping_df = {}
    # preprocess meta_mapping
    for column, mapping in meta_mapping.items():
        column_name = dim_aliases.get(column, column)
        if isinstance(mapping, str) or callable(mapping):
            if isinstance(mapping, str):  # need to translate to function first
                try:
                    func, args = mapping_functions[mapping][column]
                except KeyError:
                    logger.error(
                        f"Unknown metadata mapping {mapping} for column {column}, "
                        f"known mappings are: {list(mapping_functions.keys())}."
                    )
                    raise ValueError(
                        f"Unknown metadata mapping {mapping} for column {column}."
                    )
            else:
                func = mapping
                args = []

            if not args:  # simple case: no additional args needed
                values_to_map = data[column_name].unique()
                values_mapped = map(func, values_to_map)
                meta_mapping_df[column_name] = dict(zip(values_to_map, values_mapped))

            else:  # need to supply additional arguments
                # this can't be handled using the replace()-call later since the mapped
                # values don't depend on the original values only, therefore
                # we do it directly
                sel = [column_name] + args
                values_to_map = np.unique(data[sel].to_records(index=False))
                for vals_to_map in values_to_map:
                    # we replace values where all the arguments match - build a
                    # selector for that, then do the replacement
                    selector = data[column_name] == vals_to_map[0]
                    for i, arg in enumerate(args):
                        selector &= data[arg] == vals_to_map[i + 1]

                    data.loc[selector, column_name] = func(*vals_to_map)

        else:
            meta_mapping_df[column_name] = mapping

    data.replace(meta_mapping_df, inplace=True)


def rename_columns(
    data: pd.DataFrame,
    coords_cols: Dict[str, str],
    coords_defaults: Dict[str, Any],
    coords_terminologies: Dict[str, str],
) -> dict:
    """Rename columns to match PRIMAP2 specifications and generate the corresponding
    dataset-wide attrs for PRIMAP2."""

    attr_names = {"category": "cat", "scenario": "scen", "area": "area"}

    attrs = {}
    sec_cats = []
    coord_renaming = {}
    for coord in itertools.chain(coords_cols, coords_defaults):
        if coord in coords_terminologies:
            name = f"{coord} ({coords_terminologies[coord]})"
        else:
            name = coord

        if coord.startswith(SEC_CATS_PREFIX):
            name = name[len(SEC_CATS_PREFIX) :]
            sec_cats.append(name)
        elif coord in attr_names:
            attrs[attr_names[coord]] = name

        coord_renaming[coords_cols.get(coord, coord)] = name

    data.rename(columns=coord_renaming, inplace=True)

    if sec_cats:
        attrs["sec_cats"] = sec_cats

    return attrs


def harmonize_units(
    data: pd.DataFrame,
    *,
    unit_col: str = "unit",
    entity_col: str = "entity",
    dimensions: Iterable[str],
) -> None:
    """
    Harmonize the units of the input data. For each entity, convert
    all time series to the same unit (the unit that occurs first). Units must already
    be in PRIMAP2 style.

    Parameters
    ----------
    data: pd.DataFrame
        data for which the units should be harmonized
    unit_col: str
        column name for unit column. Default: "unit"
    entity_col: str
        column name for entity column. Default: "entity"
    dimensions: list of str
        the dimensions, i.e. the metadata columns.

    Returns
    -------
    None
        The data is altered in place.
    """
    # we need to convert the data such that we have one unit per entity
    data_cols = list(set(data.columns.values) - set(dimensions))

    entities = data[entity_col].unique()
    for entity in entities:
        # get all units for this entity
        data_this_entity = data.loc[data[entity_col] == entity]
        units_this_entity = data_this_entity[unit_col].unique()
        # print(units_this_entity)
        if len(units_this_entity) > 1:
            # need unit conversion. convert to first unit (base units have second as
            # time that is impractical)
            unit_to = units_this_entity[0]
            # print("unit_to: " + unit_to)
            for unit in units_this_entity[1:]:
                # print(unit)
                unit_pint = ureg[unit]
                unit_pint = unit_pint.to(unit_to)
                # print(unit_pint)
                factor = unit_pint.magnitude
                # print(factor)
                mask = (data[entity_col] == entity) & (data[unit_col] == unit)
                data.loc[mask, data_cols] *= factor
                data.loc[mask, unit_col] = unit_to


def sort_columns_and_rows(
    data: pd.DataFrame,
    dimensions: Iterable[Hashable],
) -> pd.DataFrame:
    """Sort the data.

    The columns are ordered according to the order in
    INTERCHANGE_FORMAT_COLUMN_ORDER, with secondary categories alphabetically after
    the category and all date columns in order at the end.

    The rows are ordered by values of the non-date columns.

    Parameters
    ----------
    data: pd.DataFrame
        data which should be ordered
    dimensions: list of str
        the dimensions, i.e. the metadata columns.

    Returns
    -------
    sorted : pd.DataFrame
        the input data frame with columns and rows ordered.
    """
    time_cols = list(set(data.columns.values) - set(dimensions))

    other_cols = list(dimensions)
    cols_sorted = []
    for col in INTERCHANGE_FORMAT_COLUMN_ORDER:
        for ocol in other_cols:
            if ocol == col or (isinstance(ocol, str) and ocol.startswith(f"{col} (")):
                cols_sorted.append(ocol)
                other_cols.remove(ocol)
                break

    cols_sorted += list(sorted(other_cols))

    data = data[cols_sorted + list(sorted(time_cols))]

    data.sort_values(by=cols_sorted, inplace=True)
    data.reset_index(inplace=True, drop=True)

    return data
