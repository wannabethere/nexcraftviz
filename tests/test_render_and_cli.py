"""Server-side rendering and the command line."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexcraftviz import cli
from nexcraftviz.render import available as render_available
from nexcraftviz.render import to_html, to_png, to_svg, to_vega
from nexcraftviz.spec.model import Spec

needs_render = pytest.mark.skipif(not render_available(), reason="needs the `render` extra")


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

@needs_render
def test_compiles_to_vega(bar_spec: Spec) -> None:
    compiled = to_vega(bar_spec)
    assert "/schema/vega/v" in compiled["$schema"]
    assert compiled["marks"]


@needs_render
def test_renders_svg(bar_spec: Spec) -> None:
    svg = to_svg(bar_spec)
    assert svg.startswith("<svg") and "</svg>" in svg


@needs_render
def test_renders_png(bar_spec: Spec) -> None:
    png = to_png(bar_spec)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@needs_render
def test_scale_produces_a_larger_image(bar_spec: Spec) -> None:
    assert len(to_png(bar_spec, scale=2.0)) > len(to_png(bar_spec, scale=1.0))


@needs_render
@pytest.mark.parametrize("suffix", [".png", ".svg", ".json", ".html"])
def test_save_infers_the_format_from_the_suffix(bar_spec: Spec, tmp_path: Path, suffix) -> None:
    from nexcraftviz.render import save

    out = save(bar_spec, tmp_path / f"chart{suffix}")
    assert out.exists() and out.stat().st_size > 0


def test_save_rejects_an_unknown_suffix(bar_spec: Spec, tmp_path: Path) -> None:
    from nexcraftviz.render import save

    with pytest.raises(ValueError, match="cannot infer format"):
        save(bar_spec, tmp_path / "chart.bmp")


def test_html_output_is_self_contained(bar_spec: Spec) -> None:
    """No render extra needed — this is string templating, not rasterising."""
    html = to_html(bar_spec, title="Revenue")
    assert "<title>Revenue</title>" in html
    assert "vegaEmbed" in html
    assert '"revenue"' in html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@pytest.fixture
def spec_file(bar_spec: Spec, tmp_path: Path) -> Path:
    path = tmp_path / "spec.json"
    path.write_text(bar_spec.to_json(indent=2), encoding="utf-8")
    return path


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "data.csv"
    path.write_text("region,revenue\nWest,120.5\nEast,98\n", encoding="utf-8")
    return path


def test_profile_command(csv_file: Path, capsys) -> None:
    assert cli.main(["profile", str(csv_file)]) == 0
    payload = json.loads(capsys.readouterr().out)
    types = {c["name"]: c["type"] for c in payload["columns"]}
    assert types == {"region": "nominal", "revenue": "quantitative"}


def test_csv_numbers_are_coerced(csv_file: Path) -> None:
    """Without coercion every CSV column profiles as nominal."""
    rows = cli.load_rows(str(csv_file))
    assert rows[0]["revenue"] == 120.5 and rows[1]["revenue"] == 98


def test_validate_command_succeeds(spec_file: Path, capsys) -> None:
    assert cli.main(["validate", str(spec_file)]) == 0
    assert "valid" in capsys.readouterr().out


def test_validate_command_reports_failure_with_exit_code(tmp_path: Path, capsys) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"title": "nothing renderable"}), encoding="utf-8")

    assert cli.main(["validate", str(broken)]) == 2
    assert "no_renderable_content" in capsys.readouterr().out


def test_validate_command_repairs_and_writes(tmp_path: Path, rows, capsys) -> None:
    source = tmp_path / "spec.json"
    source.write_text(
        json.dumps(
            {
                "data": {"values": rows},
                "mark": "bar",
                "encoding": {"x": {"field": "Revenue", "type": "quantitative"}},
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "fixed.json"

    assert cli.main(["validate", str(source), "--repair", "--out", str(out)]) == 0
    assert "repaired" in capsys.readouterr().out
    fixed = json.loads(out.read_text())
    assert fixed["encoding"]["x"]["field"] == "revenue"


def test_edit_command_applies_ops(spec_file: Path, capsys) -> None:
    ops = json.dumps([{"op": "set_title", "text": "Revenue by region"}])
    assert cli.main(["edit", str(spec_file), ops]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Revenue by region"


def test_edit_command_reads_ops_from_a_file(spec_file: Path, tmp_path: Path, capsys) -> None:
    ops_file = tmp_path / "ops.json"
    ops_file.write_text(json.dumps([{"op": "set_size", "width": 900}]), encoding="utf-8")

    assert cli.main(["edit", str(spec_file), f"@{ops_file}"]) == 0
    assert json.loads(capsys.readouterr().out)["width"] == 900


def test_edit_command_reports_a_failed_op_but_still_writes(spec_file: Path, capsys) -> None:
    ops = json.dumps(
        [{"op": "set_title", "text": "kept"}, {"op": "drop_series", "index": 4}]
    )
    assert cli.main(["edit", str(spec_file), ops]) == 2
    captured = capsys.readouterr()
    assert "skipped drop_series" in captured.err
    assert json.loads(captured.out)["title"] == "kept"


@needs_render
def test_render_command(spec_file: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "chart.png"
    assert cli.main(["render", str(spec_file), str(out)]) == 0
    assert out.stat().st_size > 1000
    assert "wrote" in capsys.readouterr().out


def test_chart_command_builds_without_a_model(csv_file: Path, capsys) -> None:
    assert cli.main(["chart", str(csv_file), "revenue by region"]) == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["mark"] == "bar"
    assert spec["data"]["values"]


def test_chart_json_output_is_the_vega_lite_spec(csv_file: Path, tmp_path: Path) -> None:
    """`.json` means "compiled Vega" to the renderer, which is right for
    `render` and wrong here — asking for "the spec" should give the editable
    one, and the round trip through `validate` proves it."""
    out = tmp_path / "spec.json"
    assert cli.main(["chart", str(csv_file), "revenue by region", "--out", str(out)]) == 0

    spec = json.loads(out.read_text())
    assert "mark" in spec, "compiled Vega has `marks`, not `mark`"
    assert cli.main(["validate", str(out), "--data", str(csv_file)]) == 0


def test_chart_command_honours_a_forced_type_and_theme(csv_file: Path, capsys) -> None:
    assert cli.main(["chart", str(csv_file), "--type", "donut",
                     "--theme", "nexcraftviz-dark"]) == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["mark"]["type"] == "arc"
    assert "config" in spec


def test_chart_command_refuses_data_it_cannot_chart(tmp_path: Path, capsys) -> None:
    data = tmp_path / "text.json"
    data.write_text(json.dumps([{"note": "a"}, {"note": "b"}]), encoding="utf-8")
    assert cli.main(["chart", str(data)]) == 2
    assert "error" in capsys.readouterr().err


def test_ops_command_lists_the_algebra(capsys) -> None:
    assert cli.main(["ops"]) == 0
    output = capsys.readouterr().out
    assert "set_palette" in output and "limit_top_n" in output


def test_ops_command_emits_a_usable_json_schema(capsys) -> None:
    assert cli.main(["ops", "--json"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert "ops" in schema["properties"]


def test_missing_file_is_a_clean_error(capsys) -> None:
    assert cli.main(["validate", "/nonexistent/spec.json"]) == 1
    assert "error:" in capsys.readouterr().err
