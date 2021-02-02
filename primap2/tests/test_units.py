#!/usr/bin/env python
"""Tests for _units.py"""

import xarray as xr

from .utils import allclose, assert_equal


def test_roundtrip_quantify(opulent_ds: xr.Dataset):
    roundtrip = opulent_ds.pr.dequantify().pr.quantify()
    assert roundtrip == opulent_ds


def test_roundtrip_quantify_da(opulent_ds: xr.Dataset):
    da: xr.DataArray = opulent_ds["SF6 (SARGWP100)"]
    roundtrip = da.pr.dequantify().pr.quantify()
    assert_equal(roundtrip, da)


def test_convert_to_gwp(opulent_ds: xr.Dataset):
    da: xr.DataArray = opulent_ds["SF6"]
    da_converted = da.pr.convert_to_gwp("SARGWP100", "CO2 Gg / year")
    da_expected = opulent_ds["SF6 (SARGWP100)"]
    assert_equal(da_converted, da_expected)

    da_converted_like = da.pr.convert_to_gwp_like(da_expected)
    assert_equal(da_converted_like, da_expected)


def test_convert_to_mass(opulent_ds: xr.Dataset):
    da: xr.DataArray = opulent_ds["SF6 (SARGWP100)"]
    da_converted = da.pr.convert_to_mass()
    da_expected = opulent_ds["SF6"]
    assert_equal(da_converted, da_expected)


def test_context(opulent_ds: xr.Dataset):
    da: xr.DataArray = opulent_ds["SF6 (SARGWP100)"]
    with da.pr.gwp_context:
        da_converted = opulent_ds["SF6"].pint.to(da.pint.units)
    assert allclose(da, da_converted)
