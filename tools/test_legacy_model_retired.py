import ast
from pathlib import Path


def test_production_modules_do_not_import_legacy_model():
    for path in (
        Path('scraper/score.py'),
        Path('scraper/scoring_v3.py'),
        Path('scraper/mixing_model_b.py'),
        Path('scraper/scoring_pipeline.py'),
    ):
        tree = ast.parse(path.read_text())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.extend(f'{node.module}.{alias.name}' for alias in node.names)
        assert 'scraper.mixing_model' not in imported


def test_forward_risk_owns_its_metric_history():
    from scraper import mixing_model_b

    assert hasattr(mixing_model_b, '_HIST_B')


def test_pipeline_removes_cached_legacy_fields():
    source = Path('scraper/scoring_pipeline.py').read_text()
    for key in ('v5_score', 'v5_confidence', 'v5_shap_top5', 'legacy_model'):
        assert f"p.pop(_retired_key" in source
        assert key in source
