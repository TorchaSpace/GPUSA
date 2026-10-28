"""The depot's movement PDF reads typed names as text, not as reportlab markup."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from depot_app.export.movement_report_export import export_pdf


def _report(title="Movements", product="Carton", reference=None):
    return SimpleNamespace(
        title=title, period_text="today", kinds_present=[],
        lines=[SimpleNamespace(barcode="A1", name=product, units_in=1, units_out=0, net=1, by_kind={})],
        units_in=1, units_out=0, net=1, kind_total=lambda key: 0,
        movements=[{"created_at": "2026-10-08T10:00:00Z", "movement_type": "receive", "barcode": "A1",
                    "product_name": product, "quantity": 1, "reason": "receive", "reference": reference,
                    "handled_by": "Ada"}],
    )


def _text(path) -> str:
    return " ".join(page.extract_text() for page in PdfReader(str(path)).pages)


def test_markup_looking_names_appear_verbatim(tmp_path):
    path = tmp_path / "m.pdf"
    export_pdf(_report(title="Depo <b>X", product="Bolts <M8> x", reference="REF<b>9</b>Z"), path)
    text = _text(path)
    assert "Bolts <M8> x" in text and "REF<b>9</b>Z" in text and "Depo <b>X" in text


@pytest.mark.parametrize("reference", ["</para>", "<img src='/nonexistent.png'/>", "a <unknown x", "R&D <i>"])
def test_hostile_references_do_not_break_the_export(tmp_path, reference):
    path = tmp_path / "m.pdf"
    export_pdf(_report(reference=reference), path)
    assert path.exists() and path.stat().st_size > 0
