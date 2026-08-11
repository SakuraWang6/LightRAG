"""Deterministic Simplified-Chinese document generator for memory evaluation."""

from __future__ import annotations

import hashlib
import random
import time
from pathlib import Path

from memory_data_service.cross_document import add_cross_document_case
from memory_data_service.generators.docx_generator import (
    _convert_pdf,
    _file_record,
    _skipped,
)
from memory_data_service.provenance import (
    annotate_question_scenarios,
    build_provenance,
    resolve_scenario_quotas,
)
from memory_data_service.resource_guard import (
    GenerationResourceMonitor,
    enforce_generation_limits,
    estimate_generation_resources,
)
from memory_data_service.schemas import (
    DatasetCreateRequest,
    DatasetManifest,
    DocumentObject,
    FactRecord,
    GeneratedFile,
    ObjectRelation,
    OraclePayload,
    QuestionRecord,
)
from memory_data_service.storage import DEFAULT_GENERATED_ROOT, ensure_root, write_json

_DEFAULT_ENGLISH_TITLE = "LightRAG Synthetic Rich Memory Document"
_DEFAULT_CHINESE_TITLE = "LightRAG 合成中文记忆测评文档"
_WORKSTREAMS = (
    (
        "业务目标与服务范围",
        "本工作流负责把高频售后咨询转为可追溯的知识服务；范围内问题可自动答复，涉及退款、隐私或高风险承诺的问题必须转人工。",
    ),
    (
        "数据治理与质量控制",
        "知识来源须具备责任人、有效期和审批记录。新增资料先经过重复检测与敏感字段检查，再进入待发布版本。",
    ),
    (
        "发布节奏与变更管理",
        "每个发布批次先在灰度环境核验召回、引用和拒答表现；未满足门槛的批次只能修订，不能越过评审直接上线。",
    ),
    (
        "运行监控与事件响应",
        "运行期间同时监控答案依据、人工转接率和版本漂移；事件关闭必须记录根因、补救措施和后续验证窗口。",
    ),
)


def generate_chinese_dataset(
    request: DatasetCreateRequest,
    *,
    root: Path = DEFAULT_GENERATED_ROOT,
) -> DatasetManifest:
    """Generate a Chinese corpus while preserving the regular dataset contract."""
    started = time.perf_counter()
    root = ensure_root(root)
    pages = request.resolved_pages()
    enforce_generation_limits(request, pages)
    resource_estimate = estimate_generation_resources(request, pages)
    dataset_id = request.dataset_id or _stable_dataset_id(request, pages)
    dataset_path = root / dataset_id
    dataset_path.mkdir(parents=True, exist_ok=True)
    effective_request = request.model_copy(
        update={
            "title": (
                _DEFAULT_CHINESE_TITLE
                if request.title == _DEFAULT_ENGLISH_TITLE
                else request.title
            )
        }
    )
    docx_path = dataset_path / f"{dataset_id}.docx"

    with GenerationResourceMonitor() as resource_monitor:
        facts, questions, objects, relations = _write_docx(
            effective_request, docx_path, pages
        )
        companion_docx = (
            add_cross_document_case(
                dataset_id=dataset_id,
                dataset_path=dataset_path,
                facts=facts,
                questions=questions,
                language="zh",
            )
            if "docx" in effective_request.formats
            else None
        )
        scenario_counts = annotate_question_scenarios(questions)
        scenario_quotas = resolve_scenario_quotas(
            requested=effective_request.scenario_quotas, observed=scenario_counts
        )
        pdf_record = (
            _convert_pdf(docx_path, dataset_path)
            if "pdf" in effective_request.formats
            else _skipped("pdf")
        )
        oracle = OraclePayload(
            dataset_id=dataset_id,
            language="zh",
            facts=facts,
            questions=questions,
            objects=objects,
            relations=relations,
        )
        _write_artifacts(
            dataset_path=dataset_path,
            dataset_id=dataset_id,
            facts=facts,
            questions=questions,
            objects=objects,
            relations=relations,
            oracle=oracle,
        )

    files: list[GeneratedFile] = [
        _file_record(docx_path, "docx", role="source_document")
        if "docx" in effective_request.formats
        else _skipped("docx")
    ]
    files.append(pdf_record)
    if companion_docx is not None:
        files.append(_file_record(companion_docx, "docx", role="source_document"))
    for name in ("facts.json", "questions.json", "objects.json", "relations.json", "oracle.json"):
        files.append(_file_record(dataset_path / name, "json"))
    for asset_path in sorted(dataset_path.glob("*.png")):
        files.append(_file_record(asset_path, "png"))

    fingerprint, provenance = build_provenance(
        request=effective_request,
        pages=pages,
        generator="chinese_docx_generator",
        template_version=f"zh-{effective_request.profile}-docx-v2",
        source_file=Path(__file__),
    )
    manifest = DatasetManifest(
        dataset_id=dataset_id,
        tier=effective_request.tier,
        pages=pages,
        profile=effective_request.profile,
        language="zh",
        formats=effective_request.formats,
        modalities=effective_request.modalities,
        title=effective_request.title,
        display_name=effective_request.display_name,
        split=effective_request.split,
        scenario_quotas=scenario_quotas,
        scenario_counts=scenario_counts,
        dataset_fingerprint=fingerprint,
        generation_provenance=provenance,
        files=files,
        generation_time_seconds=round(time.perf_counter() - started, 3),
        generation_peak_memory_mb=resource_monitor.peak_memory_mb,
        generation_resource_estimate=resource_estimate,
    )
    write_json(dataset_path / "manifest.json", manifest)
    return manifest


