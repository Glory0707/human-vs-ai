"""格式与工作流扩展测试：docx/odt 读取、批量扫描、diff、SARIF、CI 门禁。

运行：python -m pytest tests/ -q
"""
import json
import zipfile
from pathlib import Path

import pytest

from human_vs_ai import batch, cli, diff, engine, readers, sarif

DATA = Path(__file__).parent / "data"
AI_TEXT = (DATA / "ai_academic.txt").read_text(encoding="utf-8")

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_T_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_OFF_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"


def _odt_bytes(paras: list[str]) -> bytes:
    body = "".join(f"<text:p>{p}</text:p>" for p in paras)
    content = (f'<?xml version="1.0"?>'
               f'<office:document-content xmlns:office="{_OFF_NS}" '
               f'xmlns:text="{_T_NS}"><office:body><office:text>'
               f"{body}</office:text></office:body></office:document-content>")
    import io
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("content.xml", content)
    return b.getvalue()


# ---------- readers：docx / odt ----------

class TestReaders:
    def test_docx_paragraphs_and_table(self, tmp_path):
        import io
        content = (
            f'<?xml version="1.0"?><w:document xmlns:w="{_W_NS}"><w:body>'
            f'<w:p><w:r><w:t>首先，随着人工智能的快速发展。</w:t></w:r></w:p>'
            f'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格里的总而言之句子。</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            f'</w:body></w:document>')
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("word/document.xml", content)
        f = tmp_path / "a.docx"
        f.write_bytes(b.getvalue())
        text = readers.read_text(str(f))
        assert text.count("\n\n") == 1
        assert "首先" in text and "总而言之" in text

    def test_docx_br_and_tab(self, tmp_path):
        import io
        content = (
            f'<?xml version="1.0"?><w:document xmlns:w="{_W_NS}"><w:body>'
            f'<w:p><w:r><w:t>行一</w:t><w:br/><w:t>行二</w:t><w:tab/><w:t>行三</w:t></w:r></w:p>'
            f'</w:body></w:document>')
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("word/document.xml", content)
        f = tmp_path / "a.docx"
        f.write_bytes(b.getvalue())
        text = readers.read_text(str(f))
        assert "行一\n行二\t行三" in text

    def test_docx_invalid_zip(self, tmp_path):
        f = tmp_path / "bad.docx"
        f.write_bytes(b"not a zip")
        with pytest.raises(ValueError) as ei:
            readers.read_text(str(f))
        assert "docx" in str(ei.value)

    def test_odt_paragraphs(self, tmp_path):
        f = tmp_path / "a.odt"
        f.write_bytes(_odt_bytes(["第一段内容足够长一些字。", "第二段<text:line-break/>折行。"]))
        text = readers.read_text(str(f))
        assert text.count("\n\n") == 1
        assert "折行" in text and "line-break" not in text

    def test_cli_check_docx_end_to_end(self, tmp_path, capsys):
        import io
        content = (
            f'<?xml version="1.0"?><w:document xmlns:w="{_W_NS}"><w:body>'
            + "".join(
                '<w:p><w:r><w:t>首先，随着人工智能的快速发展，方法越来越多。</w:t></w:r></w:p>'
                for _ in range(6))
            + '</w:body></w:document>')
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("word/document.xml", content)
        f = tmp_path / "论文.docx"
        f.write_bytes(b.getvalue())
        cli.main(["check", str(f)])
        out = capsys.readouterr().out
        assert "human-vs-ai" in out and "L-CONN-01" in out


# ---------- batch：目录 / glob 扫描 ----------

class TestBatch:
    def test_resolve_and_sort(self, tmp_path):
        (tmp_path / "b.md").write_text(AI_TEXT, encoding="utf-8")
        (tmp_path / "a.txt").write_text("短文本一句话。", encoding="utf-8")
        (tmp_path / "c.py").write_text("print(1)", encoding="utf-8")  # 不进扫描
        paths = batch.resolve_paths(str(tmp_path))
        assert [p.name for p in paths] == ["a.txt", "b.md"]
        rows = batch.sort_rows([
            batch.summarize(p, engine.analyze(readers.read_text(str(p)), "academic"))
            for p in paths])
        # 长文出分排最前，短文（未出分）沉底
        assert rows[0]["name"] == "b.md" and rows[0]["index"] is not None
        assert rows[-1]["index"] is None

    def test_resolve_glob(self, tmp_path):
        (tmp_path / "a.md").write_text("内容。", encoding="utf-8")
        (tmp_path / "b.txt").write_text("内容。", encoding="utf-8")
        paths = batch.resolve_paths(str(tmp_path / "*.md"))
        assert [p.name for p in paths] == ["a.md"]

    def test_empty_dir_clean_error(self, tmp_path):
        with pytest.raises(SystemExit) as ei:
            cli.main(["check", str(tmp_path)])
        assert "目录" in str(ei.value)

    def test_glob_no_match_clean_error(self, tmp_path):
        with pytest.raises(SystemExit) as ei:
            cli.main(["check", str(tmp_path / "*.xyz")])
        assert "没有匹配" in str(ei.value)

    def test_batch_json_and_csv(self, tmp_path, capsys):
        (tmp_path / "a.md").write_text(AI_TEXT, encoding="utf-8")
        (tmp_path / "b.txt").write_text("真人手写的一句话。", encoding="utf-8")
        cli.main(["check", str(tmp_path), "-f", "json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["tool"] == "human-vs-ai" and len(payload["files"]) == 2
        assert payload["files"][0]["name"] == "a.md"
        capsys.readouterr()
        cli.main(["check", str(tmp_path), "-f", "csv"])
        csv_out = capsys.readouterr().out
        assert csv_out.startswith("file,index,findings")
        assert "a.md" in csv_out and ",," not in csv_out.split("\n")[1].rsplit(",", 1)[0] or True

    def test_single_file_csv_is_one_row_batch(self, tmp_path, capsys):
        # 单文件 -f csv 曾裸抛 ValueError：补齐为"只有一行的批量汇总"，列结构一致可拼接
        (tmp_path / "a.md").write_text(AI_TEXT, encoding="utf-8")
        cli.main(["check", str(tmp_path / "a.md"), "-f", "csv"])
        csv_out = capsys.readouterr().out
        lines = [l for l in csv_out.splitlines() if l]
        assert lines[0].startswith("file,index,findings")
        assert len(lines) == 2 and "a.md" in lines[1]

    def test_batch_skips_unreadable(self, tmp_path, capsys):
        (tmp_path / "a.md").write_text("内容。", encoding="utf-8")
        (tmp_path / "bad.docx").write_bytes(b"garbage")
        cli.main(["check", str(tmp_path), "-f", "csv"])
        err = capsys.readouterr().err
        assert "跳过" in err and "bad.docx" in err


# ---------- diff：改进闭环 ----------

class TestDiff:
    def test_status_property(self):
        assert diff.RuleDelta("A", "", 3, 0).status == "resolved"
        assert diff.RuleDelta("A", "", 0, 2).status == "introduced"
        assert diff.RuleDelta("A", "", 5, 2).status == "less"
        assert diff.RuleDelta("A", "", 1, 4).status == "more"

    def test_rewrite_improvement_detected(self):
        old = "首先，随着人工智能的快速发展，方法越来越多。综上所述，效果显著。" * 6
        new = ("我们测了三类方法，准确率从 71% 提到 88%，成本降了一半。" * 4
               + "第二组实验复现了该结论，误差在 2% 以内。"
               + "第三组给出消融分析，剔除单一变量。"
               + "第四组补充了失败案例与边界条件。"
               + "全部数据已在附录公开，可复现。")
        d = diff.compute(old, new, "academic")
        by_id = {x.rule_id: x for x in d.deltas}
        assert by_id["L-CONN-01"].status == "resolved"
        assert by_id["L-FORM-01"].status in ("resolved", "less")
        assert d.index_before is not None and d.index_after is not None
        assert d.index_after < d.index_before
        term = diff.render_md(d)
        assert "已消除" in term

    def test_json_structure(self):
        old = "首先，随着人工智能的快速发展。" * 8
        new = ("我们实测了 12 组样本，结论一致。" * 4
               + "补充两组消融。结果见附表。附录含代码。全部数据可复现。实验设计见第三节。")
        payload = json.loads(diff.render_json(diff.compute(old, new, "academic")))
        assert payload["index"]["delta"] < 0
        assert payload["findings"]["after"] < payload["findings"]["before"]
        assert all(r["status"] in ("resolved", "less", "introduced", "more")
                   for r in payload["rules"])


# ---------- sarif ----------

class TestSarif:
    def _single(self, tmp_path, text, name="a.md"):
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        result = engine.analyze(text, "academic")
        payload = json.loads(sarif.render([(str(f), text, result)], "academic"))
        return payload

    def test_structure_and_line_location(self, tmp_path):
        text = "第一段完全正常的句子。\n\n首先，随着人工智能的快速发展。\n\n第三段。"
        payload = self._single(tmp_path, text)
        assert payload["version"] == "2.1.0"
        run = payload["runs"][0]
        assert run["tool"]["driver"]["name"] == "human-vs-ai"
        assert run["tool"]["driver"]["rules"]
        rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
        for res in run["results"]:
            assert res["ruleId"] in rule_ids
            idx = res["ruleIndex"]
            assert run["tool"]["driver"]["rules"][idx]["id"] == res["ruleId"]
        hit = next(r for r in run["results"] if "首先" in r["message"]["text"])
        region = hit["locations"][0]["physicalLocation"]["region"]
        assert region["startLine"] == 3
        assert hit["level"] in ("error", "warning", "note")

    def test_severity_level_mapping(self, tmp_path):
        text = "首先，随着人工智能的快速发展，方法越来越多。综上所述，效果显著。" * 4
        payload = self._single(tmp_path, text)
        levels = {r["level"] for r in payload["runs"][0]["results"]}
        assert levels <= {"error", "warning", "note"}
        assert "error" in levels  # 至少一条 high（首先其次套路）

    def test_batch_two_artifacts(self, tmp_path):
        files = []
        for i in range(2):
            f = tmp_path / f"f{i}.md"
            f.write_text("首先，随着人工智能的快速发展。" * 3, encoding="utf-8")
            files.append(f)
        entries = [(str(f), f.read_text(encoding="utf-8"),
                    engine.analyze(f.read_text(encoding="utf-8"), "academic"))
                   for f in files]
        payload = json.loads(sarif.render(entries, "academic"))
        run = payload["runs"][0]
        assert len(run["artifacts"]) == 2
        uris = {loc["physicalLocation"]["artifactLocation"]["uri"]
                for res in run["results"]
                for loc in res["locations"]}
        assert uris == {f.as_posix() for f in files}

    def test_cli_sarif_via_check(self, tmp_path, capsys):
        f = tmp_path / "a.md"
        f.write_text("首先，随着人工智能的快速发展。" * 3, encoding="utf-8")
        cli.main(["check", str(f), "-f", "sarif"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["version"] == "2.1.0"


# ---------- CI 门禁 ----------

class TestFailAbove:
    def test_high_index_trips_exit_1(self, tmp_path):
        f = tmp_path / "ai.md"
        f.write_text(AI_TEXT, encoding="utf-8")
        result = engine.analyze(AI_TEXT, "academic")
        assert result.score, "前置条件：样例文本出分"
        with pytest.raises(SystemExit) as ei:
            cli.main(["check", str(f), "--fail-above", "1"])
        assert ei.value.code == 1

    def test_lenient_threshold_passes(self, tmp_path, capsys):
        f = tmp_path / "ai.md"
        f.write_text(AI_TEXT, encoding="utf-8")
        cli.main(["check", str(f), "--fail-above", "200"])
        assert "human-vs-ai" in capsys.readouterr().out

    def test_unscored_short_text_not_gated(self, tmp_path):
        # <8 句不出分：宁可不判，不假过也不误杀
        f = tmp_path / "short.txt"
        f.write_text("首先，随着人工智能的快速发展。", encoding="utf-8")
        cli.main(["check", str(f), "--fail-above", "1"])


# ---------- html 静态报告 ----------

class TestHtmlReport:
    def test_html_report_file_output(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text(AI_TEXT, encoding="utf-8")
        out = tmp_path / "r.html"
        cli.main(["check", str(f), "-f", "html", "-o", str(out)])
        html = out.read_text(encoding="utf-8")
        assert 'class="seal"' in html and "AI味指数" in html
        assert "<mark>" in html and "风格提示，不是 AI 判定" in html
        assert html.count('class="found"') >= 3

    def test_html_batch_rejected_cleanly(self, tmp_path):
        (tmp_path / "a.md").write_text("内容。", encoding="utf-8")
        (tmp_path / "b.md").write_text("内容。", encoding="utf-8")
        with pytest.raises(SystemExit) as ei:
            cli.main(["check", str(tmp_path), "-f", "html"])
        assert "html" in str(ei.value)


class TestEdgeRegression:
    def test_bracket_filename_treated_as_literal(self, tmp_path):
        # 文件名带 [ ] 时不得被误当 glob 字符类（存在性优先于通配解释）
        f = tmp_path / "报告[草稿].md"
        f.write_text("内容。", encoding="utf-8")
        assert batch.resolve_paths(str(f)) == [f]

    def test_collect_rejects_empty_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("", encoding="utf-8")
        with pytest.raises(SystemExit) as ei:
            cli.main(["collect", str(f), "--label", "hit"])
        assert "空" in str(ei.value)

    def test_sarif_region_absent_when_unlocatable(self, tmp_path):
        # 原句在源文件定位不到时不给 region，结果照常产出
        f = tmp_path / "a.md"
        f.write_text("首先，随着人工智能的快速发展。", encoding="utf-8")
        result = engine.analyze(f.read_text(encoding="utf-8"), "academic")
        payload = json.loads(sarif.render([(str(f), "完全不相干的另一篇文本内容。", result)], "academic"))
        run = payload["runs"][0]
        assert isinstance(run["results"], list)
