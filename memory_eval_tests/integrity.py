from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from memory_data_service.schemas import DatasetManifest, OraclePayload
from memory_eval_tests.dataset_client import DatasetClient


def audit_dataset_integrity(source: str) -> dict[str, Any]:
    client = DatasetClient(source)
    manifest = DatasetManifest.model_validate(client.manifest())
    oracle = OraclePayload.model_validate(client.oracle())
    issues: list[str] = []

    if manifest.dataset_id != oracle.dataset_id:
        issues.append(
            f"manifest dataset_id {manifest.dataset_id!r} != oracle dataset_id {oracle.dataset_id!r}"
        )

    object_type_counts = Counter(obj.object_type for obj in oracle.objects)
    object_label_counts = Counter(label for obj in oracle.objects for label in obj.labels)
    fact_type_counts = Counter(fact.fact_type for fact in oracle.facts)
    question_type_counts = Counter(question.question_type for question in oracle.questions)
    relation_type_counts = Counter(relation.relation_type for relation in oracle.relations)

    fact_ids = {fact.fact_id for fact in oracle.facts}
    object_ids = {obj.object_id for obj in oracle.objects}
    for question in oracle.questions:
        missing = [fact_id for fact_id in question.evidence_fact_ids if fact_id not in fact_ids]
        if missing:
            issues.append(f"{question.id} references missing facts: {missing}")
        if question.expected_behavior == "abstain" and question.evidence_fact_ids:
            issues.append(f"{question.id} is abstain but has evidence_fact_ids")
        if question.expected_behavior == "answer" and not question.evidence_fact_ids:
            issues.append(f"{question.id} expects an answer but has no evidence_fact_ids")

    for relation in oracle.relations:
        if relation.source_id not in object_ids and relation.source_id not in fact_ids:
            issues.append(f"{relation.relation_id} has missing source_id {relation.source_id}")
        if relation.target_id not in object_ids and relation.target_id not in fact_ids:
            issues.append(f"{relation.relation_id} has missing target_id {relation.target_id}")

    rich_density_checks: dict[str, Any] = {"checked": False, "checks": {}}
    if manifest.profile == "rich":
        object_types = set(object_type_counts)
        labels = set(object_label_counts)
        fact_types = set(fact_type_counts)
        question_types = set(question_type_counts)
        relation_types = set(relation_type_counts)
        required_by_modality = {
            "tables": "table",
            "figures": "figure",
            "equations": "equation",
        }
        for modality, object_type in required_by_modality.items():
            if modality in manifest.modalities and object_type not in object_types:
                issues.append(f"rich dataset declares {modality} but has no {object_type} object")
        required_question_types = {"direct_numeric", "abstain"}
        if manifest.pages >= 5:
            required_question_types.add("multi_hop")
        if manifest.pages >= 7:
            required_question_types.add("version_condition")
        if manifest.pages >= 8 and "figures" in manifest.modalities:
            required_question_types.add("figure_text")
        if manifest.pages >= 9:
            required_question_types.add("conflict_resolution")
        if manifest.pages >= 11:
            required_question_types.add("negative_constraint")
        if "tables" in manifest.modalities:
            required_question_types.add("table_cell")
        if "figures" in manifest.modalities:
            required_question_types.add("figure_caption")
        if "equations" in manifest.modalities:
            required_question_types.add("equation")
            if manifest.pages >= 5:
                required_question_types.add("formula_variable")
        for question_type in sorted(required_question_types):
            if question_type not in question_types:
                issues.append(f"rich dataset has no {question_type} question")
        required_fact_types = {
            "direct_numeric",
        }
        if "tables" in manifest.modalities:
            required_fact_types.add("table_cell")
        if "figures" in manifest.modalities:
            required_fact_types.add("figure_caption")
        if "equations" in manifest.modalities:
            required_fact_types.add("equation")
            if manifest.pages >= 5:
                required_fact_types.add("equation_variable")
        if manifest.pages >= 7:
            required_fact_types.add("version_condition")
        if manifest.pages >= 9:
            required_fact_types.add("conflict_resolution")
        if manifest.pages >= 11:
            required_fact_types.add("negative_constraint")
        for fact_type in sorted(required_fact_types):
            if (
                fact_type == "table_cell"
                and "tables" not in manifest.modalities
                or fact_type == "figure_caption"
                and "figures" not in manifest.modalities
                or fact_type == "equation"
                and "equations" not in manifest.modalities
            ):
                continue
            if fact_type not in fact_types:
                issues.append(f"rich dataset has no {fact_type} fact")
        required_relation_types = {"contains", "supports", "distracts"}
        if "tables" in manifest.modalities or "figures" in manifest.modalities:
            required_relation_types.add("caption_of")
        if manifest.pages >= 6 and "tables" in manifest.modalities:
            required_relation_types.add("refers_to")
        if manifest.pages >= 5 and "equations" in manifest.modalities:
            required_relation_types.update({"defines", "mentions"})
        if manifest.pages >= 9:
            required_relation_types.add("contradicts")
        for relation_type in sorted(required_relation_types):
            if relation_type not in relation_types:
                issues.append(f"rich dataset has no {relation_type} relation")
        required_object_types = {"footnote", "endnote", "layout_region", "textbox"}
        for object_type in sorted(required_object_types):
            if object_type not in object_types:
                issues.append(f"rich dataset has no {object_type} object")
        required_labels = {
            "section_summary",
            "local_conclusion",
            "bullet_control",
            "nested_bullet_control",
            "cross_page_long_table",
            "two_column_layout",
            "textbox",
            "floating_object",
            "citation_field",
            "bibliography_field",
        }
        for label in sorted(required_labels):
            if label not in labels:
                issues.append(f"rich dataset has no {label} object label")
        min_facts = max(manifest.pages, 1)
        min_questions = max(manifest.pages, 1)
        if len(oracle.facts) < min_facts:
            issues.append(
                f"rich dataset has too few facts: {len(oracle.facts)} < {min_facts}"
            )
        if len(oracle.questions) < min_questions:
            issues.append(
                f"rich dataset has too few questions: {len(oracle.questions)} < {min_questions}"
            )
        rich_density_checks = _audit_rich_density(
            manifest=manifest,
            fact_count=len(oracle.facts),
            question_count=len(oracle.questions),
            object_count=len(oracle.objects),
            relation_count=len(oracle.relations),
            object_type_counts=object_type_counts,
            object_label_counts=object_label_counts,
            question_type_counts=question_type_counts,
        )
        issues.extend(rich_density_checks["issues"])

    local_checks = _audit_local_files(source, manifest, oracle)
    issues.extend(local_checks["issues"])
    if manifest.profile == "rich":
        rich_docx_checks = _audit_rich_docx_structure(source, manifest)
        issues.extend(rich_docx_checks["issues"])
    else:
        rich_docx_checks = {"checked": False, "docx_file": "", "features": {}, "issues": []}

    return {
        "source": source,
        "dataset_id": manifest.dataset_id,
        "tier": manifest.tier,
        "profile": manifest.profile,
        "pages": manifest.pages,
        "facts": len(oracle.facts),
        "questions": len(oracle.questions),
        "objects": len(oracle.objects),
        "relations": len(oracle.relations),
        "object_types": sorted({obj.object_type for obj in oracle.objects}),
        "object_type_counts": dict(sorted(object_type_counts.items())),
        "object_label_counts": dict(
            sorted(object_label_counts.items())
        ),
        "fact_types": sorted({fact.fact_type for fact in oracle.facts}),
        "fact_type_counts": dict(sorted(fact_type_counts.items())),
        "question_types": sorted({question.question_type for question in oracle.questions}),
        "question_type_counts": dict(sorted(question_type_counts.items())),
        "relation_types": sorted({relation.relation_type for relation in oracle.relations}),
        "relation_type_counts": dict(sorted(relation_type_counts.items())),
        "rich_density_checks": {
            "checked": rich_density_checks["checked"],
            "checks": rich_density_checks["checks"],
        },
        "rich_docx_structure_checked": rich_docx_checks["checked"],
        "rich_docx_file": rich_docx_checks["docx_file"],
        "rich_docx_features": rich_docx_checks["features"],
        "files": [
            {
                "name": file.name,
                "format": file.format,
                "status": file.status,
                "size_bytes": file.size_bytes,
                "message": file.message,
            }
            for file in manifest.files
        ],
        "local_files_checked": local_checks["checked"],
        "passed": not issues,
        "issues": issues,
    }


