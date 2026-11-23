"""Read a small supported CV in memory. Only matched catalogue IDs reach storage."""
import io
import json
import os
import re
import zipfile
from functools import lru_cache
from pathlib import Path

from docx import Document
from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import text as sql


def readable_text(filename, data):
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise HTTPException(415, "Sube un CV en PDF, DOCX o TXT.")
    try:
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 30:
                raise ValueError()
            content = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > 16_000_000:
                    raise ValueError()
            doc = Document(io.BytesIO(data))
            lines = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                lines.extend(cell.text for row in table.rows for cell in row.cells)
            content = "\n".join(lines)
        else:
            content = data.decode("utf-8-sig")
        if not content.strip() or "\x00" in content:
            raise ValueError()
    except Exception as error:
        raise HTTPException(422, "El CV no tiene texto legible. Usa un documento con texto seleccionable, sin contraseña y de hasta 30 páginas si es PDF.") from error
    return content[:100_000]


@lru_cache(maxsize=1)
def model_for(path):
    import torch
    from transformers import AutoTokenizer, AutoModel
    if not Path(path).is_dir():
        raise ValueError("No se encontró la carpeta local de BETO.")
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    model = AutoModel.from_pretrained(path, local_files_only=True, trust_remote_code=False, use_safetensors=True).eval()
    if model.config.model_type != "bert" or model.config.hidden_size != 768:
        raise ValueError("Se esperaba BETO base en español, con dimensión 768.")
    return torch, tokenizer, model


def embeddings(texts, path):
    torch, tokenizer, model = model_for(path)
    result = []
    with torch.inference_mode():
        for start in range(0, len(texts), 16):
            batch = tokenizer(texts[start:start + 16], padding=True, truncation=True, max_length=64, return_tensors="pt")
            hidden = model(**batch).last_hidden_state
            mask = batch["attention_mask"].unsqueeze(-1)
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            result.extend(torch.nn.functional.normalize(pooled, dim=1).tolist())
    return result


def best_matches(candidates, catalogue, db):
    # Vectors are normalized. The SQLite path gives the same cosine distance in memory.
    if db.bind.dialect.name == "postgresql":
        statement = sql("""
          WITH phrases AS (SELECT v::text::vector AS emb FROM jsonb_array_elements(CAST(:phrases AS jsonb)) AS t(v)),
          skills AS (SELECT (item->>'id')::int AS id, (item->'vector')::text::vector AS emb
                     FROM jsonb_array_elements(CAST(:skills AS jsonb)) AS t(item))
          SELECT skills.id, MAX(1 - (skills.emb <=> phrases.emb)) FROM skills CROSS JOIN phrases GROUP BY skills.id
        """)
        return dict(db.execute(statement, {"phrases": json.dumps(candidates), "skills": json.dumps(catalogue)}).all())
    return {entry["id"]: max(sum(a * b for a, b in zip(entry["vector"], other)) for other in candidates)
            for entry in catalogue}


def extract_competencies(content, competencies, db, *, threshold=.92):
    normalized = content.casefold()
    found = {}
    for skill in competencies:
        terms = [skill.name, *(skill.aliases or [])]
        if any(re.search(rf"(?<!\w){re.escape(term.casefold())}(?!\w)", normalized) for term in terms if term):
            found[skill.id] = {"competency_id": skill.id, "name": skill.name, "source": "Diccionario", "confirmed": False}
    path = os.getenv("BETO_MODEL_PATH")
    remaining = [skill for skill in competencies if skill.id not in found]
    if path and remaining:
        # Short clauses provide local semantic context, and are never sent to a remote model.
        phrases = [part.strip()[:240] for part in re.split(r"[.;\n•]+", content) if len(part.strip()) >= 3][:64]
        if phrases:
            try:
                if not 0 <= threshold <= 1:
                    raise ValueError("Umbral de coseno fuera de rango")
                refs = embeddings([", ".join([s.name, *(s.aliases or [])]) for s in remaining], path)
                vectors = [{"id": s.id, "vector": vector} for s, vector in zip(remaining, refs)]
                similarities = best_matches(embeddings(phrases, path), vectors, db)
            except Exception as error:
                import logging
                logging.getLogger(__name__).warning("Semantic extraction unavailable (%s)", type(error).__name__)
                raise HTTPException(503, "El extractor semántico configurado no está disponible. Puedes continuar con la autodeclaración.") from error
            for skill in remaining:
                similarity = similarities.get(skill.id, -1)
                if similarity >= threshold:
                    found[skill.id] = {"competency_id": skill.id, "name": skill.name, "source": "BETO",
                                       "similarity": round(similarity, 4), "confirmed": False}
    return list(found.values())
