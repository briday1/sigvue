"""Callable adapters that remove one-method lifecycle wrapper classes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from sigvue.plugin import (
    Analysis,
    Delivery,
    DeliveryContext,
    ParameterContext,
    Presentation,
    ViewContext,
)


SourceData = TypeVar("SourceData")
DeliveredData = TypeVar("DeliveredData")
SettingsData = TypeVar("SettingsData")
AnalysisProducts = TypeVar("AnalysisProducts")


class CallableDelivery(
    Delivery[SourceData, DeliveredData],
    Generic[SourceData, DeliveredData],
):
    """A concrete ``Delivery`` backed by one ordinary function."""

    def __init__(
        self,
        prepare: Callable[[SourceData, DeliveryContext], DeliveredData],
    ) -> None:
        if not callable(prepare):
            raise TypeError("prepare must be callable")
        self._prepare = prepare

    def prepare(
        self,
        source_data: SourceData,
        ui: DeliveryContext,
    ) -> DeliveredData:
        return self._prepare(source_data, ui)


class CallableAnalysis(
    Analysis[DeliveredData, SettingsData, AnalysisProducts],
    Generic[DeliveredData, SettingsData, AnalysisProducts],
):
    """A concrete ``Analysis`` backed by process and optional configure functions."""

    def __init__(
        self,
        process: Callable[[DeliveredData, SettingsData | None], AnalysisProducts],
        configure: Callable[[DeliveredData, ParameterContext], SettingsData] | None = None,
    ) -> None:
        if not callable(process):
            raise TypeError("process must be callable")
        if configure is not None and not callable(configure):
            raise TypeError("configure must be callable or omitted")
        self._process = process
        self._configure = configure

    @property
    def has_configuration(self) -> bool:
        return self._configure is not None

    def configure(
        self,
        data: DeliveredData,
        ui: ParameterContext,
    ) -> SettingsData | None:
        return None if self._configure is None else self._configure(data, ui)

    def process(
        self,
        data: DeliveredData,
        settings: SettingsData | None,
    ) -> AnalysisProducts:
        return self._process(data, settings)


class CallablePresentation(
    Presentation[AnalysisProducts],
    Generic[AnalysisProducts],
):
    """A concrete ``Presentation`` backed by one ordinary function."""

    def __init__(
        self,
        present: Callable[[AnalysisProducts, ViewContext], None],
    ) -> None:
        if not callable(present):
            raise TypeError("present must be callable")
        self._present = present

    def present(
        self,
        products: AnalysisProducts,
        ui: ViewContext,
    ) -> None:
        self._present(products, ui)
