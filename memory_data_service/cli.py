from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from memory_data_service.generators import generate_dataset
from memory_data_service.schemas import DatasetCreateRequest
from memory_data_service.storage import DEFAULT_GENERATED_ROOT, list_datasets


app = typer.Typer(help="Synthetic rich-document data service for LightRAG memory evaluation.")
console = Console()


@app.command()
def generate(
    tier: Annotated[str, typer.Option(help="smoke, medium, large, or stress")] = "smoke",
    profile: Annotated[str, typer.Option(help="basic or rich")] = "rich",
    formats: Annotated[str, typer.Option(help="Comma-separated formats: docx,pdf")] = "docx",
    modalities: Annotated[
        str, typer.Option(help="Comma-separated modalities: text,tables,figures,equations")
    ] = "text,tables,figures,equations",
    pages: Annotated[int | None, typer.Option(help="Override tier page count")] = None,
    dataset_id: Annotated[str | None, typer.Option(help="Optional stable dataset id")] = None,
    allow_oversized_generation: Annotated[
        bool,
        typer.Option(
            help="Allow generation above the default 3000-page guard. Use only for explicit stress experiments."
        ),
    ] = False,
    output_root: Annotated[
        Path, typer.Option(help="Generated dataset root")
    ] = DEFAULT_GENERATED_ROOT,
) -> None:
    request = DatasetCreateRequest(
        tier=tier,  # type: ignore[arg-type]
        profile=profile,  # type: ignore[arg-type]
        pages=pages,
        formats=[f.strip() for f in formats.split(",") if f.strip()],  # type: ignore[list-item]
        modalities=[m.strip() for m in modalities.split(",") if m.strip()],  # type: ignore[list-item]
        dataset_id=dataset_id,
        allow_oversized_generation=allow_oversized_generation,
    )
    try:
        manifest = generate_dataset(request, root=output_root)
    except ValueError as exc:
        console.print(f"[red]Generation refused:[/red] {exc}")
        raise typer.Exit(2) from exc
    console.print(f"[green]Generated dataset[/green] {manifest.dataset_id}")
    console.print(str(output_root / manifest.dataset_id))


@app.command("list")
def list_command(
    output_root: Annotated[
        Path, typer.Option(help="Generated dataset root")
    ] = DEFAULT_GENERATED_ROOT,
) -> None:
    table = Table(title="Generated datasets")
    table.add_column("dataset_id")
    table.add_column("tier")
    table.add_column("profile")
    table.add_column("pages", justify="right")
    table.add_column("files")
    table.add_column("path")
    for summary in list_datasets(output_root):
        table.add_row(
            summary.dataset_id,
            summary.tier,
            summary.profile,
            str(summary.pages),
            ", ".join(summary.files),
            summary.path,
        )
    console.print(table)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port")] = 9731,
) -> None:
    import uvicorn

    uvicorn.run("memory_data_service.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
