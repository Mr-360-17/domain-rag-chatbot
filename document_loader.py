"""Modules 1-3: validate PDFs, extract text page by page, split pages into chunks."""
from dataclasses import asdict, dataclass
from io import BytesIO

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

MAX_FILE_SIZE_MB = 10
MAX_FILES = 5
CHUNK_SIZE = 900        # characters (brief suggests 700-1000)
CHUNK_OVERLAP = 150     # characters (brief suggests 100-150)


class DocumentError(ValueError):
    """Raised when an uploaded file cannot be accepted or read."""


@dataclass
class Chunk:
    text: str
    source: str     # file name
    page: int       # 1-based page number
    chunk_id: int

    def to_dict(self) -> dict:
        return asdict(self)


def validate_file(filename: str, size_bytes: int) -> None:
    if not filename.lower().endswith(".pdf"):
        raise DocumentError(f"{filename}: only PDF files are supported.")
    if size_bytes == 0:
        raise DocumentError(f"{filename}: the file is empty.")
    if size_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise DocumentError(f"{filename}: larger than {MAX_FILE_SIZE_MB} MB.")


def extract_pages(filename: str, data: bytes) -> list[dict]:
    """Return [{"source", "page", "text"}] for every page that has text."""
    validate_file(filename, len(data))
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:
        raise DocumentError(f"{filename}: could not open the PDF ({exc}).") from exc
    if reader.is_encrypted:
        raise DocumentError(f"{filename}: the PDF is password protected.")
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        text = " ".join((page.extract_text() or "").split())
        if text:  # skip empty or image-only pages safely
            pages.append({"source": filename, "page": number, "text": text})
    if not pages:
        raise DocumentError(f"{filename}: no text found. It may be a scanned PDF (OCR is not supported).")
    return pages


def chunk_pages(pages: list[dict], start_id: int = 0) -> list[Chunk]:
    """Split each page separately, so every chunk keeps an exact page number."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks, next_id = [], start_id
    for page in pages:
        for piece in splitter.split_text(page["text"]):
            chunks.append(Chunk(piece, page["source"], page["page"], next_id))
            next_id += 1
    return chunks


def load_documents(files: list[tuple[str, bytes]]) -> tuple[list[Chunk], list[str]]:
    """Load several PDFs. Returns the chunks plus a list of per-file warnings."""
    if not files:
        raise DocumentError("Upload at least one PDF.")
    if len(files) > MAX_FILES:
        raise DocumentError(f"Upload at most {MAX_FILES} PDFs at a time.")
    chunks, warnings = [], []
    for filename, data in files:
        try:
            chunks.extend(chunk_pages(extract_pages(filename, data), start_id=len(chunks)))
        except DocumentError as err:
            warnings.append(str(err))
    if not chunks:
        raise DocumentError("None of the files could be read. " + " ".join(warnings))
    return chunks, warnings