def _audit_rich_density(
    *,
    manifest: DatasetManifest,
    fact_count: int,
    question_count: int,
    object_count: int,
    relation_count: int,
    object_type_counts: Counter[str],
    object_label_counts: Counter[str],
    question_type_counts: Counter[str],
) -> dict[str, Any]:
    pages = max(manifest.pages, 1)
    checks: dict[str, dict[str, Any]] = {}
    issues: list[str] = []

    def require(name: str, actual: int, minimum: int) -> None:
        passed = actual >= minimum
        checks[name] = {"actual": actual, "minimum": minimum, "passed": passed}
        if not passed:
            issues.append(f"rich density check failed for {name}: {actual} < {minimum}")

    require("facts", fact_count, pages * 2)
    require("questions", question_count, int(pages * 2.5))
    require("objects", object_count, pages * 12)
    require("relations", relation_count, pages * 16)
    require("direct_numeric_questions", question_type_counts.get("direct_numeric", 0), pages)
    require("multi_hop_questions", question_type_counts.get("multi_hop", 0), max(1, pages // 4))
    require("distractor_labels", object_label_counts.get("distractor", 0), max(1, pages // 2))
    require("section_summary_labels", object_label_counts.get("section_summary", 0), max(1, pages // 2))
    require("local_conclusion_labels", object_label_counts.get("local_conclusion", 0), pages)
    require("footnote_objects", object_type_counts.get("footnote", 0), 1)
    require("endnote_objects", object_type_counts.get("endnote", 0), 1)
    require("layout_region_objects", object_type_counts.get("layout_region", 0), 1)
    require("textbox_objects", object_type_counts.get("textbox", 0), 1)
    require("cross_page_long_table_labels", object_label_counts.get("cross_page_long_table", 0), 1)
    require("floating_object_labels", object_label_counts.get("floating_object", 0), 1)
    require("citation_field_labels", object_label_counts.get("citation_field", 0), 1)
    require("bibliography_field_labels", object_label_counts.get("bibliography_field", 0), 1)
    if "tables" in manifest.modalities:
        require("table_objects", object_type_counts.get("table", 0), max(1, pages // 3))
    if "figures" in manifest.modalities:
        require("figure_objects", object_type_counts.get("figure", 0), max(1, pages // 4))
    if "equations" in manifest.modalities:
        require("equation_objects", object_type_counts.get("equation", 0), max(1, pages // 5))

    return {"checked": True, "checks": checks, "issues": issues}


def _audit_local_files(
    source: str,
    manifest: DatasetManifest,
    oracle: OraclePayload,
) -> dict[str, Any]:
    if source.startswith("http://") or source.startswith("https://"):
        return {"checked": False, "issues": []}

    source_path = Path(source)
    dataset_dir = source_path.parent if source_path.is_file() else source_path
    issues: list[str] = []
    for generated_file in manifest.files:
        if generated_file.status == "skipped":
            continue
        target = dataset_dir / generated_file.name
        if not target.exists():
            issues.append(f"missing generated file: {generated_file.name}")
            continue
        if generated_file.size_bytes and target.stat().st_size != generated_file.size_bytes:
            issues.append(f"size mismatch for {generated_file.name}")

    facts_path = dataset_dir / manifest.facts_file
    questions_path = dataset_dir / manifest.questions_file
    objects_path = dataset_dir / manifest.objects_file
    relations_path = dataset_dir / manifest.relations_file
    if facts_path.exists():
        facts_payload = json.loads(facts_path.read_text(encoding="utf-8"))
        if facts_payload.get("facts") != [fact.model_dump() for fact in oracle.facts]:
            issues.append(f"{manifest.facts_file} does not match oracle facts")
    else:
        issues.append(f"missing {manifest.facts_file}")

    if questions_path.exists():
        questions_payload = json.loads(questions_path.read_text(encoding="utf-8"))
        if questions_payload.get("questions") != [
            question.model_dump() for question in oracle.questions
        ]:
            issues.append(f"{manifest.questions_file} does not match oracle questions")
    else:
        issues.append(f"missing {manifest.questions_file}")

    if objects_path.exists():
        objects_payload = json.loads(objects_path.read_text(encoding="utf-8"))
        if objects_payload.get("objects") != [obj.model_dump() for obj in oracle.objects]:
            issues.append(f"{manifest.objects_file} does not match oracle objects")
    else:
        issues.append(f"missing {manifest.objects_file}")

    if relations_path.exists():
        relations_payload = json.loads(relations_path.read_text(encoding="utf-8"))
        if relations_payload.get("relations") != [
            relation.model_dump() for relation in oracle.relations
        ]:
            issues.append(f"{manifest.relations_file} does not match oracle relations")
    else:
        issues.append(f"missing {manifest.relations_file}")

    return {"checked": True, "issues": issues}


def _audit_rich_docx_structure(
    source: str,
    manifest: DatasetManifest,
) -> dict[str, Any]:
    if source.startswith("http://") or source.startswith("https://"):
        return {"checked": False, "docx_file": "", "issues": []}
    source_path = Path(source)
    dataset_dir = source_path.parent if source_path.is_file() else source_path
    docx_file = next(
        (
            item.name
            for item in manifest.files
            if item.format == "docx" and item.status == "created"
        ),
        "",
    )
    if not docx_file:
        return {"checked": False, "docx_file": "", "issues": ["rich dataset has no created DOCX"]}
    docx_path = dataset_dir / docx_file
    issues: list[str] = []
    with zipfile.ZipFile(docx_path) as package:
        names = set(package.namelist())
        has_footnotes_part = "word/footnotes.xml" in names
        has_endnotes_part = "word/endnotes.xml" in names
        if not has_footnotes_part:
            issues.append("rich DOCX has no word/footnotes.xml")
        if not has_endnotes_part:
            issues.append("rich DOCX has no word/endnotes.xml")
        document_xml = package.read("word/document.xml").decode("utf-8")
        rels_xml = package.read("word/_rels/document.xml.rels").decode("utf-8")
        has_footnote_reference = "footnoteReference" in document_xml
        has_endnote_reference = "endnoteReference" in document_xml
        has_ref_cross_reference_field = " REF " in document_xml
        has_citation_field = " CITATION " in document_xml
        has_bibliography_field = " BIBLIOGRAPHY " in document_xml
        has_two_column_section = 'w:num="2"' in document_xml
        has_textbox = "v:textbox" in document_xml or "w:txbxContent" in document_xml
        settings_xml = (
            package.read("word/settings.xml").decode("utf-8")
            if "word/settings.xml" in names
            else ""
        )
        has_update_fields_setting = "w:updateFields" in settings_xml
        has_footnotes_relationship = "footnotes" in rels_xml
        has_endnotes_relationship = "endnotes" in rels_xml
        if not has_footnote_reference:
            issues.append("rich DOCX has no footnoteReference")
        if not has_endnote_reference:
            issues.append("rich DOCX has no endnoteReference")
        if not has_ref_cross_reference_field:
            issues.append("rich DOCX has no REF cross-reference field")
        if not has_citation_field:
            issues.append("rich DOCX has no CITATION field")
        if not has_bibliography_field:
            issues.append("rich DOCX has no BIBLIOGRAPHY field")
        if not has_two_column_section:
            issues.append("rich DOCX has no two-column section")
        if not has_textbox:
            issues.append("rich DOCX has no textbox content")
        if not has_update_fields_setting:
            issues.append("rich DOCX has no updateFields setting")
        if not has_footnotes_relationship:
            issues.append("rich DOCX has no footnotes relationship")
        if not has_endnotes_relationship:
            issues.append("rich DOCX has no endnotes relationship")
    return {
        "checked": True,
        "docx_file": docx_file,
        "features": {
            "footnotes_part": has_footnotes_part,
            "endnotes_part": has_endnotes_part,
            "footnote_reference": has_footnote_reference,
            "endnote_reference": has_endnote_reference,
            "ref_cross_reference_field": has_ref_cross_reference_field,
            "citation_field": has_citation_field,
            "bibliography_field": has_bibliography_field,
            "two_column_section": has_two_column_section,
            "textbox": has_textbox,
            "update_fields_setting": has_update_fields_setting,
            "footnotes_relationship": has_footnotes_relationship,
            "endnotes_relationship": has_endnotes_relationship,
        },
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a memory evaluation dataset.")
    parser.add_argument("source", help="Dataset directory, manifest path, or dataset HTTP URL.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = audit_dataset_integrity(args.source)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Dataset: {report['dataset_id']}")
        print(f"Facts: {report['facts']} Questions: {report['questions']}")
        print(f"Passed: {report['passed']}")
        if report["issues"]:
            print(json.dumps(report["issues"], ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