def _write_docx(
    request: DatasetCreateRequest, docx_path: Path, pages: int
) -> tuple[
    list[FactRecord], list[QuestionRecord], list[DocumentObject], list[ObjectRelation]
]:
    try:
        from docx import Document
        from docx.shared import Inches
    except ImportError as exc:
        raise RuntimeError("python-docx is required to generate DOCX datasets") from exc

    rng = random.Random(request.seed)
    document = Document()
    document.core_properties.title = request.title
    document.core_properties.subject = "面向 LightRAG 的中文文档记忆测评"
    document.core_properties.keywords = "LightRAG, 中文, 测评, 检索, 标准答案"
    document.add_heading(request.title, 0)
    document.add_paragraph(
        "本文档为 LightRAG 中文文档记忆测评自动生成。文中包含可验证的事实、"
        "干扰信息、表格、图示、公式与跨文档证据，所有题目的标准答案均保存在数据集 oracle 中。"
    )

    image_path = docx_path.parent / "中文流程图.png"
    if "figures" in request.modalities:
        _write_diagram(image_path)

    facts: list[FactRecord] = []
    questions: list[QuestionRecord] = []
    objects: list[DocumentObject] = []
    relations: list[ObjectRelation] = []
    object_counter = 0
    relation_counter = 0

    def add_object(
        object_type: str,
        *,
        title: str,
        text: str,
        section: str,
        page: int,
        parent_id: str = "",
        labels: list[str] | None = None,
    ) -> str:
        nonlocal object_counter
        object_counter += 1
        object_id = f"OBJ-{object_counter:05d}"
        if request.profile == "rich":
            objects.append(
                DocumentObject(
                    object_id=object_id,
                    object_type=object_type,  # type: ignore[arg-type]
                    title=title,
                    text=text,
                    section=section,
                    page_start=page,
                    parent_id=parent_id,
                    labels=labels or [],
                )
            )
        return object_id

    def add_relation(source_id: str, target_id: str, relation_type: str, evidence: str = "") -> None:
        nonlocal relation_counter
        if request.profile != "rich":
            return
        relation_counter += 1
        relations.append(
            ObjectRelation(
                relation_id=f"REL-{relation_counter:05d}",
                source_id=source_id,
                target_id=target_id,
                relation_type=relation_type,  # type: ignore[arg-type]
                evidence_text=evidence,
            )
        )

    root_id = add_object(
        "document",
        title=request.title,
        text="中文合成测评文档及其证据对象图。",
        section="文档",
        page=1,
        labels=["document", "zh"],
    )
    fact_counter = 1
    previous_text_fact = ""
    previous_table_fact = ""
    last_delivery_fact = ""
    last_risk_fact = ""

    if request.profile == "rich":
        document.add_heading("项目背景与治理边界", level=1)
        context_text = (
            "“星桥客户服务知识升级”项目在 2026 年第三季度推进。项目负责人林岚负责业务验收，"
            "数据治理负责人周衡负责来源准入，安全负责人顾澄负责涉及隐私与退款场景的上线签核。"
            "任何批次只有同时满足质量门槛、责任人签核和回滚预案完整三个条件，才能进入生产环境。"
        )
        document.add_paragraph(context_text)
        context_id = add_object(
            "paragraph",
            title="项目背景",
            text=context_text,
            section="项目背景与治理边界",
            page=1,
            parent_id=root_id,
            labels=["program_context", "governance"],
        )
        add_relation(root_id, context_id, "contains", context_text)
        context_fact_id = f"FACT-{fact_counter:05d}"
        fact_counter += 1
        facts.append(
            FactRecord(
                fact_id=context_fact_id,
                fact_type="governance_owner",
                answer="林岚、周衡和顾澄分别负责业务验收、来源准入与安全签核",
                expected_text=context_text,
                section="项目背景与治理边界",
                page=1,
                object_type="text",
                object_id_hint=context_id,
            )
        )
        questions.append(
            QuestionRecord(
                id=f"Q-{context_fact_id}",
                question="星桥项目中，业务验收、来源准入和安全签核分别由谁负责？",
                answer="林岚、周衡和顾澄分别负责业务验收、来源准入与安全签核",
                question_type="multi_hop",
                evidence_fact_ids=[context_fact_id],
            )
        )

    for page in range(1, pages + 1):
        chapter = (page - 1) // 10 + 1
        unit = (page - 1) // 2 + 1
        workstream_title, workstream_narrative = _WORKSTREAMS[(page - 1) % len(_WORKSTREAMS)]
        chapter_title = f"第 {chapter} 章：星桥项目——{workstream_title}"
        section_title = f"第 {chapter}.{unit} 节：实施单元 {unit:04d}"
        section = f"{chapter_title} / {section_title} / 第 {page} 页"
        if page == 1 or (page - 1) % 10 == 0:
            document.add_heading(chapter_title, level=1)
        if page == 1 or (page - 1) % 2 == 0:
            document.add_heading(section_title, level=2)
        section_id = add_object(
            "section",
            title=section_title,
            text=f"本节记录{workstream_title}的目标、证据与决策约束。",
            section=section,
            page=page,
            parent_id=root_id,
            labels=["section"],
        )
        add_relation(root_id, section_id, "contains")

        fact_id = f"FACT-{fact_counter:05d}"
        fact_counter += 1
        value = 1000 + page * 7 + rng.randint(0, 6)
        answer = f"{value} QMU"
        text = (
            f"{fact_id}：第 {page} 页检索单元的标准标定上限为 {answer}。"
            f"相邻的 {value + 11} QMU 和 {value - 9} QMU 为已废弃的干扰值，不得使用。"
        )
        document.add_heading(f"第 {page} 页规格说明", level=3)
        document.add_paragraph(text)
        paragraph_id = add_object(
            "paragraph",
            title=fact_id,
            text=text,
            section=section,
            page=page,
            parent_id=section_id,
            labels=["gold_fact"],
        )
        add_relation(section_id, paragraph_id, "contains", text)
        facts.append(
            FactRecord(
                fact_id=fact_id,
                fact_type="direct_numeric",
                answer=answer,
                expected_text=text,
                section=section,
                page=page,
                object_type="text",
                object_id_hint=paragraph_id if request.profile == "rich" else "",
            )
        )
        questions.append(
            QuestionRecord(
                id=f"Q-{fact_id}",
                question=f"第 {page} 页检索单元的标准标定上限是多少？",
                answer=answer,
                question_type="direct_numeric",
                evidence_fact_ids=[fact_id],
            )
        )
        previous_text_fact = fact_id
        document.add_paragraph(
            f"{workstream_narrative} 本页将该要求落实到实施单元 {unit:04d}："
            "记录不仅要说明结果，还要能够追溯到责任人、前置条件和版本边界。"
        )
        if request.profile == "rich":
            document.add_paragraph(
                "执行控制：先核验标准来源，再记录参数变更；任何不带事实编号的数值均不能作为最终结论。"
                "当业务目标与风险控制冲突时，以安全签核和可回滚性为准，而不是以交付日期为唯一依据。"
            )

            if page % 4 == 1:
                delivery_fact_id = f"FACT-{fact_counter:05d}"
                fact_counter += 1
                milestone = f"M-{chapter}{unit:02d}"
                delivery_text = (
                    f"{delivery_fact_id}：实施单元 {unit:04d} 的交付里程碑为 {milestone}，"
                    f"由林岚在第 {page + 2} 页对应的业务验收窗口确认。未完成验收的资料仅可停留在灰度环境。"
                )
                document.add_paragraph(delivery_text)
                delivery_id = add_object(
                    "paragraph",
                    title=delivery_fact_id,
                    text=delivery_text,
                    section=section,
                    page=page,
                    parent_id=section_id,
                    labels=["milestone", "gold_fact"],
                )
                add_relation(section_id, delivery_id, "contains", delivery_text)
                facts.append(
                    FactRecord(
                        fact_id=delivery_fact_id,
                        fact_type="delivery_milestone",
                        answer=milestone,
                        expected_text=delivery_text,
                        section=section,
                        page=page,
                        object_type="text",
                        object_id_hint=delivery_id,
                    )
                )
                last_delivery_fact = delivery_fact_id
            elif page % 4 == 3:
                risk_fact_id = f"FACT-{fact_counter:05d}"
                fact_counter += 1
                control = "周衡完成来源复核且顾澄完成安全签核"
                risk_text = (
                    f"{risk_fact_id}：若实施单元 {unit:04d} 涉及退款或隐私信息，"
                    f"即使已达到业务里程碑，也必须在发布前满足“{control}”这一双重控制条件。"
                )
                document.add_paragraph(risk_text)
                risk_id = add_object(
                    "paragraph",
                    title=risk_fact_id,
                    text=risk_text,
                    section=section,
                    page=page,
                    parent_id=section_id,
                    labels=["risk_control", "gold_fact"],
                )
                add_relation(section_id, risk_id, "contains", risk_text)
                facts.append(
                    FactRecord(
                        fact_id=risk_fact_id,
                        fact_type="release_constraint",
                        answer=control,
                        expected_text=risk_text,
                        section=section,
                        page=page,
                        object_type="text",
                        object_id_hint=risk_id,
                    )
                )
                last_risk_fact = risk_fact_id
            elif page % 4 == 0 and last_delivery_fact and last_risk_fact:
                questions.append(
                    QuestionRecord(
                        id=f"Q-RELEASE-GATE-{page:04d}",
                        question="依据最近的交付里程碑和风险控制要求，涉及退款或隐私信息的单元在满足里程碑后还需要什么才能上线？",
                        answer="周衡完成来源复核且顾澄完成安全签核",
                        question_type="multi_hop",
                        evidence_fact_ids=[last_delivery_fact, last_risk_fact],
                    )
                )

        if "tables" in request.modalities and page % 3 == 0:
            table_fact_id = f"FACT-{fact_counter:05d}"
            fact_counter += 1
            table_answer = f"{page * 3}.5 ms"
            caption = f"表 {page}：检索单元的时延阈值"
            document.add_paragraph(caption)
            table = document.add_table(rows=4, cols=3)
            table.style = "Table Grid"
            rows = [
                ("参数", "标称值", "最大值"),
                ("延迟 Alpha", f"{page}.0 ms", table_answer),
                ("延迟 Beta", f"{page + 2}.0 ms", f"{page * 4}.5 ms"),
                (table_fact_id, "标准行标记", table_answer),
            ]
            for row_index, row_values in enumerate(rows):
                for column_index, value_text in enumerate(row_values):
                    table.rows[row_index].cells[column_index].text = value_text
            table_id = add_object(
                "table",
                title=caption,
                text=f"{table_fact_id} 标准行标记 {table_answer}",
                section=section,
                page=page,
                parent_id=section_id,
                labels=["table", "gold_fact"],
            )
            add_relation(section_id, table_id, "contains")
            facts.append(
                FactRecord(
                    fact_id=table_fact_id,
                    fact_type="table_cell",
                    answer=table_answer,
                    expected_text=f"{table_fact_id} 标准行标记 {table_answer}",
                    section=section,
                    page=page,
                    object_type="table",
                    object_id_hint=table_id if request.profile == "rich" else caption,
                )
            )
            questions.append(
                QuestionRecord(
                    id=f"Q-{table_fact_id}",
                    question=f"在表 {page} 中，标准行标记对应的最大值是多少？",
                    answer=table_answer,
                    question_type="table_cell",
                    evidence_fact_ids=[table_fact_id],
                )
            )
            previous_table_fact = table_fact_id

        if "figures" in request.modalities and page % 4 == 0:
            figure_fact_id = f"FACT-{fact_counter:05d}"
            fact_counter += 1
            document.add_picture(str(image_path), width=Inches(3.8))
            caption = f"图 {page}：{figure_fact_id} 表示第 {page} 页已核验的控制流状态。"
            document.add_paragraph(caption)
            figure_id = add_object(
                "figure",
                title=f"图 {page}",
                text=caption,
                section=section,
                page=page,
                parent_id=section_id,
                labels=["figure", "caption"],
            )
            add_relation(section_id, figure_id, "contains", caption)
            facts.append(
                FactRecord(
                    fact_id=figure_fact_id,
                    fact_type="figure_caption",
                    answer=f"第 {page} 页已核验的控制流状态",
                    expected_text=caption,
                    section=section,
                    page=page,
                    object_type="figure",
                    object_id_hint=figure_id if request.profile == "rich" else f"图 {page}",
                )
            )
            questions.append(
                QuestionRecord(
                    id=f"Q-{figure_fact_id}",
                    question=f"图 {page} 表示的是什么状态？",
                    answer=f"第 {page} 页已核验的控制流状态",
                    question_type="figure_caption",
                    evidence_fact_ids=[figure_fact_id],
                )
            )

        if "equations" in request.modalities and page % 5 == 0:
            equation_fact_id = f"FACT-{fact_counter:05d}"
            fact_counter += 1
            equation = f"E_{page} = P_{page} × T_{page} / η"
            equation_text = f"{equation_fact_id}：第 {page} 页能量指标由公式 {equation} 定义。"
            document.add_paragraph(equation_text)
            equation_id = add_object(
                "equation",
                title=f"公式 {page}",
                text=equation_text,
                section=section,
                page=page,
                parent_id=section_id,
                labels=["equation", "gold_fact"],
            )
            add_relation(section_id, equation_id, "contains", equation_text)
            facts.append(
                FactRecord(
                    fact_id=equation_fact_id,
                    fact_type="equation",
                    answer=equation,
                    expected_text=equation_text,
                    section=section,
                    page=page,
                    object_type="equation",
                    object_id_hint=equation_id if request.profile == "rich" else f"公式 {page}",
                )
            )
            questions.append(
                QuestionRecord(
                    id=f"Q-{equation_fact_id}",
                    question=f"第 {page} 页的能量指标由什么公式定义？",
                    answer=equation,
                    question_type="equation",
                    evidence_fact_ids=[equation_fact_id],
                )
            )
            if previous_table_fact and previous_text_fact:
                questions.append(
                    QuestionRecord(
                        id=f"Q-MULTIHOP-{page:04d}",
                        question="结合本页公式和最近的标准表格行，应引用哪两个事实编号？",
                        answer=f"{previous_table_fact}；{equation_fact_id}",
                        question_type="multi_hop",
                        evidence_fact_ids=[previous_table_fact, equation_fact_id],
                    )
                )

        if page < pages:
            document.add_page_break()

    questions.append(
        QuestionRecord(
            id="Q-ABSTAIN-00001",
            question="文档中不存在的锆石旁路模块的审批编号是什么？",
            answer="文档未提供此信息。",
            question_type="abstain",
            evidence_fact_ids=[],
            expected_behavior="abstain",
        )
    )
    document.save(docx_path)
    return facts, questions, objects, relations


def _write_artifacts(
    *,
    dataset_path: Path,
    dataset_id: str,
    facts: list[FactRecord],
    questions: list[QuestionRecord],
    objects: list[DocumentObject],
    relations: list[ObjectRelation],
    oracle: OraclePayload,
) -> None:
    common = {"dataset_id": dataset_id, "language": "zh"}
    write_json(dataset_path / "facts.json", {**common, "facts": facts})
    write_json(dataset_path / "questions.json", {**common, "questions": questions})
    write_json(dataset_path / "objects.json", {**common, "objects": objects})
    write_json(dataset_path / "relations.json", {**common, "relations": relations})
    write_json(dataset_path / "oracle.json", oracle)


def _stable_dataset_id(request: DatasetCreateRequest, pages: int) -> str:
    raw = (
        f"zh:{request.profile}:{request.tier}:{pages}:{request.formats}:"
        f"{request.modalities}:{request.seed}:{request.split}:"
        f"{sorted(request.scenario_quotas.items())}"
    )
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]
    return f"zh-{request.profile}-{request.tier}-{pages}p-{digest}"


def _write_diagram(path: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Pillow is required to generate figure assets") from exc
    image = Image.new("RGB", (900, 420), color=(245, 247, 250))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 100, 330, 300), radius=18, outline=(27, 99, 157), width=6)
    draw.rounded_rectangle((570, 100, 830, 300), radius=18, outline=(35, 130, 91), width=6)
    draw.line((335, 200, 565, 200), fill=(80, 80, 80), width=5)
    draw.polygon([(545, 182), (575, 200), (545, 218)], fill=(80, 80, 80))
    image.save(path)
